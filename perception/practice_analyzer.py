"""Practice-edition AI Visibility analysis pipeline.

Implements analyze_practice() — a parallel pipeline to analyze_location() for
physician practices and specialty clinics.  The hospital path in analyzer.py is
not modified.  This module imports private helpers from analyzer.py (underscore
prefix is a convention; Python does not enforce it).
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import anthropic

from . import scoring
from .config import settings
from .data import places
from .db import get_connection, init_db
from .models import (
    AffiliationType,
    AnalysisResult,
    ConsolidatedLocation,
    GoogleFootprint,
    GoogleFrontDoor,
    ImprovementSection,
    RankedProvider,
    SizeCategory,
    SystemAggregate,
    ThirdPartyAggregate,
    TierScores,
    UsNewsRanking,
)
from .practice_models import PROFILE_DISPLAY, classify_practice_profile
from .practice_prompts import build_practice_prompt
from . import practice_scoring

# Import shared helpers from the hospital analyzer
from .strings import AIVS_DISCLAIMER_CHECK as _AIVS_DISCLAIMER_CHECK, FULL_DISCLAIMER as _FULL_DISCLAIMER
from .analyzer import (
    _get_client,
    _MODEL,
    _clean,
    _slug,
    _gather_individual_evidence,
    _stream_narrative,
    _save_to_db,
    _AIVS_DISCLAIMER,
    _warn_grade_mismatch,
    _warn_snake_case,
)

_RUBRIC_VERSION = "practice-v1.0"

# ── Address normalization for composite dedup ─────────────────────────────────
_DIRECTIONAL_ABBR: dict[str, str] = {
    r"\bne\b": "northeast", r"\bnw\b": "northwest",
    r"\bse\b": "southeast", r"\bsw\b": "southwest",
    r"\be\b": "east",  r"\bw\b": "west",
    r"\bn\b": "north", r"\bs\b": "south",
}
_STREET_TYPE_ABBR: dict[str, str] = {
    r"\brd\b": "road",    r"\bst\b": "street",  r"\bave\b": "avenue",
    r"\bblvd\b": "boulevard", r"\bdr\b": "drive", r"\bln\b": "lane",
    r"\bct\b": "court",  r"\bpl\b": "place",   r"\bpkwy\b": "parkway",
    r"\bhwy\b": "highway",
}


def _normalize_street(s: str) -> str:
    """Normalize a street string for identity comparison.

    Expands common directional and street-type abbreviations, strips suite
    suffixes and leading street numbers, removes punctuation, and lowercases.
    Used only for composite dedup — not in scoring or data collection.
    """
    if not s:
        return ""
    s = s.lower()
    for pat, rep in _DIRECTIONAL_ABBR.items():
        s = re.sub(pat, rep, s)
    for pat, rep in _STREET_TYPE_ABBR.items():
        s = re.sub(pat, rep, s)
    s = re.sub(r'\b(suite|ste|apt|unit|#)\s*[\w-]+', '', s)
    s = re.sub(r'^\d+\s*', '', s)
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


_HOSPITAL_SIGNAL_TOKENS = frozenset({"leapfrog", "cms overall star", "hcahps"})


def _hospital_signal(text: str) -> bool:
    """Return True if text references a hospital-only signal that should not appear in practice reports."""
    tl = text.lower()
    return any(tok in tl for tok in _HOSPITAL_SIGNAL_TOKENS)

# ─────────────────────────────────────────────────────────────────────────────
# Practice-edition structured extraction tool
# Uses the same TierScores field keys as the hospital tool, but semantically
# remapped in the description so Claude fills in practice pillar scores.
# Additional fields capture practice-specific derived metrics.
# ─────────────────────────────────────────────────────────────────────────────
_PRACTICE_TIER_SCORES_SCHEMA = {
    "type": "object",
    "description": (
        "Practice pillar scores 0–100. FIELD MAPPING: "
        "clinical_outcomes_safety = Pillar 1 (Practitioner Credentials & Clinical Quality); "
        "credentials_recognition = Pillar 2 (Reviews & Reputation); "
        "patient_experience_reviews = Pillar 3 (Identity & Machine-Readability); "
        "access_fit = Pillar 4 (Access & Fit). "
        "All values are from public data per the practice rubric anchor rubric. "
        "Null only if truly no data exists for that pillar."
    ),
    "properties": {
        "clinical_outcomes_safety":   {"type": ["integer", "null"]},
        "credentials_recognition":    {"type": ["integer", "null"]},
        "patient_experience_reviews": {"type": ["integer", "null"]},
        "access_fit":                 {"type": ["integer", "null"]},
    },
    "required": [
        "clinical_outcomes_safety", "credentials_recognition",
        "patient_experience_reviews", "access_fit",
    ],
    "additionalProperties": False,
}

_PRACTICE_GOOGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "front_door": {
            "type": "object",
            "properties": {
                "rating":   {"type": ["number", "null"]},
                "count":    {"type": ["integer", "null"]},
                "recency":  {"type": ["string", "null"]},
                "verified": {"type": "boolean"},
                "reason":   {"type": ["string", "null"]},
            },
            "required": ["rating", "count", "verified"],
            "additionalProperties": False,
        },
        "listings_estimate": {"type": "string"},
        "rating_range":      {"type": "string"},
        "consistency":       {"type": "string"},
        "gap_note":          {"type": "string"},
    },
    "required": ["front_door", "consistency", "gap_note"],
    "additionalProperties": False,
}

_PRACTICE_TOOL = {
    "name": "submit_practice_result",
    "description": (
        "Submit the structured practice AI Visibility analysis result. "
        "Call exactly once after completing the full narrative report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "market_overview": {
                "type": "string",
                "description": (
                    "2–3 paragraph Organization Overview section — practice history, "
                    "specialty focus, roster, ownership, market position."
                ),
            },
            "ai_visibility_verdict": {
                "type": "string",
                "description": (
                    "2–3 sentence AI Visibility Verdict — how the practice and its "
                    "physicians currently surface to AI assistants."
                ),
            },
            "weighting_profile": {
                "type": "string",
                "enum": [
                    "practice_procedural",
                    "practice_relationship",
                    "practice_referral_fed",
                    "practice_hybrid",
                ],
                "description": "The practice AI Visibility weighting profile for this entity.",
            },
            "top_recommendation": {
                "type": "string",
                "description": "The single most important AI visibility improvement for this practice.",
            },
            "practical_advice": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Leave as empty array [] — improvement actions go in improvement_sections.",
            },
            "improvement_sections": {
                "type": "array",
                "description": (
                    "Improvement actions grouped into labeled sections. Standard titles: "
                    "'Your Website (Technical & Content Fixes)', "
                    "'Third-Party Listings & Profiles', "
                    "'Reputation, Press & Community (Long-Term Training Data)'. "
                    "Add additional sections as needed with a clear title and one-line description."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "title":       {"type": "string"},
                        "description": {"type": "string"},
                        "items":       {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "description", "items"],
                    "additionalProperties": False,
                },
            },
            "disclaimer": {"type": "string"},
            "entity_resolution_pct": {
                "type": ["number", "null"],
                "description": (
                    "Estimated % of standardized battery runs that correctly resolve "
                    "this practice (not a competitor or stale location). "
                    "Triggers score ceiling if <85. Null if unassessable."
                ),
            },
            "linkage_integrity_pct": {
                "type": ["number", "null"],
                "description": (
                    "Estimated % of physician-level runs where the assistant correctly "
                    "links the physician to this practice. Linkage Integrity. "
                    "Triggers score ceiling if <70. Null if unassessable."
                ),
            },
            "board_cert_unverifiable": {
                "type": "boolean",
                "description": (
                    "True if ANY sampled physician's board certification cannot be confirmed "
                    "from crawlable public sources. Triggers score ceiling."
                ),
            },
            "key_person_flag": {
                "type": "boolean",
                "description": (
                    "True if this is a solo or two-physician practice where one physician's "
                    "profile IS the practice. No score change — but must be flagged."
                ),
            },
            "rankings": {
                "type": "array",
                "description": (
                    "Single-element array containing the practice as rank=1. "
                    "Include ALL data fields for this one entity."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "rank": {"type": "integer"},
                        "name": {"type": "string"},
                        "website_url": {
                            "type": ["string", "null"],
                            "description": "Primary public website URL, or null.",
                        },
                        "affiliation_type": {
                            "type": "string",
                            "enum": ["independent", "hospital_affiliated", "unknown"],
                        },
                        "size_category": {
                            "type": "string",
                            "enum": ["large", "community", "unknown"],
                        },
                        "physician_count": {"type": ["string", "null"]},
                        "overall_rating": {"type": "string"},
                        "weighting_profile": {
                            "type": "string",
                            "enum": [
                                "practice_procedural", "practice_relationship",
                                "practice_referral_fed", "practice_hybrid",
                            ],
                        },
                        "tier_scores": _PRACTICE_TIER_SCORES_SCHEMA,
                        "google_footprint": _PRACTICE_GOOGLE_SCHEMA,
                        "third_party_aggregate": {
                            "type": "object",
                            "properties": {
                                "rating":  {"type": ["number", "null"]},
                                "sources": {"type": "string"},
                                "note":    {"type": "string"},
                            },
                            "required": ["note"],
                            "additionalProperties": False,
                        },
                        "disqualifiers":         {"type": "array", "items": {"type": "string"}},
                        "key_strengths":         {"type": "array", "items": {"type": "string"}},
                        "notable_weaknesses":    {"type": "array", "items": {"type": "string"}},
                        "best_suited_for":       {"type": "string"},
                        "recommendation_summary": {"type": "string"},
                        "consolidated_locations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name":               {"type": "string"},
                                    "overall_rating":     {"type": "string"},
                                    "google_rating":      {"type": ["number", "null"]},
                                    "google_review_count": {"type": ["integer", "null"]},
                                    "address":            {"type": ["string", "null"]},
                                },
                                "required": ["name", "overall_rating"],
                                "additionalProperties": False,
                            },
                        },
                        "patient_voice_summary": {
                            "type": "string",
                            "description": (
                                "2–3 sentences on recurring patient themes from Google, "
                                "Healthgrades, Vitals, and any CAHPS data. Note physician-level "
                                "themes if they diverge from org-level themes."
                            ),
                        },
                        "accreditations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Practice-level accreditations: AAAHC, AAAASF, NCQA PCMH, "
                                "specialty certifications. Empty array if none confirmed."
                            ),
                        },
                        "cms_quality_highlights": {
                            "type": "string",
                            "description": "MIPS score, QPP performance, or affiliated-hospital quality notes. Empty string if not applicable.",
                        },
                        "ai_says": {
                            "type": "string",
                            "description": (
                                "2–3 sentences: how AI assistants currently describe this practice "
                                "and its physicians when a patient queries by specialty + city. "
                                "Note physician surfacing in physician-first queries and any identity confusion."
                            ),
                        },
                        "leapfrog_grade":  {"type": ["string", "null"], "description": "Affiliated hospital Leapfrog grade if relevant, or null."},
                        "cms_star_rating": {"type": ["integer", "null"], "description": "Affiliated hospital CMS star rating if relevant, or null."},
                        "us_news_rankings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category":         {"type": "string"},
                                    "rank":             {"type": ["integer", "null"]},
                                    "recognition_type": {"type": "string", "enum": ["nationally_ranked", "high_performing"]},
                                },
                                "required": ["category", "recognition_type"],
                                "additionalProperties": False,
                            },
                        },
                        "trauma_level":    {"type": ["string", "null"]},
                        "teaching_status": {"type": ["string", "null"], "enum": ["major", "minor", "not_teaching", None]},
                    },
                    "required": [
                        "rank", "name", "website_url", "affiliation_type", "overall_rating",
                        "weighting_profile", "tier_scores", "google_footprint",
                        "key_strengths", "notable_weaknesses", "best_suited_for",
                        "recommendation_summary", "patient_voice_summary",
                        "accreditations", "cms_quality_highlights", "ai_says",
                        "leapfrog_grade", "cms_star_rating", "us_news_rankings",
                        "trauma_level", "teaching_status",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "market_overview", "ai_visibility_verdict", "weighting_profile",
            "top_recommendation", "practical_advice", "disclaimer", "rankings",
            "entity_resolution_pct", "linkage_integrity_pct",
            "board_cert_unverifiable", "key_person_flag",
        ],
        "additionalProperties": False,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_practice_provider(r: dict, run_profile: str) -> RankedProvider:
    """Build a RankedProvider from Claude's extracted practice data."""
    ts  = r.get("tier_scores") or {}
    fp  = r.get("google_footprint") or {}
    fd  = fp.get("front_door") or {}
    tpa = r.get("third_party_aggregate") or {}
    return RankedProvider(
        rank=r["rank"],
        name=_clean(r["name"]),
        website_url=r.get("website_url") or None,
        affiliation_type=AffiliationType(r.get("affiliation_type", "unknown")),
        size_category=SizeCategory(r.get("size_category", "unknown")),
        physician_count=r.get("physician_count") or None,
        overall_rating=r.get("overall_rating") or "",
        weighting_profile=r.get("weighting_profile") or run_profile,
        tier_scores=TierScores(
            clinical_outcomes_safety=ts.get("clinical_outcomes_safety"),
            credentials_recognition=ts.get("credentials_recognition"),
            patient_experience_reviews=ts.get("patient_experience_reviews"),
            access_fit=ts.get("access_fit"),
        ),
        google_footprint=GoogleFootprint(
            front_door=GoogleFrontDoor(
                rating=fd.get("rating"),
                count=fd.get("count"),
                recency=fd.get("recency"),
                verified=bool(fd.get("verified")),
                reason=fd.get("reason"),
            ),
            listings_estimate=fp.get("listings_estimate") or "",
            rating_range=fp.get("rating_range") or "",
            consistency=fp.get("consistency") or "",
            gap_note=fp.get("gap_note") or "",
        ),
        third_party_aggregate=ThirdPartyAggregate(
            rating=tpa.get("rating"),
            sources=tpa.get("sources") or "Healthgrades, Vitals, WebMD, Yelp",
            note=tpa.get("note") or "",
        ),
        disqualifiers=[d for d in r.get("disqualifiers", []) if isinstance(d, str)],
        key_strengths=[s for s in r.get("key_strengths", [])
                       if isinstance(s, str) and not _hospital_signal(s)],
        notable_weaknesses=[w for w in r.get("notable_weaknesses", [])
                            if isinstance(w, str) and not _hospital_signal(w)],
        best_suited_for=r.get("best_suited_for") or "",
        recommendation_summary=r.get("recommendation_summary") or "",
        consolidated_locations=[
            ConsolidatedLocation(
                name=loc["name"],
                overall_rating=loc.get("overall_rating", ""),
                google_rating=loc.get("google_rating"),
                google_review_count=loc.get("google_review_count"),
                address=loc.get("address"),
            )
            for loc in r.get("consolidated_locations", [])
            if isinstance(loc, dict) and loc.get("name")
        ],
        patient_voice_summary=r.get("patient_voice_summary") or "",
        leapfrog_grade=r.get("leapfrog_grade") or None,
        accreditations=[a for a in r.get("accreditations", []) if isinstance(a, str)],
        cms_quality_highlights=r.get("cms_quality_highlights") or "",
        cms_star_rating=r.get("cms_star_rating") or None,
        us_news_rankings=[
            UsNewsRanking(
                category=u.get("category", ""),
                rank=u.get("rank"),
                recognition_type=u.get("recognition_type", "nationally_ranked"),
            )
            for u in (r.get("us_news_rankings") or [])
            if isinstance(u, dict) and u.get("category")
        ],
        ai_says=r.get("ai_says") or "",
        trauma_level=r.get("trauma_level") or None,
        teaching_status=r.get("teaching_status") or None,
        report_type="practice",
    )


