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
from .analyzer import (
    _get_client,
    _MODEL,
    _clean,
    _slug,
    _gather_individual_evidence,
    _stream_narrative,
    _save_to_db,
    _AIVS_DISCLAIMER,
)

_RUBRIC_VERSION = "practice-v1.0"

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
            "physician_capture_rate": {
                "type": ["number", "null"],
                "description": (
                    "Estimated % of physician-first discovery queries (Category B) where "
                    "any of this practice's physicians appear. Headline metric. Null if unassessable."
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
            "entity_resolution_pct", "linkage_integrity_pct", "physician_capture_rate",
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
        key_strengths=[s for s in r.get("key_strengths", []) if isinstance(s, str)],
        notable_weaknesses=[w for w in r.get("notable_weaknesses", []) if isinstance(w, str)],
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
            evidence_text = _gather_individual_evidence(entity_name, city, state)
        except Exception as exc:
            console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
            evidence_text = (
                f"=== Evidence for {entity_name}, {city}, {state} ===\n"
                "Google data unavailable this run."
            )
    console.print(f"[green]✓[/green] Practice evidence: {entity_name}")

    system_prompt, user_prompt = build_practice_prompt(
        entity_name=entity_name,
        city=city,
        state=state,
        specialty=specialty,
        evidence_block=evidence_text,
        aggregate=aggregate,
        practice_profile=practice_profile,
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
        "Set physician_capture_rate from physician-first discovery assessment. "
        "Set board_cert_unverifiable=true if ANY sampled physician's ABMS/AOA cert "
        "could not be confirmed from crawlable sources. "
        "Set key_person_flag=true for solo or two-physician practices.\n\n"
        "CEILING NOTE: The system applies the ≤74 ceiling automatically — report "
        "the raw pillar scores honestly; do NOT pre-apply the ceiling yourself.\n\n"
        "The practice entity must appear as rank=1 in rankings.\n\n"
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
    physician_capture_rate  = structured_data.get("physician_capture_rate")
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

    disclaimer = _clean(structured_data.get("disclaimer", ""))
    if "AI Visibility Score" not in disclaimer:
        disclaimer = (disclaimer + " " + _AIVS_DISCLAIMER).strip()

    result = AnalysisResult(
        run_id=run_id,
        location=f"{city}, {state}",
        specialty=specialty,
        aggregate=aggregate,
        patient_perspective=False,
        teaser_report=teaser_report,
        individual_report=True,
        entity_name=entity_name,
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
                items=[_clean(i) for i in s.get("items", []) if isinstance(i, str)],
            )
            for s in structured_data.get("improvement_sections", [])
            if isinstance(s, dict) and s.get("title")
        ],
        disclaimer=disclaimer,
        rankings=rankings,
        report_markdown=report_markdown,
    )

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
    if skip_pdf:
        console.print("[dim]skip_pdf=True — PDF rendering skipped[/dim]")
        result.md_path = str(report_path)
    else:
        emit({"type": "phase", "name": "pdf", "text": "Rendering Practice PDF"})
        with console.status("[bold dark_sea_green4]Rendering practice PDF…[/bold dark_sea_green4]"):
            from .pdf import render_pdf
            pdf_path = output_dir / f"{_stem}.pdf"
            render_pdf(result, pdf_path, brand=brand)
        console.print(f"[green]✓[/green] Practice PDF → [dim]{pdf_path}[/dim]")
        result.pdf_path = str(pdf_path)
        result.md_path  = str(report_path)

    # ── Persist to DB ─────────────────────────────────────────────────────────
    _save_to_db(result)
    _save_practice_extras(
        result,
        entity_resolution_pct=entity_resolution_pct,
        linkage_integrity_pct=linkage_integrity_pct,
        physician_capture_rate=physician_capture_rate,
        key_person_flag=key_person_flag,
    )

    emit({"type": "phase", "name": "done_item", "text": "Complete"})
    return result
