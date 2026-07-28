"""Network Pulse — analysis pipeline for multi-state hospital networks.

Two public entry points:
  extract_roster_from_url(url) → dict
  analyze_network(...)         → NetworkResult
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import anthropic

from .models import NetworkFacility, NetworkResult
from . import network_scoring
from .network_prompts import (
    build_roster_extraction_prompt,
    build_network_analysis_prompt,
    _ROSTER_TOOL,
    _ANALYSIS_TOOL,
)
from .db import get_connection, init_db

client = anthropic.Anthropic()

_MODEL = "claude-opus-4-8"


# ─────────────────────────────────────────────────────────────────────────────
# Roster extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_roster_from_url(url: str) -> dict:
    """Load a network locations page and extract the hospital roster via Claude.

    Uses Playwright to load the URL (waits for networkidle, 30s timeout),
    then extracts page text and a content snippet for Claude to parse.

    Returns:
        {
            "facilities": [{"name": str, "city": str, "state": str, "beds": int|None}, ...],
            "network_name": str,
            "total_found": int,
        }
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        # Plain text is cleaner for facility parsing
        body_text = page.inner_text("body")
        # Also grab raw HTML for structure hints (first 80k chars)
        html_content = page.content()[:80000]
        browser.close()

    # Combine: prefer the plain text, fall back to HTML if text is sparse
    content = body_text if len(body_text) > 500 else html_content

    system_prompt, user_prompt = build_roster_extraction_prompt(content, url)

    response = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        tools=[_ROSTER_TOOL],
        tool_choice={"type": "tool", "name": "submit_hospital_roster"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_hospital_roster":
            data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            return {
                "facilities": data.get("facilities", []),
                "network_name": data.get("network_name", ""),
                "total_found": data.get("total_found", 0),
            }

    return {"facilities": [], "network_name": "", "total_found": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Network analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_network(
    network_name: str,
    hq_location: str,
    source_url: str,
    facilities: list[dict],
    brand: str = "original",
    on_event: Optional[Callable] = None,
) -> NetworkResult:
    """Run a Network AI Visibility analysis for a multi-state hospital network.

    Args:
        network_name:  Network display name (e.g. "Atrium Health").
        hq_location:   Headquarters city/state (e.g. "Charlotte, NC").
        source_url:    URL of the roster source page.
        facilities:    List of facility dicts: {name, city, state, beds?}.
        brand:         Brand config key (passed to PDF renderer).
        on_event:      Optional callback receiving event dicts:
                         {"type": "phase", "name": str, "text": str}
                         {"type": "text",  "text": str}

    Returns:
        NetworkResult with all fields populated and pdf_path set.
    """
    init_db()

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    run_id = str(uuid.uuid4())

    # ── Phase: analyzing ─────────────────────────────────────────────────────
    emit({"type": "phase", "name": "analyzing",
          "text": f"Analyzing AI visibility for {network_name}"})

    system_prompt, user_prompt = build_network_analysis_prompt(
        network_name=network_name,
        hq_location=hq_location,
        facilities=facilities,
        source_url=source_url,
    )

    response = client.messages.create(
        model=_MODEL,
        max_tokens=8000,
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_network_result"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_network_result":
            raw = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    # ── Parse scores ─────────────────────────────────────────────────────────
    brand_score    = raw.get("brand_visibility_score")
    market_score   = raw.get("market_coverage_score")
    accuracy_score = raw.get("information_accuracy_score")

    composite = network_scoring.composite(brand_score, market_score, accuracy_score)
    letter, band = network_scoring.grade_band(composite)

    # ── Parse facilities ──────────────────────────────────────────────────────
    facility_objects: list[NetworkFacility] = []
    for fa in raw.get("facility_assessments", []):
        if not isinstance(fa, dict):
            continue
        fac_score = fa.get("ai_visibility_score")
        facility_objects.append(NetworkFacility(
            name=fa.get("name", ""),
            city=fa.get("city", ""),
            state=fa.get("state", ""),
            beds=next(
                (f.get("beds") for f in facilities
                 if f.get("name", "").lower() == fa.get("name", "").lower()),
                None,
            ),
            ai_visibility_score=fac_score,
            grade=network_scoring.facility_grade(fac_score),
            surfaced_for_local=fa.get("surfaced_for_local"),
            attributed_to_network=fa.get("attributed_to_network"),
            key_gap=fa.get("key_gap"),
        ))

    # Sort worst → best (None scores go to the bottom)
    facility_objects.sort(
        key=lambda f: f.ai_visibility_score if f.ai_visibility_score is not None else 999
    )

    # Derive states_covered from roster
    states_covered = sorted({f.get("state", "") for f in facilities if f.get("state")})

    result = NetworkResult(
        run_id=run_id,
        network_name=network_name,
        network_canonical_name=raw.get("network_canonical_name") or network_name,
        hq_location=hq_location,
        source_url=source_url,
        total_hospitals=len(facilities),
        states_covered=states_covered,
        generated_at=date.today(),
        ai_visibility_score=composite,
        brand_visibility_score=brand_score,
        market_coverage_score=market_score,
        information_accuracy_score=accuracy_score,
        grade=letter,
        grade_band=band,
        executive_summary=raw.get("executive_summary", ""),
        brand_visibility_narrative=raw.get("brand_visibility_narrative", ""),
        market_coverage_narrative=raw.get("market_coverage_narrative", ""),
        strategic_recommendations=[
            r for r in raw.get("strategic_recommendations", [])
            if isinstance(r, str)
        ],
        top_markets=[m for m in raw.get("top_markets", []) if isinstance(m, str)],
        gap_markets=[m for m in raw.get("gap_markets", []) if isinstance(m, str)],
        facilities=facility_objects,
    )

    # ── Phase: pdf ───────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "pdf",
          "text": "Rendering Network Pulse PDF"})
    try:
        from .network_pdf import render_network_pdf
        output_dir = Path("reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        slug = _slug(network_name)
        pdf_filename = f"{slug}-network-pulse-{run_id[:8]}.pdf"
        pdf_path = output_dir / pdf_filename
        render_network_pdf(result, str(pdf_path), brand=brand)
        result.pdf_path = str(pdf_path)
    except Exception as exc:
        emit({"type": "text", "text": f"\n⚠ PDF render failed: {exc}\n"})

    # ── Phase: saving ────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "saving", "text": "Saving to database"})
    _save_network_run(result)

    emit({"type": "done", "run_id": run_id})
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _save_network_run(result: NetworkResult) -> None:
    """Persist a NetworkResult to the network_runs table."""
    with get_connection() as con:
        con.execute(
            """INSERT INTO network_runs
               (run_id, network_name, hq_location, source_url, total_hospitals,
                ai_visibility_score, grade, generated_at, result_json, pdf_path, user_role)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (run_id) DO UPDATE SET
                   ai_visibility_score = excluded.ai_visibility_score,
                   grade               = excluded.grade,
                   result_json         = excluded.result_json,
                   pdf_path            = excluded.pdf_path""",
            [
                result.run_id,
                result.network_name,
                result.hq_location,
                result.source_url,
                result.total_hospitals,
                result.ai_visibility_score,
                result.grade,
                result.generated_at.isoformat(),
                result.model_dump_json(),
                result.pdf_path,
                "admin",
            ],
        )