def _ground_and_score_practice(
    prov: RankedProvider,
    city: str,
    state: str,
    entity_resolution_pct: Optional[float],
    linkage_integrity_pct: Optional[float],
    board_cert_unverifiable: bool,
) -> None:
    """Verify Google front door, update Reviews & Reputation tier (Pillar 2), and
    compute deterministic practice composite score with ceiling."""
    read, footprint = places.fetch_provider(prov.name, city, state)

    if read is not None and read.verified:
        prov.google_footprint.front_door = GoogleFrontDoor(
            rating=read.rating,
            count=read.review_count,
            recency=read.business_status or prov.google_footprint.front_door.recency,
            verified=True,
            reason=None,
        )
        # For practices, Google rating feeds Pillar 2 (credentials_recognition)
        band = practice_scoring.reviews_band(read.rating, read.review_count)
        if band is not None:
            prov.tier_scores.credentials_recognition = band
    elif read is not None:
        prov.google_footprint.front_door = GoogleFrontDoor(verified=False, reason=read.reason)

    if footprint and footprint.listings_sampled > 1 and not prov.google_footprint.rating_range:
        prov.google_footprint.rating_range = footprint.as_line()

    # Compute composite with ceiling
    profile = prov.weighting_profile or "practice_procedural"
    score, ceiling_applied, ceiling_reason = practice_scoring.composite(
        prov.tier_scores.as_dict(),
        profile,
        entity_resolution_pct=entity_resolution_pct,
        linkage_integrity_pct=linkage_integrity_pct,
        board_cert_unverifiable=board_cert_unverifiable,
    )
    prov.ai_visibility_score = score
    # Grade is always computed — never left to the LLM.
    prov.overall_rating, _ = scoring.grade_from_score(prov.ai_visibility_score)

    # Stash ceiling metadata on the provider for DB persistence
    prov._score_ceiling_applied = ceiling_applied   # type: ignore[attr-defined]
    prov._score_ceiling_reason  = ceiling_reason    # type: ignore[attr-defined]


