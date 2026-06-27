from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date
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
)
from .prompts import build_hospital_prompt, build_specialty_prompt

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
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "overall_rating": {"type": "string"},
                                },
                                "required": ["name", "overall_rating"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": [
                        "rank", "name", "affiliation_type", "overall_rating",
                        "weighting_profile", "tier_scores", "google_footprint",
                        "key_strengths", "notable_weaknesses", "best_suited_for",
                        "recommendation_summary",
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
    emit({"type": "phase", "name": "evidence", "text": "Gathering verified market evidence"})
    with console.status("[bold dark_sea_green4]Fetching CMS/NPPES census + Google reads…[/bold dark_sea_green4]"):
        try:
            evidence = _gather_evidence(client, city, state, specialty, emit)
            evidence_block = evidence.to_prompt_block()
        except Exception as exc:  # network/data failure → degrade gracefully
            console.print(f"[yellow]⚠[/yellow] Evidence gathering failed ({exc}); proceeding model-only.")
            evidence = MarketEvidence(location=f"{city}, {state}", mode="specialty" if specialty else "hospital",
                                      specialty=specialty, coverage_note="Verified evidence unavailable this run.")
            evidence_block = evidence.to_prompt_block()
    console.print(f"[green]✓[/green] Evidence: {evidence.coverage_note}")

    if specialty:
        system_prompt, user_prompt = build_specialty_prompt(city, state, specialty, evidence_block, aggregate=aggregate, radius_miles=radius_miles)
    else:
        system_prompt, user_prompt = build_hospital_prompt(city, state, evidence_block, aggregate=aggregate, radius_miles=radius_miles)

    # --- Phase 1: stream the narrative (with web search for currency) ---
    emit({"type": "phase", "name": "generating", "text": "Generating analysis"})
    report_markdown = _stream_narrative(client, system_prompt, user_prompt, emit, console)

    # --- Phase 2: extract structured data via tool use ---
    emit({"type": "phase", "name": "structured", "text": "Extracting structured data"})
    extraction_prompt = (
        "The following is a completed healthcare market analysis report. Extract "
        "the structured data by calling submit_analysis_result. Include every "
        "provider in the rankings, each with its four tier scores, weighting "
        "profile, Google footprint, third-party aggregate, and any disqualifiers.\n\n"
        f"--- REPORT ---\n{report_markdown}\n--- END REPORT ---"
    )
    structured_data: dict = {}
    with console.status("[bold dark_sea_green4]Extracting structured data…[/bold dark_sea_green4]"):
        response = client.messages.create(
            model=_MODEL,
            max_tokens=16000,
            tools=[_STRUCTURED_OUTPUT_TOOL],
            tool_choice={"type": "tool", "name": "submit_analysis_result"},
            messages=[{"role": "user", "content": extraction_prompt}],
        )
    if response.stop_reason == "max_tokens":
        console.print("[yellow]⚠[/yellow] Structured extraction hit token limit — partial data only")
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis_result":
            structured_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    run_profile = structured_data.get("weighting_profile") or scoring.classify_profile(specialty, evidence.mode)
    rankings = [_build_provider(r, run_profile) for r in structured_data.get("rankings", [])]

    # --- Phase 3: inject verified Google + system reputation + composite ---
    emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
    google_index = evidence.google_index()
    footprint_index = evidence.footprint_index()
    systems_done = 0
    for prov in rankings:
        is_system = bool(prov.consolidated_locations) or prov.size_category == SizeCategory.large
        do_system = (
            settings.enable_system_reputation
            and is_system
            and systems_done < _SYSTEM_REP_CAP
        )
        _ground_and_score(prov, city, state, google_index, footprint_index, do_system=do_system)
        if do_system and prov.google_footprint.system_aggregate.available:
            systems_done += 1
    capped_note = (
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
        generated_at=date.today(),
        weighting_profile=run_profile,
        market_overview=_clean(structured_data.get("market_overview", "")),
        ai_visibility_verdict=_clean(structured_data.get("ai_visibility_verdict", "")),
        coverage_note=evidence.coverage_note,
        top_recommendation=_clean(structured_data.get("top_recommendation", "")),
        practical_advice=[_clean(a) for a in structured_data.get("practical_advice", []) if isinstance(a, str)],
        disclaimer=disclaimer,
        rankings=rankings,
        report_markdown=report_markdown,
    )

    # Save markdown + PDF
    label = _slug(f"{city}-{state}-{specialty or 'hospitals'}")
    report_path = output_dir / f"{label}-{run_id[:8]}.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    console.print(f"[green]✓[/green] Report saved → [dim]{report_path}[/dim]")

    emit({"type": "phase", "name": "pdf", "text": "Rendering PDF"})
    with console.status("[bold dark_sea_green4]Rendering PDF…[/bold dark_sea_green4]"):
        from .pdf import render_pdf
        pdf_path = output_dir / f"{label}-{run_id[:8]}.pdf"
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
        affiliation_type=AffiliationType(r.get("affiliation_type", "unknown")),
        size_category=SizeCategory(r.get("size_category", "unknown")),
        physician_count=r.get("physician_count") or None,
        overall_rating=r.get("overall_rating", ""),
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
            listings_estimate=fp.get("listings_estimate", ""),
            rating_range=fp.get("rating_range", ""),
            consistency=fp.get("consistency", ""),
            gap_note=fp.get("gap_note", ""),
        ),
        third_party_aggregate=ThirdPartyAggregate(
            rating=tpa.get("rating"),
            sources=tpa.get("sources") or "Healthgrades, Vitals, WebMD",
            note=tpa.get("note", ""),
        ),
        disqualifiers=[d for d in r.get("disqualifiers", []) if isinstance(d, str)],
        key_strengths=r.get("key_strengths", []),
        notable_weaknesses=r.get("notable_weaknesses", []),
        best_suited_for=r.get("best_suited_for", ""),
        recommendation_summary=r.get("recommendation_summary", ""),
        consolidated_locations=[
            ConsolidatedLocation(name=loc["name"], overall_rating=loc.get("overall_rating", ""))
            for loc in r.get("consolidated_locations", [])
            if isinstance(loc, dict) and loc.get("name")
        ],
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
                (run_id, rank, name, affiliation_type, size_category, physician_count,
                 overall_rating, ai_visibility_score, weighting_profile, tier_scores,
                 google_footprint, third_party_aggregate, disqualifiers,
                 key_strengths, notable_weaknesses, best_suited_for,
                 recommendation_summary, consolidated_locations)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.run_id, p.rank, p.name, p.affiliation_type.value,
                p.size_category.value, p.physician_count, p.overall_rating,
                p.ai_visibility_score, p.weighting_profile,
                p.tier_scores.model_dump_json(),
                p.google_footprint.model_dump_json(),
                p.third_party_aggregate.model_dump_json(),
                json.dumps(p.disqualifiers),
                json.dumps(p.key_strengths), json.dumps(p.notable_weaknesses),
                p.best_suited_for, p.recommendation_summary,
                json.dumps([{"name": l.name, "overall_rating": l.overall_rating} for l in p.consolidated_locations]),
            ],
        )
    con.close()
