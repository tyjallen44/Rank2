from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Callable

import anthropic

from .config import settings
from .db import get_connection, init_db
from .models import AnalysisResult, Entity, RankedProvider
from .models import AffiliationType, ConsolidatedLocation
from .prompts import (
    build_hospital_prompt,
    build_specialty_prompt,
)

_MODEL = "claude-opus-4-8"

# Tool that forces Claude to emit structured JSON alongside the narrative.
_STRUCTURED_OUTPUT_TOOL = {
    "name": "submit_analysis_result",
    "description": (
        "Submit the structured analysis result. Call this exactly once after "
        "completing the full narrative report."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "top_recommendation": {
                "type": "string",
                "description": "One or two sentence top recommendation for patients.",
            },
            "practical_advice": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3–5 actionable bullet points for patients.",
            },
            "disclaimer": {
                "type": "string",
                "description": "Data limitations and disclaimer text.",
            },
            "rankings": {
                "type": "array",
                "description": (
                    "All ranked providers across both lists. Assign globally unique "
                    "sequential ranks (1, 2, 3, ...) — rank all independent practices "
                    "first in quality order, then continue numbering for "
                    "hospital/academic-affiliated groups. Set affiliation_type on each."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "rank": {"type": "integer"},
                        "name": {"type": "string"},
                        "affiliation_type": {
                            "type": "string",
                            "enum": ["independent", "hospital_affiliated", "unknown"],
                            "description": (
                                "independent = privately owned by physicians; "
                                "hospital_affiliated = employed by or owned by a "
                                "hospital, health system, or academic medical center."
                            ),
                        },
                        "physician_count": {
                            "type": "string",
                            "description": (
                                "Number of physicians in the practice, group, or "
                                "hospital department. Use a specific number ('12'), "
                                "an estimate ('~20'), or a range ('3–5'). Applies to "
                                "independent practices, hospital-affiliated groups, and "
                                "hospital departments alike. Use 'unknown' only if "
                                "truly not findable."
                            ),
                        },
                        "overall_rating": {"type": "string"},
                        "key_strengths": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "notable_weaknesses": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "best_suited_for": {"type": "string"},
                        "recommendation_summary": {"type": "string"},
                        "consolidated_locations": {
                            "type": "array",
                            "description": (
                                "When aggregation is enabled, list each constituent "
                                "location/campus that was merged into this parent-system "
                                "entry. Leave empty if this entry was not aggregated."
                            ),
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
                        "rank",
                        "name",
                        "affiliation_type",
                        "overall_rating",
                        "key_strengths",
                        "notable_weaknesses",
                        "best_suited_for",
                        "recommendation_summary",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "top_recommendation",
            "practical_advice",
            "disclaimer",
            "rankings",
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


def analyze_location(
    city: str,
    state: str,
    specialty: str | None = None,
    aggregate: bool = False,
    output_dir: str | Path = "reports",
    on_event: Callable | None = None,
) -> AnalysisResult:
    """
    Run a Claude-powered market analysis for a city/state location.

    If specialty is provided, runs a focused specialty analysis.
    Otherwise runs a broad hospital quality analysis.

    Saves the markdown report to output_dir and persists structured data
    to DuckDB. Returns the AnalysisResult.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    from rich.console import Console
    from rich.rule import Rule
    console = Console(force_terminal=True, stderr=True)

    emit({"type": "phase", "name": "starting", "text": "Starting analysis"})
    init_db()

    if specialty:
        system_prompt, user_prompt = build_specialty_prompt(city, state, specialty, aggregate=aggregate)
    else:
        system_prompt, user_prompt = build_hospital_prompt(city, state, aggregate=aggregate)

    client = _get_client()
    run_id = str(uuid.uuid4())

    emit({"type": "phase", "name": "generating", "text": "Generating analysis"})
    console.print(Rule("[dim]Generating analysis[/dim]", style="dark_sea_green4"))
    narrative_parts: list[str] = []
    with client.messages.stream(
        model=_MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            narrative_parts.append(text)
            print(text, end="", flush=True, file=sys.stderr)
            emit({"type": "text", "text": text})
    report_markdown = "".join(narrative_parts)
    print(file=sys.stderr)

    # --- Phase 2: extract structured data via tool use ---
    emit({"type": "phase", "name": "structured", "text": "Extracting structured data"})
    structured_data: dict = {}

    extraction_prompt = (
        "The following is a completed healthcare market analysis report. "
        "Your task is to extract the structured data from it by calling the "
        "submit_analysis_result tool. Include every provider or hospital "
        "mentioned in the rankings sections.\n\n"
        f"--- REPORT ---\n{report_markdown}\n--- END REPORT ---"
    )

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

    provider_count = len(structured_data.get("rankings", []))
    console.print(f"[green]✓[/green] Structured data extracted ({provider_count} providers)")

    def _clean(text: str) -> str:
        """Strip any stray XML parameter tags that sometimes leak into tool call values."""
        return re.sub(r"</?parameter[^>]*>", "", text).strip()

    # Build the result model
    rankings = [
        RankedProvider(
            rank=r["rank"],
            name=r["name"],
            affiliation_type=AffiliationType(r.get("affiliation_type", "unknown")),
            physician_count=r.get("physician_count") or None,
            overall_rating=r.get("overall_rating", ""),
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
        for r in structured_data.get("rankings", [])
    ]

    result = AnalysisResult(
        run_id=run_id,
        location=f"{city}, {state}",
        specialty=specialty,
        aggregate=aggregate,
        generated_at=date.today(),
        top_recommendation=_clean(structured_data.get("top_recommendation", "")),
        practical_advice=[_clean(a) for a in structured_data.get("practical_advice", []) if isinstance(a, str)],
        disclaimer=_clean(structured_data.get("disclaimer", "")),
        rankings=rankings,
        report_markdown=report_markdown,
    )

    # Save markdown report + PDF
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

    # Persist to DuckDB
    _save_to_db(result)

    emit({"type": "phase", "name": "done_item", "text": "Complete"})
    return result


def analyze_entities(
    entities: list[Entity],
    output_dir: str | Path = "reports",
    on_event: Callable | None = None,
) -> list[AnalysisResult]:
    """
    Run analysis for a list of entities loaded from a spreadsheet.

    Groups entities by (city, state, specialty) so that multiple entries
    for the same location/specialty share one analysis run.
    Returns one AnalysisResult per unique location+specialty combination.
    """
    # Group by (city, state, specialty)
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

        result = analyze_location(
            city=city,
            state=state,
            specialty=specialty,
            aggregate=False,
            output_dir=output_dir,
            on_event=on_event,
        )
        results.append(result)

    return results


def _save_to_db(result: AnalysisResult) -> None:
    con = get_connection()

    con.execute(
        """
        INSERT OR REPLACE INTO analysis_runs
            (run_id, location, specialty, aggregate, generated_at,
             top_recommendation, practical_advice, disclaimer, report_markdown,
             pdf_path, md_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            result.run_id,
            result.location,
            result.specialty,
            result.aggregate,
            result.generated_at.isoformat(),
            result.top_recommendation,
            json.dumps(result.practical_advice),
            result.disclaimer,
            result.report_markdown,
            result.pdf_path,
            result.md_path,
        ],
    )

    for provider in result.rankings:
        con.execute(
            """
            INSERT OR REPLACE INTO ranked_providers
                (run_id, rank, name, affiliation_type, physician_count, overall_rating,
                 key_strengths, notable_weaknesses,
                 best_suited_for, recommendation_summary, consolidated_locations)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.run_id,
                provider.rank,
                provider.name,
                provider.affiliation_type.value,
                provider.physician_count,
                provider.overall_rating,
                json.dumps(provider.key_strengths),
                json.dumps(provider.notable_weaknesses),
                provider.best_suited_for,
                provider.recommendation_summary,
                json.dumps([{"name": l.name, "overall_rating": l.overall_rating} for l in provider.consolidated_locations]),
            ],
        )

    con.close()
