"""FQHC Community Health Edition AI Visibility analysis pipeline.

Implements analyze_fqhc() — a parallel pipeline to analyze_practice() and
analyze_location() for HRSA Section 330 grantees and look-alike health centers.
Neither the hospital nor practice path is modified.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from . import fqhc_scoring, scoring
from .config import settings
from .data import places
from .data import hrsa as hrsa_mod
from .db import get_connection, init_db
from .fqhc_prompts import build_fqhc_prompt
from .models import (
    AffiliationType,
    AnalysisResult,
    ConsolidatedLocation,
    FqhcPillarScores,
    GoogleFootprint,
    GoogleFrontDoor,
    ImprovementSection,
    RankedProvider,
    SizeCategory,
    ThirdPartyAggregate,
    TierScores,
)
from .strings import FULL_DISCLAIMER as _FULL_DISCLAIMER

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

_RUBRIC_VERSION = "community-health-v1.0"

from .strings import AIVS_DISCLAIMER as _AIVS_DISCLAIMER_SENTENCE
_FQHC_DISCLAIMER = (
    "Scores are derived from publicly available signals collected at the time of this report. "
    "Ratings, review counts, accreditation statuses, and quality designations change over time; "
    "verify current standings directly with the primary sources: HRSA Find a Health Center "
    "(findahealthcenter.hrsa.gov), HRSA Uniform Data System (UDS), NCQA (ncqa.org), "
    "Medicaid managed care organization (MCO) directories, Google Business Profile, "
    "and each center's own website.\n\n"
    "No quotes, patient statements, or clinical outcomes in this report have been fabricated. "
    "All quoted or paraphrased language is attributed to publicly available sources. "
    "Any unverifiable signal is rendered as ✗ Not established rather than estimated.\n\n"
    "This report is not a substitute for the judgment of a licensed clinician, benefits "
    "administrator, or public health official. Before making coverage, referral, or care "
    "decisions, confirm provider credentials, network participation, and current eligibility "
    "policies with the relevant insurer and health center directly.\n\n"
    + _AIVS_DISCLAIMER_SENTENCE
)

# ─────────────────────────────────────────────────────────────────────────────
# Structured extraction tool schema
# ─────────────────────────────────────────────────────────────────────────────

_FQHC_PILLAR_SCORES_SCHEMA = {
    "type": "object",
    "description": (
        "FQHC pillar sub-scores 0–100 each. "
        "mqcr_score = null in Round 1 (no battery). "
        "multilingual_score = null in Round 1 (no multilingual battery). "
        "service_adjacent_score is ALWAYS required (AI-assessed). "
        "eligibility_cost_accuracy: score based on fact_audit_rows. "
        "site_service_completeness: site census + directory consistency. "
        "experience_reputation: reviews + narrative framing. "
        "institutional_signals: HRSA quality, UDS, NCQA, 330 status."
    ),
    "properties": {
        "mqcr_score":               {"type": ["integer", "null"]},
        "multilingual_score":       {"type": ["integer", "null"]},
        "service_adjacent_score":   {"type": ["integer", "null"]},
        "eligibility_cost_accuracy": {"type": ["integer", "null"]},
        "site_service_completeness": {"type": ["integer", "null"]},
        "experience_reputation":    {"type": ["integer", "null"]},
        "institutional_signals":    {"type": ["integer", "null"]},
    },
    "required": [
        "mqcr_score", "multilingual_score", "service_adjacent_score",
        "eligibility_cost_accuracy", "site_service_completeness",
        "experience_reputation", "institutional_signals",
    ],
    "additionalProperties": False,
}

_FACT_AUDIT_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "claim":             {"type": "string", "description": "The attested fact (CLIENT-ATTESTED)"},
        "ai_representation": {"type": "string", "description": "What AI assistants say about this"},
        "flag":              {"type": "string", "enum": ["✓", "◐", "✗"]},
        "severity":          {"type": "string", "enum": ["MISSION-CRITICAL", "notable", "minor"]},
        "points":            {"type": ["number", "null"], "description": "Points at stake from rubric"},
    },
    "required": ["claim", "ai_representation", "flag", "severity"],
    "additionalProperties": False,
}

_MISSED_QUERY_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "query":     {"type": "string", "description": "Verbatim mission-frame query"},
        "language":  {"type": "string", "description": "Language of the query"},
        "assistant": {"type": "string", "description": "AI assistant name or 'multiple'"},
        "category":  {"type": "string", "enum": ["eligibility", "service-adjacent", "multilingual", "general"]},
    },
    "required": ["query", "language", "assistant", "category"],
    "additionalProperties": False,
}

_FQHC_TOOL = {
    "name": "submit_fqhc_result",
    "description": (
        "Submit the structured FQHC Community Health Edition AI Visibility result. "
        "Call exactly once after completing the full narrative report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "market_overview": {
                "type": "string",
                "description": (
                    "2–3 paragraph Organization Overview — org history, mission, sites, "
                    "services, populations served, HRSA status, market position."
                ),
            },
            "ai_visibility_verdict": {
                "type": "string",
                "description": (
                    "2 sentences written for read-aloud use. If MQCR not yet computed, "
                    "open with the standard MQCR-not-yet-computed sentence. Then state "
                    "the worst Pillar 2 finding or leading access gap."
                ),
            },
            "top_recommendation": {
                "type": "string",
                "description": "Single most important AI visibility improvement for this health center.",
            },
            "practical_advice": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Leave as empty array [] — improvement actions go in improvement_sections.",
            },
            "improvement_sections": {
                "type": "array",
                "description": "Improvement actions grouped into labeled sections.",
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
            "fqhc_pillar_scores": _FQHC_PILLAR_SCORES_SCHEMA,
            "fact_audit_rows": {
                "type": "array",
                "description": (
                    "Pillar 2 fact-audit table. One row per attested intake fact. "
                    "Any ✗ row must have severity=MISSION-CRITICAL."
                ),
                "items": _FACT_AUDIT_ROW_SCHEMA,
            },
            "missed_queries": {
                "type": "array",
                "description": "Up to 8 mission-frame queries where the org did NOT appear.",
                "items": _MISSED_QUERY_ROW_SCHEMA,
                "maxItems": 8,
            },
            "rankings": {
                "type": "array",
                "description": "Single-element array with the health center as rank=1.",
                "items": {
                    "type": "object",
                    "properties": {
                        "rank":             {"type": "integer"},
                        "name":             {"type": "string"},
                        "website_url":      {"type": ["string", "null"]},
                        "affiliation_type": {"type": "string", "enum": ["independent", "hospital_affiliated", "unknown"]},
                        "size_category":    {"type": "string", "enum": ["large", "community", "unknown"]},
                        "overall_rating":   {"type": "string"},
                        "google_footprint": {
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
                        },
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
                        "key_strengths":         {"type": "array", "items": {"type": "string"}},
                        "notable_weaknesses":    {"type": "array", "items": {"type": "string"}},
                        "best_suited_for":       {"type": "string"},
                        "recommendation_summary": {"type": "string"},
                        "consolidated_locations": {
                            "type": "array",
                            "description": "Own sites only — not affiliated practices.",
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
                        "patient_voice_summary": {"type": "string"},
                        "accreditations":        {"type": "array", "items": {"type": "string"}},
                        "ai_says":               {"type": "string"},
                        "hrsa_verification": {
                            "type": "object",
                            "description": "HRSA directory verification findings.",
                            "properties": {
                                "found_in_hrsa_directory": {"type": ["boolean", "null"]},
                                "is_330":         {"type": ["boolean", "null"]},
                                "is_lookalike":   {"type": ["boolean", "null"]},
                                "site_count":     {"type": ["integer", "null"]},
                                "directory_note": {"type": "string"},
                            },
                            "required": ["directory_note"],
                            "additionalProperties": False,
                        },
                    },
                    "required": [
                        "rank", "name", "website_url", "affiliation_type", "overall_rating",
                        "google_footprint", "key_strengths", "notable_weaknesses",
                        "best_suited_for", "recommendation_summary", "patient_voice_summary",
                        "accreditations", "ai_says", "hrsa_verification",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "market_overview", "ai_visibility_verdict", "top_recommendation",
            "practical_advice", "disclaimer", "rankings", "fqhc_pillar_scores",
            "fact_audit_rows", "missed_queries",
        ],
        "additionalProperties": False,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_fqhc_provider(r: dict) -> RankedProvider:
    fp  = r.get("google_footprint") or {}
    fd  = fp.get("front_door") or {}
    tpa = r.get("third_party_aggregate") or {}
    return RankedProvider(
        rank=r["rank"],
        name=_clean(r["name"]),
        website_url=r.get("website_url") or None,
        affiliation_type=AffiliationType(r.get("affiliation_type", "community")),
        size_category=SizeCategory(r.get("size_category", "community")),
        overall_rating=r.get("overall_rating") or "",
        weighting_profile="community_health",
        tier_scores=TierScores(),  # pillar scores stored separately in fqhc_pillar_scores
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
            sources=tpa.get("sources") or "Healthgrades, Yelp, Google",
            note=tpa.get("note") or "",
        ),
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
        accreditations=[a for a in r.get("accreditations", []) if isinstance(a, str)],
        ai_says=r.get("ai_says") or "",
        report_type="community_health",
    )


def _ground_and_score_fqhc(
    prov: RankedProvider,
    city: str,
    state: str,
    pillar_scores: FqhcPillarScores,
) -> None:
    """Verify Google front door and compute FQHC composite AI Visibility Score."""
    read, footprint = places.fetch_provider(prov.name, city, state)

    if read is not None and read.verified:
        prov.google_footprint.front_door = GoogleFrontDoor(
            rating=read.rating,
            count=read.review_count,
            recency=read.business_status or prov.google_footprint.front_door.recency,
            verified=True,
            reason=None,
        )
        # Anchor experience_reputation from verified Google read
        band = fqhc_scoring.experience_band(read.rating, read.review_count)
        if band is not None and pillar_scores.experience_reputation is None:
            pillar_scores.experience_reputation = band
    elif read is not None:
        prov.google_footprint.front_door = GoogleFrontDoor(verified=False, reason=read.reason)

    if footprint and footprint.listings_sampled > 1 and not prov.google_footprint.rating_range:
        prov.google_footprint.rating_range = footprint.as_line()

    # Compute composite AI Visibility Score from 7 pillar sub-scores
    score = fqhc_scoring.composite(pillar_scores.as_dict())
    prov.ai_visibility_score = score
    prov.overall_rating, _ = scoring.grade_from_score(prov.ai_visibility_score)


def _save_fqhc_extras(
    result: AnalysisResult,
) -> None:
    """Save FQHC-specific data to fqhc_intake and fqhc_fact_audit tables."""
    con = get_connection()
    # Tag analysis_runs with edition
    con.execute(
        "UPDATE analysis_runs SET edition=?, entity_type=?, rubric_version=? WHERE run_id=?",
        ["community_health", "community_health", _RUBRIC_VERSION, result.run_id],
    )

    # Save intake
    if result.fqhc_intake:
        intake_id = str(uuid.uuid4())
        con.execute(
            """INSERT INTO fqhc_intake
               (id, run_id, entity_name, city, state, intake_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (run_id) DO UPDATE SET
                   entity_name = excluded.entity_name,
                   city        = excluded.city,
                   state       = excluded.state,
                   intake_json = excluded.intake_json,
                   created_at  = excluded.created_at""",
            [
                intake_id, result.run_id, result.entity_name,
                result.location.split(",")[0].strip(),
                result.location.split(",")[1].strip() if "," in result.location else "",
                json.dumps(result.fqhc_intake),
                datetime.utcnow().isoformat(),
            ],
        )

    # Save fact audit rows
    for i, row in enumerate(result.fqhc_fact_audit):
        con.execute(
            """INSERT INTO fqhc_fact_audit
               (id, run_id, row_order, claim, ai_representation, flag, severity, points, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                str(uuid.uuid4()), result.run_id, i,
                row.get("claim", ""), row.get("ai_representation", ""),
                row.get("flag", "◐"), row.get("severity", "minor"),
                row.get("points"), datetime.utcnow().isoformat(),
            ],
        )
    con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def analyze_fqhc(
    entity_name: str,
    city: str,
    state: str,
    fqhc_intake: Optional[dict] = None,
    aggregate: bool = False,
    site_roster: Optional[list[str]] = None,
    teaser_report: bool = False,
    output_dir: str | Path = "reports",
    on_event: Optional[Callable] = None,
    brand: str = "original",
    skip_pdf: bool = False,
    force_rerun: bool = False,
    briefing_variant: Optional[str] = None,
    override_today_lock: bool = False,
    report_title: Optional[str] = None,
) -> AnalysisResult:
    """Run a Community Health Edition AI Visibility analysis for an FQHC.

    Parallel to analyze_practice() — hospital and practice paths are not modified.
    Returns an AnalysisResult with entity_type='community_health', FQHC pillar
    scores, fact-audit rows, and missed-queries exhibit.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    from rich.console import Console
    console = Console(force_terminal=True, stderr=True)

    emit({"type": "phase", "name": "starting", "text": "Starting Community Health analysis"})
    init_db()

    # Cache logic
    from .db import get_recent_run
    _loc_key = f"{city}, {state}"
    if not override_today_lock:
        _today = get_recent_run(entity_name, _loc_key, days=0, entity_type="community_health")
        if _today:
            emit({"type": "phase", "name": "cached",
                  "text": f"Returning today's cached result for {entity_name}"})
            return AnalysisResult.model_validate_json(_today["result_json"])
        if not force_rerun:
            _cached = get_recent_run(entity_name, _loc_key, entity_type="community_health")
            if _cached:
                emit({"type": "phase", "name": "cached",
                      "text": f"Returning cached result for {entity_name}"})
                return AnalysisResult.model_validate_json(_cached["result_json"])

    client = _get_client()
    run_id = str(uuid.uuid4())

    # ── Phase 0: Gather evidence ──────────────────────────────────────────────
    emit({"type": "phase", "name": "evidence", "text": "Gathering FQHC evidence"})
    with console.status("[bold dark_sea_green4]Fetching Google data…[/bold dark_sea_green4]"):
        try:
            evidence_text, _indiv_read = _gather_individual_evidence(entity_name, city, state)
        except Exception as exc:
            console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
            evidence_text = (
                f"=== Evidence for {entity_name}, {city}, {state} ===\n"
                "Google data unavailable this run."
            )
            _indiv_read = None

    # HRSA lookup
    emit({"type": "phase", "name": "hrsa", "text": "Looking up HRSA directory"})
    with console.status("[bold dark_sea_green4]Fetching HRSA data…[/bold dark_sea_green4]"):
        try:
            hrsa_data = hrsa_mod.lookup(entity_name, city, state)
        except Exception:
            hrsa_data = {"found": False}

    if hrsa_data.get("found"):
        console.print(f"[green]✓[/green] HRSA match: {hrsa_data.get('health_center_name', entity_name)}")
    else:
        console.print(f"[yellow]⚠[/yellow] No HRSA match found — proceeding with public sources")

    console.print(f"[green]✓[/green] FQHC evidence: {entity_name}")

    system_prompt, user_prompt = build_fqhc_prompt(
        entity_name=entity_name,
        city=city,
        state=state,
        evidence_block=evidence_text,
        intake=fqhc_intake,
        hrsa_data=hrsa_data,
        aggregate=aggregate,
        site_roster=site_roster,
    )

    # ── Phase 1: Stream narrative ─────────────────────────────────────────────
    emit({"type": "phase", "name": "generating", "text": "Generating Community Health analysis"})
    report_markdown = _stream_narrative(client, system_prompt, user_prompt, emit, console)

    # ── Phase 2: Extract structured data ─────────────────────────────────────
    emit({"type": "phase", "name": "structured", "text": "Extracting structured data"})

    extraction_prompt = (
        "Extract the structured data from the completed Community Health Edition "
        "AI Visibility report below by calling submit_fqhc_result. "
        "Use the full report to populate all fields.\n\n"
        "KEY REQUIREMENTS:\n"
        "- fqhc_pillar_scores.mqcr_score = null (Round 1, no battery data)\n"
        "- fqhc_pillar_scores.multilingual_score = null (Round 1, no multilingual battery)\n"
        "- fqhc_pillar_scores.service_adjacent_score = required (AI-assessed)\n"
        "- fact_audit_rows: one row per attested fact; any ✗ must be MISSION-CRITICAL\n"
        "- missed_queries: up to 8 rows; prioritize eligibility-frame, then service-adjacent\n"
        "- rankings: single element, rank=1, with the health center\n\n"
        "--- REPORT ---\n"
        f"{report_markdown}\n--- END REPORT ---"
    )

    structured_data: dict = {}
    with console.status("[bold dark_sea_green4]Extracting FQHC structured data…[/bold dark_sea_green4]"):
        with client.messages.stream(
            model=_MODEL,
            max_tokens=16000,
            tools=[_FQHC_TOOL],
            tool_choice={"type": "tool", "name": "submit_fqhc_result"},
            messages=[{"role": "user", "content": extraction_prompt}],
        ) as stream:
            response = stream.get_final_message()

    if response.stop_reason == "max_tokens":
        console.print("[yellow]⚠[/yellow] FQHC extraction hit token limit — partial data only")
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_fqhc_result":
            structured_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    # Parse pillar scores
    raw_ps = structured_data.get("fqhc_pillar_scores") or {}
    pillar_scores = FqhcPillarScores(
        mqcr_score=raw_ps.get("mqcr_score"),
        multilingual_score=raw_ps.get("multilingual_score"),
        service_adjacent_score=raw_ps.get("service_adjacent_score"),
        eligibility_cost_accuracy=raw_ps.get("eligibility_cost_accuracy"),
        site_service_completeness=raw_ps.get("site_service_completeness"),
        experience_reputation=raw_ps.get("experience_reputation"),
        institutional_signals=raw_ps.get("institutional_signals"),
    )

    # Parse fact audit rows
    fact_audit_rows: list[dict] = [
        {
            "claim": r.get("claim", ""),
            "ai_representation": r.get("ai_representation", ""),
            "flag": r.get("flag", "◐"),
            "severity": r.get("severity", "minor"),
            "points": r.get("points"),
        }
        for r in structured_data.get("fact_audit_rows", [])
        if isinstance(r, dict)
    ]

    # Parse missed queries
    missed_queries: list[dict] = [
        {
            "query": q.get("query", ""),
            "language": q.get("language", "English"),
            "assistant": q.get("assistant", ""),
            "category": q.get("category", "general"),
        }
        for q in structured_data.get("missed_queries", [])[:8]
        if isinstance(q, dict)
    ]

    rankings = [
        _build_fqhc_provider(r)
        for r in structured_data.get("rankings", [])
    ]

    # ── Phase 3: Verify Google + score ───────────────────────────────────────
    emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
    for prov in rankings:
        _ground_and_score_fqhc(prov, city, state, pillar_scores)

    # Truncation gate: if all pillars None → block PDF
    skip_pdf_flag = skip_pdf
    if rankings and all(p.ai_visibility_score is None for p in rankings):
        emit({"type": "error", "text": (
            "Collection was too incomplete to produce a scored Community Health report. "
            "Ensure key FQHC evidence is available and retry."
        )})
        skip_pdf_flag = True

    console.print(f"[green]✓[/green] Scored FQHC ({entity_name})")

    disclaimer = _FQHC_DISCLAIMER
    _verdict = _clean(structured_data.get("ai_visibility_verdict", ""))
    _warn_snake_case(_verdict, context="fqhc/ai_visibility_verdict")

    result = AnalysisResult(
        run_id=run_id,
        location=_loc_key,
        individual_report=True,
        entity_name=entity_name,
        report_title=report_title,
        generated_at=date.today(),
        entity_type="community_health",
        rubric_version=_RUBRIC_VERSION,
        weighting_profile="community_health",
        market_overview=_clean(structured_data.get("market_overview", "")),
        ai_visibility_verdict=_verdict,
        coverage_note=f"Community Health Edition report: {entity_name}",
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
        teaser_report=teaser_report,
        fqhc_intake=fqhc_intake,
        fqhc_pillar_scores=pillar_scores,
        fqhc_fact_audit=fact_audit_rows,
        fqhc_missed_queries=missed_queries,
        fqhc_mqcr=None,  # Round 1: not yet computed
        briefing_variant=briefing_variant,
    )

    # ── Phase 4: Render PDF ───────────────────────────────────────────────────
    if not skip_pdf_flag:
        emit({"type": "phase", "name": "pdf", "text": "Rendering Community Health PDF"})
        try:
            from .fqhc_pdf import render_fqhc_pdf
            slug = _slug(entity_name)
            pdf_filename = f"{slug}-community-health-{run_id[:8]}.pdf"
            pdf_path = output_dir / pdf_filename
            render_fqhc_pdf(result, str(pdf_path), brand=brand)
            result.pdf_path = str(pdf_path)
            console.print(f"[green]✓[/green] PDF: {pdf_path}")
        except Exception as exc:
            console.print(f"[yellow]⚠[/yellow] PDF failed ({exc})")

    # ── Phase 5: Persist ─────────────────────────────────────────────────────
    emit({"type": "phase", "name": "saving", "text": "Saving to database"})
    _save_to_db(result)
    _save_fqhc_extras(result)

    # ── Phase 6: Briefing ─────────────────────────────────────────────────────
    if briefing_variant and result.rankings:
        emit({"type": "phase", "name": "briefing", "text": "Generating Pulse Briefing"})
        try:
            from .briefing import validate_briefing_inputs, extract as briefing_extract
            from .briefing_pdf import render_briefing_pdf
            missing = validate_briefing_inputs(result)
            if missing:
                result.briefing_skipped_reason = f"Missing: {', '.join(missing)}"
                console.print(f"[yellow]⚠[/yellow] Briefing skipped: {result.briefing_skipped_reason}")
            else:
                br = briefing_extract(result, briefing_variant)
                b_slug = _slug(entity_name)
                b_filename = f"{b_slug}-briefing-{briefing_variant}-{run_id[:8]}.pdf"
                b_path = output_dir / b_filename
                render_briefing_pdf(br, str(b_path), brand=brand)
                result.briefing_pdf_path = str(b_path)
                from .db import update_briefing_pdf_path
                update_briefing_pdf_path(run_id, str(b_path))
                console.print(f"[green]✓[/green] Briefing PDF: {b_path}")
        except Exception as exc:
            result.briefing_skipped_reason = str(exc)
            console.print(f"[yellow]⚠[/yellow] Briefing failed: {exc}")

    emit({"type": "done", "run_id": run_id})
    return result
