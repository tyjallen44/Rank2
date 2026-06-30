from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import anthropic

from . import scoring
from .config import settings
from .data import evidence as evidence_mod
from .data import places, reputation
from .data.evidence import MarketEvidence, normalize_name
from .db import get_connection, init_db
from .models import (
    AffiliationType,
    AnalysisResult,
    ConsolidatedLocation,
    Entity,
    GoogleFootprint,
    GoogleFrontDoor,
    RankedProvider,
    SizeCategory,
    SystemAggregate,
    ThirdPartyAggregate,
    TierScores,
    UsNewsRanking,
)
from .prompts import build_hospital_prompt, build_specialty_prompt, build_individual_prompt

_MODEL = "claude-opus-4-8"

# Bound per-report cost: aggregate system-wide reputation for at most this many
# multi-location systems (each enumerates up to system_reputation_max_locations).
_SYSTEM_REP_CAP = 15

# The AI Visibility disclaimer note that must appear in every client report.
_AIVS_DISCLAIMER = (
    "The AI Visibility Score (0–100) reflects how favorably this provider "
    "surfaces to today's leading AI assistants — scored on the public sources "
    "those assistants state they weight when recommending providers, blended by "
    "each assistant's usage. It is a market-perception measure, not a "
    "clinical-quality verdict."
)

# Web search is restricted to authoritative healthcare sources so the currency
# layer (recognitions, rankings, recent events) never pulls a stray review number.
_WEB_SEARCH_DOMAINS = [
    "cms.gov", "medicare.gov", "usnews.com", "newsweek.com",
    "leapfroggroup.org", "qualitycheck.org", "healthgrades.com",
    "castleconnolly.com", "ncqa.org", "nursingworld.org",
]

# NPPES taxonomy_description expects clinical spellings; map a few common terms.
_TAXONOMY_ALIASES = {
    "orthopedics": "Orthopaedic",
    "orthopedic": "Orthopaedic",
    "orthopaedics": "Orthopaedic",
    "ent": "Otolaryngology",
    "cardiology": "Cardiovascular Disease",
    "gi": "Gastroenterology",
    "obgyn": "Obstetrics & Gynecology",
}

# Shared sub-schemas for the structured-extraction tool.
_TIER_SCORES_SCHEMA = {
    "type": "object",
    "description": "Each tier scored 0–100 from public data per the anchor rubric.",
    "properties": {
        "clinical_outcomes_safety": {"type": ["integer", "null"]},
        "credentials_recognition": {"type": ["integer", "null"]},
        "patient_experience_reviews": {"type": ["integer", "null"]},
        "access_fit": {"type": ["integer", "null"]},
    },
    "required": [
        "clinical_outcomes_safety",
        "credentials_recognition",
        "patient_experience_reviews",
        "access_fit",
    ],
    "additionalProperties": False,
}

_GOOGLE_FOOTPRINT_SCHEMA = {
    "type": "object",
    "properties": {
        "front_door": {
            "type": "object",
            "properties": {
                "rating": {"type": ["number", "null"]},
                "count": {"type": ["integer", "null"]},
                "recency": {"type": ["string", "null"]},
                "verified": {"type": "boolean"},
                "reason": {"type": ["string", "null"]},
            },
            "required": ["rating", "count", "verified"],
            "additionalProperties": False,
        },
        "listings_estimate": {"type": "string"},
        "rating_range": {"type": "string"},
        "consistency": {"type": "string"},
        "gap_note": {"type": "string"},
    },
    "required": ["front_door", "consistency", "gap_note"],
    "additionalProperties": False,
}

