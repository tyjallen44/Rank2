from __future__ import annotations

import json
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Callable

import anthropic

from .config import settings
from .db import get_connection, init_db
from .models import AnalysisResult, Entity, RankedProvider
from .prompts import (
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_PROMPT,
    build_hospital_prompt,
    build_specialty_prompt,
)

_MODEL = "claude-opus-4-8"
_OPENAI_MODEL = "gpt-4o"

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
                        "surgeon_count": {
                            "type": "string",
                            "description": (
                                "Number of surgeons/physicians in the group. "
                                "Use a specific number ('12'), an estimate ('~20'), "
                                "or a range ('3–5'). Use 'unknown' only if truly "
                                "not findable."
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
                    },
                    "required": [
                        "rank",
                        "name",
                        "affiliation_type",
                        "surgeon_count",
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


def _openai_available() -> bool:
    return bool(settings.openai_api_key)


def _run_claude_analysis(client: anthropic.Anthropic, system_prompt: str, user_prompt: str) -> str:
    response = client.messages.create(
        model=_MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in response.content if hasattr(b, "text"))


def _run_openai_analysis(system_prompt: str, user_prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=_OPENAI_MODEL,
        max_tokens=8000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content or ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def analyze_location(
    city: str,
    state: str,
    specialty: str | None = None,
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
        system_prompt, user_prompt = build_specialty_prompt(city, state, specialty)
    else:
        system_prompt, user_prompt = build_hospital_prompt(city, state)

    client = _get_client()
    run_id = str(uuid.uuid4())

    if _openai_available():
        # --- Phase 1: gather Claude + GPT-4o in parallel ---
        emit({"type": "phase", "name": "generating", "text": "Gathering perspectives from Claude & GPT-4o"})
        console.print(Rule("[dim]Gathering Claude & GPT-4o analyses in parallel…[/dim]", style="dark_sea_green4"))

        claude_narrative = openai_narrative = ""
        with ThreadPoolExecutor(max_workers=2) as pool:
            claude_future = pool.submit(_run_claude_analysis, client, system_prompt, user_prompt)
            openai_future = pool.submit(_run_openai_analysis, system_prompt, user_prompt)
            for future in as_completed([claude_future, openai_future]):
                if future is claude_future:
                    claude_narrative = future.result()
                    console.print("[green]✓[/green] Claude analysis complete")
                else:
                    openai_narrative = future.result()
                    console.print("[green]✓[/green] GPT-4o analysis complete")

        # --- Phase 2: stream synthesis ---
        emit({"type": "phase", "name": "synthesizing", "text": "Synthesizing perspectives"})
        console.print(Rule("[dim]Synthesizing perspectives…[/dim]", style="dark_sea_green4"))
        synthesis_user = SYNTHESIS_USER_PROMPT.format(
            claude_analysis=claude_narrative,
            gpt_analysis=openai_narrative,
        )
        narrative_parts: list[str] = []
        with client.messages.stream(
            model=_MODEL,
            max_tokens=8000,
            system=SYNTHESIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": synthesis_user}],
        ) as stream:
            for text in stream.text_stream:
                narrative_parts.append(text)
                print(text, end="", flush=True, file=sys.stderr)
                emit({"type": "text", "text": text})
        report_markdown = "".join(narrative_parts)
        print(file=sys.stderr)

    else:
        # --- Claude-only path (no OpenAI key configured) ---
        emit({"type": "phase", "name": "generating", "text": "Generating analysis"})
        console.print(Rule("[dim]Generating analysis[/dim]", style="dark_sea_green4"))
        narrative_parts = []
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

    with console.status("[bold dark_sea_green4]Extracting structured data…[/bold dark_sea_green4]"):
        response = client.messages.create(
            model=_MODEL,
            max_tokens=16000,
            system=system_prompt,
            tools=[_STRUCTURED_OUTPUT_TOOL],
            tool_choice={"type": "tool", "name": "submit_analysis_result"},
            messages=[
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": report_markdown},
                {
                    "role": "user",
                    "content": (
                        "Now call the submit_analysis_result tool with the structured "
                        "version of your analysis above. Include every provider mentioned "
                        "in both the Independent Practices and Hospital & Academic-Affiliated "
                        "sections."
                    ),
                },
            ],
        )

    if response.stop_reason == "max_tokens":
        console.print("[yellow]⚠[/yellow] Structured extraction hit token limit — partial data only")

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis_result":
            structured_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    provider_count = len(structured_data.get("rankings", []))
    console.print(f"[green]✓[/green] Structured data extracted ({provider_count} providers)")

    # Build the result model
    from .models import AffiliationType
    rankings = [
        RankedProvider(
            rank=r["rank"],
            name=r["name"],
            affiliation_type=AffiliationType(r.get("affiliation_type", "unknown")),
            surgeon_count=r.get("surgeon_count") or None,
            overall_rating=r.get("overall_rating", ""),
            key_strengths=r.get("key_strengths", []),
            notable_weaknesses=r.get("notable_weaknesses", []),
            best_suited_for=r.get("best_suited_for", ""),
            recommendation_summary=r.get("recommendation_summary", ""),
        )
        for r in structured_data.get("rankings", [])
    ]

    result = AnalysisResult(
        run_id=run_id,
        location=f"{city}, {state}",
        specialty=specialty,
        generated_at=date.today(),
        top_recommendation=structured_data.get("top_recommendation", ""),
        practical_advice=structured_data.get("practical_advice", []),
        disclaimer=structured_data.get("disclaimer", ""),
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
            (run_id, location, specialty, generated_at,
             top_recommendation, practical_advice, disclaimer, report_markdown,
             pdf_path, md_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            result.run_id,
            result.location,
            result.specialty,
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
                (run_id, rank, name, affiliation_type, surgeon_count, overall_rating,
                 key_strengths, notable_weaknesses,
                 best_suited_for, recommendation_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.run_id,
                provider.rank,
                provider.name,
                provider.affiliation_type.value,
                provider.surgeon_count,
                provider.overall_rating,
                json.dumps(provider.key_strengths),
                json.dumps(provider.notable_weaknesses),
                provider.best_suited_for,
                provider.recommendation_summary,
            ],
        )

    con.close()