def _save_practice_extras(
    result: AnalysisResult,
    entity_resolution_pct: Optional[float],
    linkage_integrity_pct: Optional[float],
    physician_capture_rate: Optional[float],
    key_person_flag: bool,
) -> None:
    """UPDATE the practice-specific columns written AFTER the base _save_to_db call."""
    con = get_connection()
    # Update analysis_runs with practice-specific fields
    con.execute(
        """UPDATE analysis_runs
           SET entity_type=?, rubric_version=?, practice_profile=?
           WHERE run_id=?""",
        [result.entity_type, result.rubric_version, result.practice_profile, result.run_id],
    )
    # Update ranked_providers for rank=1 with derived metrics + ceiling
    if result.rankings:
        p = result.rankings[0]
        ceiling_applied = getattr(p, "_score_ceiling_applied", False)
        ceiling_reason  = getattr(p, "_score_ceiling_reason", "")
        con.execute(
            """UPDATE ranked_providers
               SET entity_resolution_pct=?, linkage_integrity_pct=?,
                   physician_capture_rate=?, key_person_flag=?,
                   score_ceiling_applied=?, score_ceiling_reason=?
               WHERE run_id=? AND rank=1""",
            [
                entity_resolution_pct, linkage_integrity_pct,
                physician_capture_rate, key_person_flag,
                ceiling_applied, ceiling_reason,
                result.run_id,
            ],
        )
    con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def analyze_practice(
    entity_name: str,
    city: str,
    state: str,
    specialty: Optional[str] = None,
    aggregate: bool = False,
    practice_profile: Optional[str] = None,
    teaser_report: bool = False,
    output_dir: str | Path = "reports",
    on_event: Optional[Callable] = None,
    brand: str = "original",
    skip_pdf: bool = False,
    practice_composite: bool = False,
    practice_roster: list[dict] | None = None,
    physician_composite: bool = False,
    physician_roster: dict | None = None,
    force_rerun: bool = False,
    briefing_variant: Optional[str] = None,
    override_today_lock: bool = False,
    report_title: Optional[str] = None,
    confirmed_siblings: Optional[list] = None,
    org_name: Optional[str] = None,
) -> AnalysisResult:
    """Run a Practice Edition AI Visibility analysis for a single named practice.

    Parallel to analyze_location() — hospital path is not modified.
    Returns an AnalysisResult with entity_type='practice' and practice-edition
    pillar scores, derived metrics, and ceiling logic applied.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    from rich.console import Console
    console = Console(force_terminal=True, stderr=True)

    emit({"type": "phase", "name": "starting", "text": "Starting practice analysis"})
    init_db()

    # Cache logic
    from .db import get_recent_run
    _loc_key = f"{city}, {state}"
    if not override_today_lock:
        # Same-day lock: always serve today's result regardless of force_rerun
        _today = get_recent_run(entity_name, _loc_key, days=0, entity_type="practice", aggregate=aggregate)
        if _today:
            emit({"type": "phase", "name": "cached", "text": f"Returning today's cached result for {entity_name}"})
            return AnalysisResult.model_validate_json(_today["result_json"])
        # 90-day cache (only when force_rerun is not set)
        if not force_rerun:
            _cached = get_recent_run(entity_name, _loc_key, entity_type="practice", aggregate=aggregate)
            if _cached:
                emit({"type": "phase", "name": "cached", "text": f"Returning cached result for {entity_name}"})
                return AnalysisResult.model_validate_json(_cached["result_json"])
    client = _get_client()
    run_id = str(uuid.uuid4())

    # Classify profile if not supplied by the user
    if not practice_profile:
        practice_profile = classify_practice_profile(specialty)
    profile_label = PROFILE_DISPLAY.get(practice_profile, "Procedural")

    # ── Phase 0: Gather evidence ──────────────────────────────────────────────
    emit({"type": "phase", "name": "evidence", "text": "Gathering practice evidence"})
    with console.status("[bold dark_sea_green4]Fetching entity Google data…[/bold dark_sea_green4]"):
        try:
            evidence_text, _indiv_read = _gather_individual_evidence(entity_name, city, state)
            if _indiv_read.formatted_address:
                _ratio = places.city_match_ratio(city, _indiv_read.formatted_address)
                if _ratio < 0.85:
                    _found_city = places._city_from_address(_indiv_read.formatted_address)
                    if _found_city:
                        emit({"type": "confirm_city", "input_city": city,
                              "suggested_city": _found_city, "ratio": round(_ratio, 3)})
        except Exception as exc:
            console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
            evidence_text = (
                f"=== Evidence for {entity_name}, {city}, {state} ===\n"
                "Google data unavailable this run."
            )
            _indiv_read = None
    console.print(f"[green]✓[/green] Practice evidence: {entity_name}")

    # Capture anchor's normalized street address for address-based composite dedup (D2).
    # Uses only the street line (before the first comma) to avoid city/state noise.
    _anchor_addr_norm = ""
    if _indiv_read and getattr(_indiv_read, "formatted_address", None):
        _street_line = _indiv_read.formatted_address.split(",")[0]
        _anchor_addr_norm = _normalize_street(_street_line)

    # For aggregate runs, build a stable location roster for the scoring prompt.
    # When confirmed_siblings is provided by the initiation flow, use it directly
    # (skips the Claude discovery call). None means run discovery as usual.
    _location_roster: list[str] | None = None
    if aggregate:
        if confirmed_siblings is not None:
            # Pre-confirmed by the user in the initiation screen
            if confirmed_siblings:
                _location_roster = [entity_name] + [s["name"] for s in confirmed_siblings]
                emit({"type": "phase", "name": "discovery",
                      "text": f"Using confirmed roster: {len(_location_roster)} locations"})
            else:
                emit({"type": "phase", "name": "discovery",
                      "text": f"Single-location run for {entity_name}"})
        else:
            from .practice_discovery import discover_practice_siblings as _disc_siblings
            emit({"type": "phase", "name": "discovery", "text": f"Discovering {entity_name} locations"})
            _siblings, _discovered_org_name = _disc_siblings(
                entity_name, city, state, on_event=emit, force_rerun=force_rerun
            )
            if _siblings:
                _location_roster = [entity_name] + [s["name"] for s in _siblings]
            # Use discovered org name as fallback when not provided by frontend
            if not org_name and _discovered_org_name:
                org_name = _discovered_org_name

    # If org_name is set and no custom report_title provided, default title to org_name
    if org_name and not report_title:
        report_title = org_name

    system_prompt, user_prompt = build_practice_prompt(
        entity_name=entity_name,
        city=city,
        state=state,
        specialty=specialty,
        evidence_block=evidence_text,
        aggregate=aggregate,
        practice_profile=practice_profile,
        location_roster=_location_roster,
        org_name=org_name,
    )

    # ── Phase 1: Stream narrative ─────────────────────────────────────────────
    emit({"type": "phase", "name": "generating", "text": "Generating practice analysis"})
    report_markdown = _stream_narrative(client, system_prompt, user_prompt, emit, console)

    # ── Phase 2: Extract structured data ─────────────────────────────────────
    emit({"type": "phase", "name": "structured", "text": "Extracting structured data"})

    extraction_prompt = (
        "Extract the structured data from the completed practice AI Visibility report below "
        "by calling submit_practice_result. Use the full report to populate all fields.\n\n"
        "FIELD MAPPING for tier_scores:\n"
        "  clinical_outcomes_safety   = Practitioner Credentials & Clinical Quality score\n"
        "  credentials_recognition    = Reviews & Reputation score\n"
        "  patient_experience_reviews = Identity & Machine-Readability score\n"
        "  access_fit                 = Access & Fit score\n\n"
        "DERIVED METRICS: Set entity_resolution_pct and linkage_integrity_pct based on "
        "your analysis of naming/identity risks and physician attribution risks observed. "
        "Set board_cert_unverifiable=true if ANY sampled physician's ABMS/AOA cert "
        "could not be confirmed from crawlable sources. "
        "Set key_person_flag=true for solo or two-physician practices.\n\n"
        "CEILING NOTE: The system applies the ≤74 ceiling automatically — report "
        "the raw pillar scores honestly; do NOT pre-apply the ceiling yourself.\n\n"
        + (
            "CONSOLIDATED LOCATIONS: This is an aggregate run covering the following confirmed "
            "locations — populate consolidated_locations with ALL of them. Use the ratings and "
            "addresses from the report where available; set google_rating=null and "
            "google_review_count=null for locations not individually rated in the report. "
            "The anchor/flagship must be the first entry.\n\nConfirmed locations:\n"
            + "\n".join(f"  - {loc}" for loc in (_location_roster or [entity_name]))
            + "\n\n"
            if _location_roster else
            "CONSOLIDATED LOCATIONS: Populate consolidated_locations with any locations "
            "mentioned in the report with their ratings.\n\n"
        )
        + "The practice entity must appear as rank=1 in rankings.\n\n"
        "--- REPORT ---\n"
        f"{report_markdown}\n--- END REPORT ---"
    )

    structured_data: dict = {}
    with console.status("[bold dark_sea_green4]Extracting practice structured data…[/bold dark_sea_green4]"):
        with client.messages.stream(
            model=_MODEL,
            max_tokens=16000,
            tools=[_PRACTICE_TOOL],
            tool_choice={"type": "tool", "name": "submit_practice_result"},
            messages=[{"role": "user", "content": extraction_prompt}],
        ) as stream:
            response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        console.print("[yellow]⚠[/yellow] Practice extraction hit token limit — partial data only")
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_practice_result":
            structured_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    run_profile = structured_data.get("weighting_profile") or practice_profile

    # Extract derived metrics from tool output
    entity_resolution_pct   = structured_data.get("entity_resolution_pct")
    linkage_integrity_pct   = structured_data.get("linkage_integrity_pct")
    physician_capture_rate  = None  # not AI-generated; computed from battery logs when available
    board_cert_unverifiable = bool(structured_data.get("board_cert_unverifiable", False))
    key_person_flag         = bool(structured_data.get("key_person_flag", False))

    rankings = [
        _build_practice_provider(r, run_profile)
        for r in structured_data.get("rankings", [])
    ]

    # ── Phase 3: Verify Google + score with ceiling ───────────────────────────
    emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
    for prov in rankings:
        _ground_and_score_practice(
            prov, city, state,
            entity_resolution_pct=entity_resolution_pct,
            linkage_integrity_pct=linkage_integrity_pct,
            board_cert_unverifiable=board_cert_unverifiable,
        )
    console.print(f"[green]✓[/green] Scored practice ({run_profile} / {profile_label})")

    disclaimer = _FULL_DISCLAIMER   # always hardcoded; LLM-generated disclaimer field ignored

    # Post-extraction validation
    _verdict = _clean(structured_data.get("ai_visibility_verdict", ""))
    _warn_snake_case(_verdict, context="practice/ai_visibility_verdict")
    for _p in rankings:
        _computed_grade, _ = scoring.grade_from_score(_p.ai_visibility_score)
        _warn_grade_mismatch(_verdict, _computed_grade, context=f"practice/verdict/{_p.name}")

    result = AnalysisResult(
        run_id=run_id,
        location=f"{city}, {state}",
        specialty=specialty,
        aggregate=aggregate,
        patient_perspective=False,
        teaser_report=teaser_report,
        individual_report=True,
        entity_name=entity_name,
        report_title=report_title,
        generated_at=date.today(),
        weighting_profile=run_profile,
        entity_type="practice",
        rubric_version=_RUBRIC_VERSION,
        practice_profile=run_profile,
        market_overview=_clean(structured_data.get("market_overview", "")),
        ai_visibility_verdict=_clean(structured_data.get("ai_visibility_verdict", "")),
        coverage_note=f"Individual practice report: {entity_name}",
        top_recommendation=_clean(structured_data.get("top_recommendation", "")),
        practical_advice=[],
        improvement_sections=[
            ImprovementSection(
                title=_clean(s.get("title", "")),
                description=_clean(s.get("description", "")),
                items=[
                    _clean(i) for i in s.get("items", [])
                    if isinstance(i, str) and not _hospital_signal(i)
                ],
            )
            for s in structured_data.get("improvement_sections", [])
            if isinstance(s, dict) and s.get("title") and not _hospital_signal(s.get("title", ""))
        ],
        disclaimer=disclaimer,
        rankings=rankings,
        report_markdown=report_markdown,
    )

    # ── Practice Composite reputation collection (before PDF so table is included) ──
    if practice_composite:
        emit({"type": "phase", "name": "practice_reputation", "text": "Collecting practice reputation"})
        from .practice_reputation import collect_platform_data
        from .practice_discovery import discover_practice_siblings

        # Practice-anchored table: the analyzed practice is the anchor row (pinned
        # first, visually distinguished).  Siblings are discovered separately so the
        # anchor entity never appears in the discovery list.
        anchor_entry = {
            "name": entity_name,
            "entity_type": "practice",
            "is_anchor": True,
            "city": city,
            "state": state,
        }
        sibling_roster = list(practice_roster or [])
        if not sibling_roster:
            sibling_roster = discover_practice_siblings(
                entity_name, city, state,
                on_event=emit,
                force_rerun=force_rerun,
            )

        # Drop siblings that resolve to the anchor entity (prevents duplicate rows in
        # the composite table when Claude names a location variant of the anchor).
        # Bidirectional AND: both directions must score "strong" (≥0.6 token overlap)
        # to drop a sibling.  This correctly catches location-suffix/label variants
        # ("-  Desert Inn", "(Main Office)") while keeping siblings with a genuinely
        # distinct location qualifier ("Summerlin", "West Campus").  The check is
        # unconditional — the anchor name need not contain digits.
        from .data.places import _name_match as _nmatch
        _anchor_lc = entity_name.strip().lower()

        # Recognized same-location alias markers stripped from sibling BEFORE token-overlap
        # check.  Direction rule: the canonical anchor name is never modified; only the
        # sibling is tested for alias-ness.  This catches "(Main Office)" even when the
        # anchor name has no address tokens to pad the ratio.
        _ALIAS_PARENTHETICALS = re.compile(
            r'\s*\(\s*(main\s+(office|campus)|headquarters|hq'
            r'|primary\s+(campus|location|office))\s*\)\s*$'
            r'|\s*[-–—]\s*(main\s+(office|campus)|headquarters|hq)\s*$',
            re.IGNORECASE,
        )

        def _is_anchor_duplicate(sibling_name: str, sibling_address: str = "") -> bool:
            if sibling_name.strip().lower() == _anchor_lc:
                return True
            # Strip recognized alias markers from sibling before token-overlap check.
            # Canonical anchor name is never modified — only the sibling is tested.
            _stripped = _ALIAS_PARENTHETICALS.sub('', sibling_name).strip()
            if _stripped.lower() != sibling_name.strip().lower():
                if _stripped.lower() == _anchor_lc:
                    return True
                if (
                    _nmatch(entity_name, _stripped) == "strong"
                    and _nmatch(_stripped, entity_name) == "strong"
                ):
                    return True
            # Bidirectional name-token AND on original name
            if (
                _nmatch(entity_name, sibling_name) == "strong"
                and _nmatch(sibling_name, entity_name) == "strong"
            ):
                return True
            # Address-based: sibling at same normalized street as anchor is a duplicate
            # regardless of how the street is abbreviated or formatted.
            if _anchor_addr_norm:
                if sibling_address and _normalize_street(sibling_address) == _anchor_addr_norm:
                    return True
                # Address embedded in sibling name: strip anchor base prefix, treat remainder
                sn_lower = sibling_name.lower()
                if sn_lower.startswith(_anchor_lc):
                    remainder = re.sub(r'^[\s\-,]+', '', sn_lower[len(_anchor_lc):])
                    if remainder and _normalize_street(remainder) == _anchor_addr_norm:
                        return True
            return False

        # Brand-prefix canonicalization: "AnchorName - X" → "X", so a sibling
        # named with and without the parent brand prefix deduplicates to one row.
        _brand_prefix = entity_name.strip() + " - "
        _brand_prefix_lc = _brand_prefix.lower()

        def _canon_sibling(name: str) -> str:
            if name.strip().lower().startswith(_brand_prefix_lc):
                return name.strip()[len(_brand_prefix):]
            return name.strip()

        deduped: list[dict] = []
        seen_names: set[str] = set()
        for s in sibling_roster:
            sn = s.get("name", "")
            sa = s.get("address", "")
            if _is_anchor_duplicate(sn, sa):
                continue
            canonical = _canon_sibling(sn)
            k = canonical.lower()
            if k not in seen_names:
                seen_names.add(k)
                deduped.append({**s, "name": canonical} if canonical != sn else s)
        sibling_roster = deduped

        roster = [anchor_entry] + sibling_roster

        result.practice_composite_rows = collect_platform_data(
            roster, entity_name, city, state, on_event=emit, run_id=result.run_id
        )

        # Bind the anchor row's google_rating to the same Places read used for
        # the main analysis score so the header star value and composite table
        # star value are always identical (Item 2).
        if rankings and result.practice_composite_rows:
            _fd_rating = rankings[0].google_footprint.front_door.rating
            _fd_verified = rankings[0].google_footprint.front_door.verified
            for _cr in result.practice_composite_rows:
                if _cr.get("is_anchor") and _fd_rating is not None:
                    _cr["google_rating"] = _fd_rating
                    if not _fd_verified:
                        _cr["google_rating"] = None

        if physician_composite and result.practice_composite_rows:
            emit({"type": "phase", "name": "physician_reputation", "text": "Collecting physician reputation"})
            from .physician_discovery import discover_physicians
            from .physician_reputation import collect_physician_data
            confirmed = physician_roster or {}
            # Flatten confirmed physicians (keyed by org name from the UI)
            all_confirmed = [ph for v in confirmed.values() for ph in v]
            # Discover at the organization level once — sub-location names yield 0 results
            # since NPPES indexes physicians under the parent organization, not specific clinics.
            target_row = (
                next((r for r in result.practice_composite_rows if r.get("is_anchor")), None)
                or next((r for r in result.practice_composite_rows if r.get("entity_type") != "hospital"), None)
            )
            if target_row:
                physicians = all_confirmed or discover_physicians(entity_name, city, state, on_event=emit)
                if physicians:
                    ph_results = collect_physician_data(
                        physicians, entity_name, city, state, on_event=emit
                    )
                    target_row["physicians"] = ph_results
                    result.physician_composite_rows.extend(ph_results)

    # ── Save markdown ─────────────────────────────────────────────────────────
    _type = (specialty or "Practice").replace(" ", "-")
    _ts   = datetime.utcnow().strftime("%y%m%d-%H%M")
    _entity_slug = _slug(entity_name)[:40]
    if teaser_report:
        _stem = f"{_entity_slug}_{city.replace(' ', '-')}_{state}_Practice-Summary-{_ts}"
    else:
        _stem = f"{_entity_slug}_{city.replace(' ', '-')}_{state}_Practice-{_ts}"
    report_path = output_dir / f"{_stem}.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    console.print(f"[green]✓[/green] Practice report → [dim]{report_path}[/dim]")

    # ── Render PDF ────────────────────────────────────────────────────────────
    # Block PDF when pillar coverage is too thin to produce a credible score.
    if rankings and all(p.ai_visibility_score is None for p in rankings):
        _failure_msg = (
            "Collection was too incomplete to score this practice — "
            "fewer than half the pillar weights were populated. "
            "No prospect-facing PDF was produced. Review the markdown report and rerun."
        )
        emit({"type": "error", "message": _failure_msg})
        console.print(f"[yellow]⚠[/yellow] {_failure_msg}")
        skip_pdf = True

    if skip_pdf:
        console.print("[dim]skip_pdf=True — PDF rendering skipped[/dim]")
        result.md_path = str(report_path)
    else:
        if result.teaser_report:
            print(f"[TEASER DEBUG] practice_analyzer.py: about to render_pdf with teaser_report={result.teaser_report} individual_report={result.individual_report} entity_name={result.entity_name!r}", flush=True)
        emit({"type": "phase", "name": "pdf", "text": "Rendering Practice PDF"})
        with console.status("[bold dark_sea_green4]Rendering practice PDF…[/bold dark_sea_green4]"):
            from .pdf import render_pdf
            pdf_path = output_dir / f"{_stem}.pdf"
            render_pdf(result, pdf_path, brand=brand)
        if result.teaser_report:
            print(f"[TEASER DEBUG] practice_analyzer.py: render_pdf completed, pdf_path={pdf_path}", flush=True)
        console.print(f"[green]✓[/green] Practice PDF → [dim]{pdf_path}[/dim]")
        result.pdf_path = str(pdf_path)
        result.md_path  = str(report_path)

    # ── Persist to DB ─────────────────────────────────────────────────────────
    _save_to_db(result)
    if result.practice_composite_rows:
        from .practice_reputation import save_practice_reputation
        from .physician_reputation import save_physician_reputation
        rep_run_id = save_practice_reputation(result.run_id, result.practice_composite_rows)
        if result.physician_composite_rows and rep_run_id:
            save_physician_reputation(rep_run_id, result.physician_composite_rows)
    _save_practice_extras(
        result,
        entity_resolution_pct=entity_resolution_pct,
        linkage_integrity_pct=linkage_integrity_pct,
        physician_capture_rate=physician_capture_rate,
        key_person_flag=key_person_flag,
    )

    if briefing_variant and not skip_pdf:
        result.briefing_variant = briefing_variant
        try:
            from .briefing import extract as _briefing_extract, BriefingValidationError
            from .briefing_pdf import render_briefing_pdf
            emit({"type": "phase", "name": "briefing", "text": f"Generating Pulse Briefing ({briefing_variant})"})
            with console.status(f"[bold dark_sea_green4]Generating Pulse Briefing ({briefing_variant})…[/bold dark_sea_green4]"):
                _br = _briefing_extract(result, briefing_variant)
                _variant_label = "Sales" if briefing_variant == "sales" else "CS"
                _briefing_path = output_dir / f"{_stem}_Briefing-{_variant_label}.pdf"
                render_briefing_pdf(_br, str(_briefing_path))
            result.briefing_pdf_path = str(_briefing_path)
            from .db import update_briefing_pdf_path
            update_briefing_pdf_path(result.run_id, str(_briefing_path))
            console.print(f"[green]✓[/green] Briefing PDF  → [dim]{_briefing_path}[/dim]")
            emit({"type": "briefing_ready", "path": str(_briefing_path)})
        except BriefingValidationError as exc:
            reason = f"Missing inputs: {', '.join(exc.missing)}"
            result.briefing_skipped_reason = reason
            console.print(f"[yellow]⚠[/yellow] Briefing skipped — {reason}")
            emit({"type": "briefing_skipped", "reason": reason})
        except Exception as exc:
            reason = str(exc)
            result.briefing_skipped_reason = reason
            console.print(f"[yellow]⚠[/yellow] Briefing generation failed: {reason}")
            emit({"type": "briefing_skipped", "reason": reason})

    emit({"type": "phase", "name": "done_item", "text": "Complete"})
    return result