_THIRD_PARTY_SCHEMA = {
    "type": "object",
    "properties": {
        "rating": {"type": ["number", "null"]},
        "sources": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["note"],
    "additionalProperties": False,
}

_STRUCTURED_OUTPUT_TOOL = {
    "name": "submit_analysis_result",
    "description": (
        "Submit the structured analysis result. Call this exactly once after "
        "completing the full narrative report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "market_overview": {
                "type": "string",
                "description": "2–3 paragraph landscape summary (the Market Overview section).",
            },
            "ai_visibility_verdict": {
                "type": "string",
                "description": "2–3 sentence neutral analyst read on the market's AI visibility.",
            },
            "weighting_profile": {
                "type": "string",
                "enum": ["procedural", "relationship"],
                "description": "The AI Visibility weighting profile used for this market.",
            },
            "top_recommendation": {"type": "string"},
            "practical_advice": {"type": "array", "items": {"type": "string"}},
            "disclaimer": {"type": "string"},
            "rankings": {
                "type": "array",
                "description": (
                    "Every hospital, health system, practice, or group in the report — "
                    "include ALL, omit none. Assign globally sequential ranks (1,2,3,...). "
                    "Specialty: independent practices first, then hospital-affiliated. "
                    "Hospital: large/major first, then community."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "rank": {"type": "integer"},
                        "name": {"type": "string"},
                        "website_url": {"type": ["string", "null"], "description": "Primary public website URL for this provider (e.g. https://www.seton.net). Include https://. Set to null only if completely unknown."},
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
                            "enum": ["procedural", "relationship"],
                        },
                        "tier_scores": _TIER_SCORES_SCHEMA,
                        "google_footprint": _GOOGLE_FOOTPRINT_SCHEMA,
                        "third_party_aggregate": _THIRD_PARTY_SCHEMA,
                        "disqualifiers": {"type": "array", "items": {"type": "string"}},
                        "key_strengths": {"type": "array", "items": {"type": "string"}},
                        "notable_weaknesses": {"type": "array", "items": {"type": "string"}},
                        "best_suited_for": {"type": "string"},
                        "recommendation_summary": {"type": "string"},
                        "consolidated_locations": {
                            "type": "array",
                            "description": (
                                "Each constituent campus or affiliated location. Include the individual "
                                "Google rating and review count for each location where known. These "
                                "per-location signals should already be factored into the parent "
                                "provider's aggregate tier scores, patient voice, and footprint."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "overall_rating": {"type": "string"},
                                    "google_rating": {
                                        "type": ["number", "null"],
                                        "description": "This location's Google rating (verbatim from evidence or web search), or null if unknown.",
                                    },
                                    "google_review_count": {
                                        "type": ["integer", "null"],
                                        "description": "Number of Google reviews for this location, or null if unknown.",
                                    },
                                    "address": {
                                        "type": ["string", "null"],
                                        "description": "Street address or city of this location, or null.",
                                    },
                                },
                                "required": ["name", "overall_rating"],
                                "additionalProperties": False,
                            },
                        },
                        "patient_voice_summary": {
                            "type": "string",
                            "description": (
                                "2–3 sentence synthesis of what patients say: recurring themes "
                                "from Google reviews, Healthgrades, and HCAHPS patient experience "
                                "data. Never fabricate specific quotes."
                            ),
                        },
                        "leapfrog_grade": {
                            "type": ["string", "null"],
                            "description": "Leapfrog Hospital Safety Grade (A/B/C/D/F), 'not rated', or null.",
                        },
                        "accreditations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of active accreditations, e.g. ['Joint Commission', 'Magnet']. Empty if none confirmed.",
                        },
                        "cms_quality_highlights": {
                            "type": "string",
                            "description": (
                                "1–2 sentences on standout CMS quality measures: mortality/readmission "
                                "rates vs. national average, patient safety indicators. Empty string if no notable data."
                            ),
                        },
                        "cms_star_rating": {
                            "type": ["integer", "null"],
                            "description": (
                                "CMS Overall Hospital Quality Star Rating: integer 1–5 from CMS Care Compare. "
                                "Null if not a hospital or not rated by CMS."
                            ),
                        },
                        "us_news_rankings": {
                            "type": "array",
                            "description": (
                                "Every U.S. News & World Report recognition — nationally ranked or "
                                "high-performing. Include all categories. Empty array if none."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {
                                        "type": "string",
                                        "description": "e.g. 'Orthopedics', 'Cardiology & Heart Surgery', 'Best Hospitals Honor Roll'",
                                    },
                                    "rank": {
                                        "type": ["integer", "null"],
                                        "description": "Specific national rank number (e.g. 12), or null if high-performing only.",
                                    },
                                    "recognition_type": {
                                        "type": "string",
                                        "enum": ["nationally_ranked", "high_performing"],
                                    },
                                },
                                "required": ["category", "recognition_type"],
                                "additionalProperties": False,
                            },
                        },
                        "ai_says": {
                            "type": "string",
                            "description": (
                                "2–3 sentences capturing how AI assistants (Claude, ChatGPT, and Gemini) "
                                "currently describe this provider when a patient asks for a recommendation. "
                                "This is the core signal this report is designed to surface: what does AI "
                                "actually say about this organization to patients TODAY, based on all the "
                                "public signals those systems can see? Cover: (1) how they frame this "
                                "provider's identity and reputation, (2) what strengths or weaknesses they "
                                "emphasize based on available signals, and (3) any notable gaps between "
                                "the clinical record and what AI is currently able to surface. "
                                "Frame as: 'Claude, ChatGPT, and Gemini currently describe [name] as…' "
                                "If the provider has a thin or fragmented digital footprint that limits "
                                "AI visibility, state that directly: 'Due to a limited public digital "
                                "footprint, AI assistants currently have minimal information about [name] "
                                "and are unlikely to surface it confidently when patients ask for "
                                "recommendations in this category.'"
                            ),
                        },
                        "trauma_level": {
                            "type": ["string", "null"],
                            "description": "Verified trauma center designation: 'Level I', 'Level II', 'Level III', or null if not a trauma center or not applicable.",
                        },
                        "teaching_status": {
                            "type": ["string", "null"],
                            "enum": ["major", "minor", "not_teaching", None],
                            "description": (
                                "Teaching status: 'major' = medical school affiliation + active GME programs; "
                                "'minor' = some residency/fellowship programs; "
                                "'not_teaching' = community hospital with no GME; "
                                "null = not a hospital or unknown."
                            ),
                        },
                    },
                    "required": [
                        "rank", "name", "website_url", "affiliation_type", "overall_rating",
                        "weighting_profile", "tier_scores", "google_footprint",
                        "key_strengths", "notable_weaknesses", "best_suited_for",
                        "recommendation_summary", "patient_voice_summary",
                        "leapfrog_grade", "accreditations", "cms_quality_highlights",
                        "cms_star_rating", "us_news_rankings", "ai_says",
                        "trauma_level", "teaching_status",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "market_overview", "ai_visibility_verdict", "weighting_profile",
            "top_recommendation", "practical_advice", "disclaimer", "rankings",
        ],
        "additionalProperties": False,
    },
}


