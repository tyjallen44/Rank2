"""Pulse Briefing PDF renderer — HTML template + Playwright → PDF."""
from __future__ import annotations

import html
import os
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .briefing import BriefingResult

_e = html.escape


def _css() -> str:
    return """
    @page { size: letter; margin: 0.65in 0.70in 0.65in 0.70in; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
        font-size: 9pt;
        color: #1c1c1e;
        background: #fff;
        line-height: 1.45;
    }
    .header {
        border-bottom: 2.5px solid #1a6b72;
        padding-bottom: 8px;
        margin-bottom: 14px;
    }
    .header-top {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
    }
    .entity-name {
        font-size: 14pt;
        font-weight: 700;
        color: #1a6b72;
        letter-spacing: -0.3px;
    }
    .meta-block {
        font-size: 7.5pt;
        color: #5e6a6b;
        text-align: right;
        line-height: 1.6;
    }
    .internal-banner {
        background: #fff4cd;
        border: 1px solid #d4a017;
        border-radius: 3px;
        padding: 4px 10px;
        font-size: 7.5pt;
        color: #7a5900;
        margin-top: 6px;
        text-align: center;
        font-style: italic;
    }
    .score-band {
        display: flex;
        align-items: baseline;
        gap: 12px;
        margin-bottom: 4px;
    }
    .score-number {
        font-size: 28pt;
        font-weight: 800;
        color: #1a6b72;
        line-height: 1;
    }
    .score-grade {
        font-size: 18pt;
        font-weight: 700;
        color: #2d8b92;
    }
    .score-band-label {
        font-size: 9pt;
        color: #5e6a6b;
        font-style: italic;
    }
    .hook-block {
        background: #f2f8f8;
        border-left: 4px solid #1a6b72;
        padding: 8px 12px;
        margin-bottom: 14px;
        font-size: 9.5pt;
        font-style: italic;
        color: #1c1c1e;
        border-radius: 0 3px 3px 0;
    }
    h2 {
        font-size: 8pt;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        color: #1a6b72;
        font-weight: 700;
        margin-bottom: 6px;
        margin-top: 12px;
        border-bottom: 1px solid #dde9ea;
        padding-bottom: 2px;
    }
    .findings-grid {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 8px;
        margin-bottom: 4px;
    }
    .finding-card {
        border: 1px solid #cde0e2;
        border-radius: 4px;
        padding: 8px 10px;
    }
    .finding-card.strength, .finding-card.relative_strength {
        border-left: 4px solid #2d8b92;
    }
    .finding-card.gap {
        border-left: 4px solid #c07a30;
    }
    .finding-type-badge {
        font-size: 6.5pt;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 5px;
    }
    .finding-type-badge.strength,
    .finding-type-badge.relative_strength { color: #2d8b92; }
    .finding-type-badge.gap               { color: #c07a30; }
    .demo-fallback {
        background: #f7f7f7;
        border: 1px solid #d0d5d6;
        border-radius: 4px;
        padding: 8px 12px;
        font-size: 8pt;
        color: #888;
        font-style: italic;
    }
    .finding-label {
        font-size: 7pt;
        text-transform: uppercase;
        color: #8a9a9b;
        font-weight: 600;
        letter-spacing: 0.3px;
        margin-top: 5px;
        margin-bottom: 1px;
    }
    .finding-text {
        font-size: 8.5pt;
        color: #1c1c1e;
        line-height: 1.4;
    }
    .finding-say {
        font-size: 8.5pt;
        color: #1a3d40;
        font-style: italic;
        line-height: 1.4;
    }
    .finding-source {
        font-size: 6.5pt;
        color: #9aabb0;
        margin-top: 4px;
    }
    .demo-block {
        background: #f7f7f7;
        border: 1px solid #d0d5d6;
        border-radius: 4px;
        padding: 8px 12px;
        margin-bottom: 4px;
    }
    .demo-prompt {
        font-family: 'Courier New', Courier, monospace;
        font-size: 8pt;
        color: #2a4d50;
        background: #eaf3f4;
        padding: 4px 8px;
        border-radius: 3px;
        margin: 4px 0;
        display: block;
    }
    .demo-outcome {
        font-size: 8pt;
        color: #444;
        margin-top: 3px;
    }
    .objections-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
    }
    .objection-card {
        border: 1px solid #dde9ea;
        border-radius: 4px;
        padding: 8px 10px;
    }
    .objection-q {
        font-size: 8pt;
        font-weight: 700;
        color: #333;
        margin-bottom: 3px;
        font-style: italic;
    }
    .objection-a {
        font-size: 8pt;
        color: #444;
        line-height: 1.4;
    }
    .ask-block {
        background: #1a6b72;
        color: #fff;
        border-radius: 4px;
        padding: 9px 14px;
        font-size: 9pt;
        line-height: 1.5;
    }
    .footer {
        margin-top: 14px;
        border-top: 1px solid #dde9ea;
        padding-top: 5px;
        font-size: 6.5pt;
        color: #9aabb0;
        display: flex;
        justify-content: space-between;
    }
    """