def _get_client() -> anthropic.Anthropic:
    if settings.anthropic_api_key:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return anthropic.Anthropic()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _clean(text: str) -> str:
    """Strip stray XML parameter tags that sometimes leak into tool call values."""
    return re.sub(r"</?parameter[^>]*>", "", text or "").strip()


def _resolve_metro_counties(client: anthropic.Anthropic, city: str, state: str) -> list[str] | None:
    """Ask Claude for the CMS county names that make up a metro (bounds the census).

    Returns county names without the 'County' suffix (CMS stores e.g. 'SALT LAKE'),
    or None on any failure so the caller falls back to a city-only census.
    """
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    f"For hospital-market analysis of the {city}, {state} metropolitan "
                    f"area, list the U.S. county names (as they appear in CMS data — "
                    f"UPPERCASE, no 'County' suffix, e.g. 'SALT LAKE') that make up the "
                    f"core metro. Return ONLY a JSON array of strings, nothing else."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return None
        counties = [str(c).strip() for c in json.loads(match.group(0)) if str(c).strip()]
        return counties or None
    except Exception:
        return None


def _gather_individual_evidence(entity_name: str, city: str, state: str) -> str:
    """Targeted Google Places evidence block for a single named entity."""
    read, footprint = places.fetch_provider(entity_name, city, state)
    return (
        f"=== Verified Google Evidence: {entity_name} ===\n"
        f"Location searched: {city}, {state}\n\n"
        f"Front-door rating: {read.as_line()}\n"
        f"Footprint sample:  {footprint.as_line()}\n"
    )


def _gather_evidence(
    client: anthropic.Anthropic,
    city: str,
    state: str,
    specialty: str | None,
    emit: Callable[[dict], None],
) -> MarketEvidence:
    """Fetch the verified census + Google reads that ground the scores."""
    if specialty:
        taxonomy = _TAXONOMY_ALIASES.get(specialty.strip().lower(), specialty)
        return evidence_mod.gather_specialty_context(city, state, specialty, taxonomy=taxonomy)
    counties = _resolve_metro_counties(client, city, state)
    return evidence_mod.gather_hospital_evidence(city, state, counties=counties)


def _web_search_tool() -> dict | None:
    """The Anthropic native web-search server tool, domain-restricted, or None."""
    if not settings.enable_web_search:
        return None
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max(1, settings.web_search_max_uses),
        "allowed_domains": _WEB_SEARCH_DOMAINS,
    }


def _stream_narrative(client, system_prompt, user_prompt, emit, console) -> str:
    """Stream the analysis narrative. Adds the native web-search tool for
    currency; if web search isn't enabled on the key, retries once without it."""
    from rich.rule import Rule

    def _run(tools: list) -> str:
        parts: list[str] = []
        with client.messages.stream(
            model=_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=tools,
        ) as stream:
            for text in stream.text_stream:
                parts.append(text)
                print(text, end="", flush=True, file=sys.stderr)
                emit({"type": "text", "text": text})
        print(file=sys.stderr)
        return "".join(parts)

    console.print(Rule("[dim]Generating analysis[/dim]", style="dark_sea_green4"))
    tool = _web_search_tool()
    if tool is None:
        return _run([])
    try:
        return _run([tool])
    except Exception as exc:  # web search not enabled for this key, etc.
        console.print(f"[yellow]⚠[/yellow] Web search unavailable ({str(exc)[:80]}); continuing without it.")
        emit({"type": "phase", "name": "generating", "text": "Generating analysis (no web search)"})
        return _run([])