def render_briefing_html(br: "BriefingResult") -> str:
    today = date.today().isoformat()
    variant_label = "Sales Edition" if br.variant == "sales" else "Customer Success Edition"

    prior_line = ""
    if br.prior_score is not None and br.prior_date:
        prior_line = f"Prior run: {br.prior_date} · Score: {br.prior_score}"

    # ── Header ──────────────────────────────────────────────────────────────
    header_html = f"""
    <div class="header">
      <div class="header-top">
        <div class="entity-name">{_e(br.entity_name)}</div>
        <div class="meta-block">
          Pulse Briefing &mdash; {_e(variant_label)}<br>
          Report date: {_e(br.report_date)}<br>
          Run ID: {_e(br.run_id)}<br>
          Generated: {_e(today)}
          {('<br>' + _e(prior_line)) if prior_line else ''}
        </div>
      </div>
      <div class="internal-banner">
        Companion to Pulse Diagnostic [{_e(br.run_id)}] &mdash;
        Internal use only &mdash; Do not distribute to customer
      </div>
    </div>
    """

    # ── Score block ──────────────────────────────────────────────────────────
    score_html = f"""
    <div class="score-band">
      <span class="score-number">{br.score}</span>
      <span class="score-grade">{_e(br.grade)}</span>
      <span class="score-band-label">{_e(br.band_descriptor)}</span>
    </div>
    """

    # ── Hook ────────────────────────────────────────────────────────────────
    hook_html = f'<div class="hook-block">{_e(br.hook)}</div>'

    # ── Findings ─────────────────────────────────────────────────────────────
    finding_cards = []
    for f in br.findings:
        is_strength_type = f.candidate_type in ("strength", "relative_strength")
        badge_cls   = f.candidate_type if f.candidate_type in ("strength", "relative_strength", "gap") else "gap"
        if f.candidate_type == "strength":
            badge_label = "&#10003; Strength"
        elif f.candidate_type == "relative_strength":
            badge_label = "&#10003; Relative Strength"
        else:
            badge_label = "&#9650; Gap"
        card = f"""
        <div class="finding-card {badge_cls}">
          <div class="finding-type-badge {badge_cls}">{badge_label}</div>
          <div class="finding-label">What we found</div>
          <div class="finding-text">{_e(f.what)}</div>
          <div class="finding-label">Why it matters</div>
          <div class="finding-text">{_e(f.why)}</div>
          <div class="finding-label">What to say</div>
          <div class="finding-say">&ldquo;{_e(f.say)}&rdquo;</div>
          <div class="finding-source">{_e(f.source_ref)}</div>
        </div>
        """
        finding_cards.append(card)

    findings_html = f"""
    <h2>3 Key Findings</h2>
    <div class="findings-grid">{"".join(finding_cards)}</div>
    """

    # ── Demo (always renders — fallback when battery not yet run) ──────────
    if br.demo:
        pct_label = f"{round(br.demo.dominant_pct * 100)}%"
        demo_html = f"""
        <h2>Live Demo Prompt</h2>
        <div class="demo-block">
          <span class="demo-prompt">{_e(br.demo.prompt_text)}</span>
          <div class="demo-outcome">
            Expected outcome ({pct_label} of runs): {_e(br.demo.outcome_description)}
          </div>
        </div>
        """
    else:
        demo_html = """
        <h2>Live Demo Prompt</h2>
        <div class="demo-fallback">
          Live demo not available for this run.
        </div>
        """

    # ── Objections (always renders — falls back to generic library) ──────────
    obj_cards = []
    for obj in br.objections:
        card = f"""
        <div class="objection-card">
          <div class="objection-q">&#8220;{_e(obj['objection'])}&#8221;</div>
          <div class="objection-a">{_e(obj['rebuttal'])}</div>
        </div>
        """
        obj_cards.append(card)

    objections_html = f"""
    <h2>Objection Handling</h2>
    <div class="objections-grid">{"".join(obj_cards)}</div>
    """

    # ── Ask ──────────────────────────────────────────────────────────────────
    ask_html = f"""
    <h2>The Ask</h2>
    <div class="ask-block">{_e(br.ask)}</div>
    """

    # ── Footer ───────────────────────────────────────────────────────────────
    footer_html = f"""
    <div class="footer">
      <span>Pulse Briefing &bull; {_e(br.variant.upper())} &bull; {_e(br.entity_name)}</span>
      <span>Generated {_e(today)} &bull; Internal use only</span>
    </div>
    """

    body = (
        header_html
        + score_html
        + hook_html
        + findings_html
        + demo_html
        + objections_html
        + ask_html
        + footer_html
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>{_css()}</style>
</head>
<body>
{body}
</body>
</html>"""


def render_briefing_pdf(br: "BriefingResult", output_path: str) -> str:
    """Render br to PDF at output_path using Playwright. Returns output_path."""
    from playwright.sync_api import sync_playwright

    html_content = render_briefing_html(br)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(html_content, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="Letter",
            print_background=True,
        )
        browser.close()

    return output_path