def analyze_location(
    city: str,
    state: str,
    specialty: str | None = None,
    aggregate: bool = False,
    radius_miles: int | None = None,
    zip_code: str | None = None,
    patient_perspective: bool = False,
    teaser_report: bool = False,
    entity_name: str | None = None,
    individual_report: bool = False,
    output_dir: str | Path = "reports",
    on_event: Callable | None = None,
) -> AnalysisResult:
    """Run a Claude-powered, evidence-grounded AI Visibility market analysis.

    Flow: fetch verified evidence (CMS/NPPES census + real Google reads) →
    stream the narrative → extract structured tiers → inject the verified Google
    front door and re-anchor the Experience tier → compute the composite AI
    Visibility Score deterministically → render PDF + persist.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    from rich.console import Console
    console = Console(force_terminal=True, stderr=True)

    emit({"type": "phase", "name": "starting", "text": "Starting analysis"})
    init_db()
    client = _get_client()
    run_id = str(uuid.uuid4())

    # --- Phase 0: gather verified evidence ---
    emit({"type": "phase", "name": "evidence", "text": "Gathering verified evidence"})
    if individual_report and entity_name:
        with console.status("[bold dark_sea_green4]Fetching entity Google data…[/bold dark_sea_green4]"):
            try:
                evidence_text = _gather_individual_evidence(entity_name, city, state)
            except Exception as exc:
                console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
                evidence_text = f"=== Evidence for {entity_name}, {city}, {state} ===\nGoogle data unavailable this run."
        coverage_note_text = f"Individual entity report: {entity_name}"
        google_index: dict = {}
        footprint_index: dict = {}
        console.print(f"[green]✓[/green] Individual report: {entity_name}")
        system_prompt, user_prompt = build_individual_prompt(
            entity_name=entity_name, city=city, state=state,
            specialty=specialty, evidence_block=evidence_text, aggregate=aggregate,
        )
    else:
        with console.status("[bold dark_sea_green4]Fetching CMS/NPPES census + Google reads…[/bold dark_sea_green4]"):
            try:
                evidence = _gather_evidence(client, city, state, specialty, emit)
                evidence_text = evidence.to_prompt_block()
            except Exception as exc:
                console.print(f"[yellow]⚠[/yellow] Evidence gathering failed ({exc}); proceeding model-only.")
                evidence = MarketEvidence(
                    location=f"{city}, {state}", mode="specialty" if specialty else "hospital",
                    specialty=specialty, coverage_note="Verified evidence unavailable this run.",
                )
                evidence_text = evidence.to_prompt_block()
        coverage_note_text = evidence.coverage_note
        google_index = evidence.google_index()
        footprint_index = evidence.footprint_index()
        console.print(f"[green]✓[/green] Evidence: {coverage_note_text}")
        if specialty:
            system_prompt, user_prompt = build_specialty_prompt(city, state, specialty, evidence_text, aggregate=aggregate, radius_miles=radius_miles)
        else:
            system_prompt, user_prompt = build_hospital_prompt(city, state, evidence_text, aggregate=aggregate, radius_miles=radius_miles)

    # --- Phase 1: stream the narrative (with web search for currency) ---
    emit({"type": "phase", "name": "generating", "text": "Generating analysis"})
    report_markdown = _stream_narrative(client, system_prompt, user_prompt, emit, console)

    # --- Phase 2: extract structured data + deterministic scoring via tool use ---
    # temperature=0 makes this call deterministic: same evidence data → same tier
    # scores every time, regardless of how Phase 1 sampling varied.
    emit({"type": "phase", "name": "structured", "text": "Extracting structured data"})
    _individual_note = (
        "INDIVIDUAL REPORT — field mapping:\n"
        "• market_overview = the full text of the '### Organization Overview' section\n"
        "• ai_visibility_verdict = the '### AI Visibility Verdict' section\n"
        "• top_recommendation = the COMPLETE 2–3 paragraph prose of the "
        "'### AI Visibility Assessment' section. Extract ALL paragraphs of analysis "
        "text that appear after that heading. Do NOT put the entity name or any "
        "sub-heading here — only the analysis paragraphs (joined with newlines).\n"
        "• practical_advice = the bullet points from '### Key Takeaways'\n\n"
    ) if individual_report else ""
    extraction_prompt = (
        "Extract the structured data from the completed market analysis report below "
        "by calling submit_analysis_result. Include every provider in the rankings.\n\n"
        + _individual_note +
        "TIER SCORES — compute all four fresh from the verified evidence block AND the "
        "narrative report section. The narrative's findings about accreditations, trauma "
        "designations, quality programs, and program depth ARE authoritative evidence for "
        "scoring — use them freely. Do NOT copy tier scores written in the report text "
        "(those are from a different sampling pass).\n\n"
        "IMPORTANT: cms_star_rating is a SEPARATE field for the raw CMS number. "
        "The clinical_outcomes_safety tier score incorporates ALL quality signals "
        "— CMS stars, Leapfrog grade, HCAHPS, mortality/readmission rates, safety "
        "indicators, procedure volume, trauma designation, and teaching status. "
        "Set it to null ONLY if the provider is genuinely unknown with zero quality "
        "signals of any kind. Any of the following is sufficient for a non-null score: "
        "Level I/II trauma designation, Joint Commission accreditation, Magnet nursing "
        "recognition, academic medical center affiliation, HCAHPS data, Leapfrog grade, "
        "CMS star rating, or published clinical program depth.\n\n"
        "Anchor rubric:\n"
        "• Outcomes & Safety: CMS 5★→88, 4★→73, 3★→58, 2★→43, 1★→28. "
        "No CMS but Leapfrog A→85, B→72, C→58, D→44, F→32. "
        "No CMS/Leapfrog but HCAHPS above national avg→55–65, below→40–52. "
        "Level I trauma center (no CMS/Leapfrog confirmed)→62–72. "
        "Level II trauma center→55–65. "
        "Joint Commission–accredited hospital with major clinical programs, no published "
        "safety warnings→50–60. "
        "Specialty practice with strong procedure volume / outcomes→50–70. "
        "Apply Leapfrog modifier on top of CMS base: A adds ~8, F subtracts ~12.\n"
        "• Credentials & Recognition: U.S. News nationally ranked→85+ floor; "
        "high-performing→70+; academic medical center / Level I trauma / fellowship "
        "depth / Magnet→band up; board-certification alone is a floor (~60).\n"
        "• Experience & Reviews: Google 4.5★+/high volume→85+, 4.0–4.4→70–84, "
        "3.5–3.9→55–69, 3.0–3.4→40–54, <3.0 or thin/stale→<40. Fragmented or "
        "largely unclaimed footprint caps this tier even with a strong flagship.\n"
        "• Access & Fit: broad multi-payer network + many locations + active "
        "new-patient availability + telehealth→70+; limited access→40–55.\n\n"
        f"{evidence_text}\n\n"
        "--- REPORT (qualitative context — do NOT copy tier scores from here) ---\n"
        f"{report_markdown}\n--- END REPORT ---"
    )
    structured_data: dict = {}
    with console.status("[bold dark_sea_green4]Extracting structured data…[/bold dark_sea_green4]"):
        with client.messages.stream(
            model=_MODEL,
            max_tokens=32000,
            tools=[_STRUCTURED_OUTPUT_TOOL],
            tool_choice={"type": "tool", "name": "submit_analysis_result"},
            messages=[{"role": "user", "content": extraction_prompt}],
        ) as stream:
            response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        console.print("[yellow]⚠[/yellow] Structured extraction hit token limit — partial data only")
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis_result":
            structured_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    run_profile = structured_data.get("weighting_profile") or scoring.classify_profile(specialty, evidence.mode)
    rankings = [_build_provider(r, run_profile) for r in structured_data.get("rankings", [])]

    # Remove consolidated_locations whose names match a standalone ranked provider.
    # Claude sometimes lists a child hospital both as a sub-location of its parent
    # system AND as its own ranked entry. Strip the duplicate from the parent's list.
    _top_level = {p.name.lower().strip() for p in rankings}
    for prov in rankings:
        own = prov.name.lower().strip()
        prov.consolidated_locations = [
            loc for loc in prov.consolidated_locations
            if not any(
                other != own and (
                    loc.name.lower().strip() == other
                    or loc.name.lower().strip() in other
                    or other in loc.name.lower().strip()
                )
                for other in _top_level
            )
        ]

    # --- Phase 3: inject verified Google + system reputation + composite ---
    emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
    systems_done = 0
    for prov in rankings:
        if individual_report:
            do_system = aggregate and settings.enable_system_reputation
        else:
            is_system = bool(prov.consolidated_locations) or prov.size_category == SizeCategory.large
            do_system = (
                settings.enable_system_reputation
                and is_system
                and systems_done < _SYSTEM_REP_CAP
            )
        _ground_and_score(prov, city, state, google_index, footprint_index, do_system=do_system)
        if not individual_report and do_system and prov.google_footprint.system_aggregate.available:
            systems_done += 1
    capped_note = "" if individual_report else (
        f" (system-wide reputation capped at {_SYSTEM_REP_CAP} systems this report)"
        if settings.enable_system_reputation
        and sum(1 for p in rankings if p.consolidated_locations or p.size_category == SizeCategory.large) > _SYSTEM_REP_CAP
        else ""
    )
    console.print(f"[green]✓[/green] Scored {len(rankings)} providers ({systems_done} system aggregates){capped_note}")

    disclaimer = _clean(structured_data.get("disclaimer", ""))
    if "AI Visibility Score" not in disclaimer:
        disclaimer = (disclaimer + " " + _AIVS_DISCLAIMER).strip()

    result = AnalysisResult(
        run_id=run_id,
        location=f"{city}, {state}",
        specialty=specialty,
        aggregate=aggregate,
        zip_code=zip_code,
        radius_miles=radius_miles,
        patient_perspective=patient_perspective or teaser_report,
        teaser_report=teaser_report,
        individual_report=individual_report,
        entity_name=entity_name if individual_report else None,
        generated_at=date.today(),
        weighting_profile=run_profile,
        market_overview=_clean(structured_data.get("market_overview", "")),
        ai_visibility_verdict=_clean(structured_data.get("ai_visibility_verdict", "")),
        coverage_note=coverage_note_text,
        top_recommendation=_clean(structured_data.get("top_recommendation", "")),
        practical_advice=[_clean(a) for a in structured_data.get("practical_advice", []) if isinstance(a, str)],
        disclaimer=disclaimer,
        rankings=rankings,
        report_markdown=report_markdown,
    )

    # Save markdown + PDF
    _type = specialty.replace(" ", "-") if specialty else "Hospitals"
    _ts   = datetime.utcnow().strftime("%y%m%d-%H%M")
    _zip_part = f"-Zip_{zip_code}" if zip_code else ""
    if individual_report and entity_name:
        _entity_slug = _slug(entity_name)[:40]
        if teaser_report:
            _stem = f"{_entity_slug}_{city.replace(' ', '-')}_{state}_Individual-Summary-{_ts}"
        else:
            _stem = f"{_entity_slug}_{city.replace(' ', '-')}_{state}_Individual-{_ts}"
    elif teaser_report:
        _stem = f"{city.replace(' ', '-')}_{state}_{_type}_Summary-Report-{_ts}"
    elif patient_perspective:
        _stem = f"{city.replace(' ', '-')}_{state}_{_type}_Patient-Perspective{_zip_part}-{_ts}"
    else:
        _stem = f"{city.replace(' ', '-')}_{state}_{_type}{_zip_part}-{_ts}"
    report_path = output_dir / f"{_stem}.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    console.print(f"[green]✓[/green] Report saved → [dim]{report_path}[/dim]")

    emit({"type": "phase", "name": "pdf", "text": "Rendering PDF"})
    with console.status("[bold dark_sea_green4]Rendering PDF…[/bold dark_sea_green4]"):
        from .pdf import render_pdf
        pdf_path = output_dir / f"{_stem}.pdf"
        render_pdf(result, pdf_path)
    console.print(f"[green]✓[/green] PDF saved    → [dim]{pdf_path}[/dim]")

    result.pdf_path = str(pdf_path)
    result.md_path = str(report_path)
    _save_to_db(result)

    emit({"type": "phase", "name": "done_item", "text": "Complete"})
    return result


def _build_provider(r: dict, run_profile: str) -> RankedProvider:
    ts = r.get("tier_scores") or {}
    fp = r.get("google_footprint") or {}
    fd = fp.get("front_door") or {}
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
            sources=tpa.get("sources") or "Healthgrades, Vitals, WebMD",
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


def _ground_and_score(
    prov: RankedProvider,
    city: str,
    state: str,
    google_index: dict,
    footprint_index: dict,
    do_system: bool = False,
) -> None:
    """Override the front door with a verified Google read, optionally compute a
    system-wide weighted reputation, re-anchor the Experience tier from the best
    available signal, then compute the deterministic composite score."""
    key = normalize_name(prov.name)
    read = google_index.get(key)
    footprint = footprint_index.get(key)

    # If we don't already have a verified read for this name (e.g. the model
    # added a provider, or this is a specialty practice), fetch it on demand.
    if read is None or not read.verified:
        fetched_read, fetched_fp = places.fetch_provider(prov.name, city, state)
        if fetched_read.verified or read is None:
            read = fetched_read
        footprint = footprint or fetched_fp

    if read is not None and read.verified:
        prov.google_footprint.front_door = GoogleFrontDoor(
            rating=read.rating,
            count=read.review_count,
            recency=read.business_status or prov.google_footprint.front_door.recency,
            verified=True,
            reason=None,
        )
        band = scoring.experience_band(read.rating, read.review_count)
        if band is not None:
            prov.tier_scores.patient_experience_reviews = band
    elif read is not None:
        prov.google_footprint.front_door = GoogleFrontDoor(verified=False, reason=read.reason)

    # Populate the numeric footprint range from Places (model keeps the qualitative read).
    if footprint and footprint.listings_sampled > 1 and not prov.google_footprint.rating_range:
        prov.google_footprint.rating_range = footprint.as_line()

    # System-wide weighted reputation — the authoritative signal for a
    # multi-location system. When available, it re-anchors the Experience tier
    # (a single flagship listing under-represents a whole system).
    if do_system:
        rep = reputation.system_reputation(
            prov.name, state, max_locations=settings.system_reputation_max_locations
        )
        if rep.available:
            prov.google_footprint.system_aggregate = SystemAggregate(
                rating=rep.weighted_rating,
                total_reviews=rep.total_reviews,
                location_count=rep.location_count,
                confidence=rep.confidence,
                capped=rep.capped,
            )
            band = scoring.experience_band(rep.weighted_rating, rep.total_reviews)
            if band is not None:
                prov.tier_scores.patient_experience_reviews = band

    profile = prov.weighting_profile or "procedural"
    prov.ai_visibility_score = scoring.composite_score(prov.tier_scores.as_dict(), profile)


def analyze_entities(
    entities: list[Entity],
    output_dir: str | Path = "reports",
    on_event: Callable | None = None,
) -> list[AnalysisResult]:
    """Run analysis for a list of entities loaded from a spreadsheet.

    Groups entities by (city, state, specialty) so multiple rows for the same
    location/specialty share one analysis run.
    """
    groups: dict[tuple[str, str, str | None], list[Entity]] = {}
    for entity in entities:
        key = (
            (entity.city or "").strip().title(),
            (entity.state or "").strip().upper(),
            entity.specialty,
        )
        groups.setdefault(key, []).append(entity)

    from rich.console import Console
    from rich.panel import Panel
    console = Console(force_terminal=True, stderr=True)

    results: list[AnalysisResult] = []
    total = len(groups)
    for idx, ((city, state, specialty), group) in enumerate(groups.items(), 1):
        label = f"{city}, {state}"
        if specialty:
            label += f"  •  {specialty}"
        console.print()
        console.print(Panel(
            f"[bold white]{label}[/bold white]\n[dim]Market {idx} of {total}[/dim]",
            border_style="dark_sea_green4",
            padding=(0, 2),
        ))
        results.append(analyze_location(
            city=city, state=state, specialty=specialty,
            aggregate=False, output_dir=output_dir, on_event=on_event,
        ))
    return results


def _save_to_db(result: AnalysisResult) -> None:
    con = get_connection()

    con.execute(
        """
        INSERT OR REPLACE INTO analysis_runs
            (run_id, location, specialty, aggregate, generated_at,
             weighting_profile, market_overview, ai_visibility_verdict, coverage_note,
             top_recommendation, practical_advice, disclaimer, report_markdown,
             pdf_path, md_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            result.run_id, result.location, result.specialty, result.aggregate,
            result.generated_at.isoformat(), result.weighting_profile,
            result.market_overview, result.ai_visibility_verdict, result.coverage_note,
            result.top_recommendation, json.dumps(result.practical_advice),
            result.disclaimer, result.report_markdown, result.pdf_path, result.md_path,
        ],
    )

    for p in result.rankings:
        con.execute(
            """
            INSERT OR REPLACE INTO ranked_providers
                (run_id, rank, name, website_url, affiliation_type, size_category, physician_count,
                 overall_rating, ai_visibility_score, weighting_profile, tier_scores,
                 google_footprint, third_party_aggregate, disqualifiers,
                 key_strengths, notable_weaknesses, best_suited_for,
                 recommendation_summary, consolidated_locations,
                 patient_voice_summary, leapfrog_grade, accreditations, cms_quality_highlights,
                 cms_star_rating, us_news_rankings, ai_says, trauma_level, teaching_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.run_id, p.rank, p.name, p.website_url, p.affiliation_type.value,
                p.size_category.value, p.physician_count, p.overall_rating,
                p.ai_visibility_score, p.weighting_profile,
                p.tier_scores.model_dump_json(),
                p.google_footprint.model_dump_json(),
                p.third_party_aggregate.model_dump_json(),
                json.dumps(p.disqualifiers),
                json.dumps(p.key_strengths), json.dumps(p.notable_weaknesses),
                p.best_suited_for, p.recommendation_summary,
                json.dumps([{
                    "name": l.name, "overall_rating": l.overall_rating,
                    "google_rating": l.google_rating,
                    "google_review_count": l.google_review_count,
                    "address": l.address,
                } for l in p.consolidated_locations]),
                p.patient_voice_summary, p.leapfrog_grade,
                json.dumps(p.accreditations), p.cms_quality_highlights,
                p.cms_star_rating,
                json.dumps([{"category": u.category, "rank": u.rank, "recognition_type": u.recognition_type} for u in p.us_news_rankings]),
                p.ai_says, p.trauma_level, p.teaching_status,
            ],
        )
    con.close()
