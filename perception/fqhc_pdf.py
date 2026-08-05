"""FQHC Community Health Edition PDF renderer.

Produces a branded PDF for Community Health (FQHC) AI Visibility reports.
Uses the same Playwright render path as pdf.py and reuses its brand configs,
color tokens, and HTML helpers.
"""
from __future__ import annotations

import html as _html_lib
import re
from pathlib import Path
from typing import Optional

from .models import AnalysisResult, FqhcPillarScores
from .fqhc_scoring import (
    FQHC_PILLAR_LABELS, FQHC_PILLAR5_LABELS, FQHC_SUB_WEIGHTS,
    composite as _fqhc_composite,
)
from .scoring import grade_from_score
# Reuse brand configs and low-level helpers from pdf.py
from .pdf import _BRAND_CONFIGS, _e, _strip_md, _TEASER_PHONE, _TEASER_DEMO_URL

_BLUR_CTA_FQHC = (
    "Access the complete Community Health Edition report — AI Visibility Verdict, "
    "all five pillars, Missed Queries Exhibit, and personalized Improvement Opportunities."
)


def render_fqhc_pdf(
    result: AnalysisResult,
    pdf_path: str,
    brand: str = "original",
) -> None:
    """Render a Community Health Edition report to a branded PDF."""
    from playwright.sync_api import sync_playwright

    cfg = _BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"])
    html = _build_fqhc_html(result, cfg)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True,
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font-family:Arial,sans-serif;'
                'font-size:9px;color:#7a9095;text-align:center;padding:0 0 8px 0">'
                'Page <span class="pageNumber"></span> of <span class="totalPages"></span>'
                "</div>"
            ),
        )
        browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# HTML builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_fqhc_html(result: AnalysisResult, brand_cfg: dict) -> str:
    primary = brand_cfg.get("primary", "#0F4146")
    pale    = brand_cfg.get("pale",    "#EEF7F1")
    accent  = brand_cfg.get("accent",  "#80F8E4")

    prov = result.rankings[0] if result.rankings else None
    score = prov.ai_visibility_score if prov else None
    grade, band = grade_from_score(score)
    entity = _e(result.report_title or result.entity_name or "Community Health Center")
    location = _e(result.location or "")
    generated = str(result.generated_at or "")
    ps = result.fqhc_pillar_scores or FqhcPillarScores()

    # Load battery rows from DB if the battery has been run
    battery_rows: list[dict] = []
    if result.fqhc_mqcr is not None and result.run_id:
        try:
            from .db import get_connection
            with get_connection() as _con:
                _rows = _con.execute(
                    "SELECT query, category, language, assistant, surfaced "
                    "FROM fqhc_battery_runs WHERE fqhc_run_id = ? ORDER BY created_at, id",
                    [result.run_id],
                ).fetchall()
                battery_rows = [
                    {"query": r[0], "category": r[1], "language": r[2],
                     "assistant": r[3], "surfaced": r[4]}
                    for r in _rows
                ]
        except Exception:
            pass

    css = _fqhc_css(primary, pale, accent)
    cover = _cover_block(entity, location, generated, score, grade, band, result, primary, pale, accent)
    scorecard = _pillar_scorecard(ps, primary, pale)
    mqcr_block = _mqcr_block(result, primary, accent)
    verdict = _verdict_block(result)
    pillar1 = _pillar1_block(result, ps, primary)
    missed_queries = _query_results_block(result, battery_rows, primary)
    pillar2 = _pillar2_block(result, ps, primary)
    pillar3 = _pillar3_block(result, ps, prov, primary)
    pillar4 = _pillar4_block(result, ps, prov, primary)
    pillar5 = _pillar5_block(result, ps, prov, primary)
    assessment = _assessment_block(result, primary)
    appendix = _appendix_block(primary, pale)
    disclaimer = _disclaimer_block(result, primary)

    logo_html = brand_cfg.get("logo_html") or _default_logo_html()
    css_overrides = brand_cfg.get("css_overrides", "")

    if result.teaser_report:
        main_body = f"""<div class="teaser-blur-wrapper">
  <div class="teaser-blur-content">
{verdict}
{pillar1}
{missed_queries}
{pillar2}
{pillar3}
{pillar4}
{pillar5}
{assessment}
  </div>
  <div class="teaser-blur-overlay">
    <div class="blur-lock">&#128274;</div>
    <div class="blur-cta-heading">Full analysis available upon request</div>
    <div class="blur-cta-sub">{_BLUR_CTA_FQHC}</div>
    <div class="blur-cta-actions">
      <span class="blur-phone">{_TEASER_PHONE}</span>
      &nbsp;&nbsp;&middot;&nbsp;&nbsp;
      <a href="{_TEASER_DEMO_URL}" class="blur-demo-link">Book a Demo &rarr;</a>
    </div>
  </div>
</div>"""
    else:
        main_body = f"""
{verdict}
{pillar1}
{missed_queries}
{pillar2}
{pillar3}
{pillar4}
{pillar5}
{assessment}"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pulse — Community Health Edition — {entity}</title>
<style>
{css}
{css_overrides}
</style>
</head>
<body>
{cover}
<div class="report-body">
{scorecard}
{mqcr_block}
{main_body}
{appendix}
{disclaimer}
</div>
</body>
</html>"""


def _default_logo_html() -> str:
    logo_path = Path(__file__).parent / "assets" / "logo-white.svg"
    if logo_path.exists():
        import base64
        data = base64.b64encode(logo_path.read_bytes()).decode()
        return f'<img src="data:image/svg+xml;base64,{data}" class="cover-logo-img" alt="Pulse">'
    return '<div class="cover-logo-text">Pulse</div>'


def _fqhc_css(primary: str, pale: str, accent: str) -> str:
    return f"""
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; color: #1a2b2e; background: #fff; }}
    .cover {{ background: {primary}; color: #fff; padding: 56px 48px 48px; min-height: 100vh; display: flex; flex-direction: column; }}
    .cover-edition {{ font-size: 9pt; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.65); margin-bottom: 12px; }}
    .cover-title {{ font-size: 26pt; font-weight: bold; margin-bottom: 8px; line-height: 1.15; }}
    .cover-location {{ font-size: 12pt; color: rgba(255,255,255,0.75); margin-bottom: 32px; }}
    .cover-score-row {{ display: flex; gap: 32px; align-items: flex-start; margin-top: 24px; }}
    .cover-score-box {{ background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; padding: 20px 28px; text-align: center; min-width: 140px; }}
    .cover-score-num {{ font-size: 48pt; font-weight: bold; color: {accent}; line-height: 1; }}
    .cover-score-out-of {{ font-size: 20pt; font-weight: 700; color: rgba(255,255,255,0.32); vertical-align: top; display: inline-block; padding-top: 10px; margin-left: 2px; }}
    .cover-score-lbl {{ font-size: 8pt; text-transform: uppercase; letter-spacing: 0.1em; color: rgba(255,255,255,0.55); margin-top: 4px; margin-bottom: 12px; }}
    .cover-quartile-badge {{ border-radius: 0 6px 6px 0; padding: 9px 16px 9px 12px; background: rgba(255,255,255,0.07); text-align: left; }}
    .cover-quartile-badge-lbl {{ font-size: 6pt; text-transform: uppercase; letter-spacing: 0.12em; color: rgba(255,255,255,0.45); font-weight: 700; margin-bottom: 3px; }}
    .cover-quartile-badge-val {{ font-size: 16pt; font-weight: 800; line-height: 1; }}
    .cover-mqcr-box {{ background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; padding: 20px 28px; text-align: center; min-width: 180px; }}
    .cover-mqcr-num {{ font-size: 34pt; font-weight: bold; color: #fff; line-height: 1; }}
    .cover-mqcr-pending {{ font-size: 12pt; color: rgba(255,255,255,0.55); font-style: italic; margin: 10px 0; }}
    .cover-mqcr-caption {{ font-size: 8.5pt; color: rgba(255,255,255,0.7); margin-top: 6px; line-height: 1.4; max-width: 200px; }}
    .cover-pillar-strip {{ display: flex; gap: 10px; margin-top: 40px; }}
    .cover-pillar-item {{ flex: 1; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding: 14px 8px; text-align: center; }}
    .cover-pillar-score {{ font-size: 20pt; font-weight: bold; color: {accent}; line-height: 1; }}
    .cover-pillar-name {{ font-size: 7.5pt; color: rgba(255,255,255,0.65); margin-top: 5px; line-height: 1.3; }}
    .cover-pillar-pts {{ font-size: 7pt; color: rgba(255,255,255,0.35); margin-top: 3px; }}
    .cover-verdict-strip {{ margin-top: 24px; padding: 16px 20px; background: rgba(255,255,255,0.06); border-left: 3px solid {accent}; border-radius: 0 6px 6px 0; }}
    .cover-verdict-strip p {{ font-size: 9.5pt; color: rgba(255,255,255,0.8); line-height: 1.55; }}
    .cover-focus {{ margin-top: 28px; }}
    .cover-focus-label {{ font-size: 8pt; text-transform: uppercase; letter-spacing: 0.1em; color: rgba(255,255,255,0.45); margin-bottom: 12px; }}
    .cover-focus-item {{ display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }}
    .cover-focus-bullet {{ width: 6px; height: 6px; border-radius: 50%; background: {accent}; margin-top: 4px; flex-shrink: 0; }}
    .cover-focus-text {{ font-size: 9.5pt; color: rgba(255,255,255,0.75); line-height: 1.45; }}
    .cover-kf {{ margin-top: 32px; display: flex; gap: 12px; flex-wrap: wrap; }}
    .cover-kf-pill {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); border-radius: 20px; padding: 6px 14px; font-size: 8.5pt; color: rgba(255,255,255,0.7); white-space: nowrap; }}
    .cover-kf-val {{ color: {accent}; font-weight: bold; }}
    .cover-meta {{ margin-top: auto; padding-top: 24px; border-top: 1px solid rgba(255,255,255,0.2); font-size: 8.5pt; color: rgba(255,255,255,0.5); display: flex; justify-content: space-between; }}
    .report-body {{ padding: 36px 48px; }}
    h2 {{ font-size: 13pt; color: {primary}; margin: 28px 0 10px; border-bottom: 2px solid {primary}; padding-bottom: 4px; }}
    h3 {{ font-size: 11pt; color: {primary}; margin: 18px 0 6px; }}
    p {{ margin-bottom: 10px; line-height: 1.55; }}
    .pillar-scorecard {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 16px 0 24px; }}
    .pillar-card {{ border: 1px solid #d0e4e7; border-radius: 6px; padding: 12px 10px; text-align: center; background: {pale}; }}
    .pillar-card-score {{ font-size: 22pt; font-weight: bold; color: {primary}; }}
    .pillar-card-label {{ font-size: 7.5pt; color: #4a6a70; margin-top: 4px; line-height: 1.3; }}
    .pillar-card-weight {{ font-size: 7pt; color: #7a9095; margin-top: 2px; }}
    .pillar-card.not-scored .pillar-card-score {{ color: #9bafb3; font-size: 16pt; }}
    .mqcr-block {{ background: {primary}; color: #fff; border-radius: 8px; padding: 20px 24px; margin: 20px 0; }}
    .mqcr-headline {{ font-size: 13pt; font-weight: bold; margin-bottom: 6px; }}
    .mqcr-caption {{ font-size: 9.5pt; color: rgba(255,255,255,0.8); line-height: 1.5; }}
    .mqcr-pending {{ font-size: 10pt; color: rgba(255,255,255,0.65); font-style: italic; }}
    .fact-audit-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 9.5pt; }}
    .fact-audit-table th {{ background: {primary}; color: #fff; padding: 8px 10px; text-align: left; font-size: 8.5pt; }}
    .fact-audit-table td {{ padding: 8px 10px; border-bottom: 1px solid #d0e4e7; vertical-align: top; }}
    .fact-audit-table tr:nth-child(even) td {{ background: {pale}; }}
    .flag-pass {{ color: #1a8744; font-weight: bold; font-size: 12pt; }}
    .flag-partial {{ color: #b87a00; font-weight: bold; font-size: 12pt; }}
    .flag-fail {{ color: #c02020; font-weight: bold; font-size: 12pt; }}
    .severity-mc {{ color: #c02020; font-weight: bold; font-size: 8pt; text-transform: uppercase; }}
    .severity-notable {{ color: #b87a00; font-size: 8pt; }}
    .severity-minor {{ color: #4a6a70; font-size: 8pt; }}
    .missed-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 9.5pt; }}
    .missed-table th {{ background: {primary}; color: #fff; padding: 8px 10px; text-align: left; font-size: 8.5pt; }}
    .missed-table td {{ padding: 8px 10px; border-bottom: 1px solid #d0e4e7; vertical-align: top; }}
    .missed-table tr:nth-child(even) td {{ background: {pale}; }}
    .missed-query-text {{ font-style: italic; color: #1a2b2e; }}
    .missed-cat {{ font-size: 8pt; color: #7a9095; text-transform: uppercase; letter-spacing: 0.05em; }}
    .site-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 9.5pt; }}
    .site-table th {{ background: {primary}; color: #fff; padding: 8px 10px; text-align: left; font-size: 8.5pt; }}
    .site-table td {{ padding: 8px 10px; border-bottom: 1px solid #d0e4e7; }}
    .site-table tr:nth-child(even) td {{ background: {pale}; }}
    /* Repeat table headers across page breaks */
    thead {{ display: table-header-group; }}
    tfoot {{ display: table-footer-group; }}
    .improvement-section {{ margin: 16px 0; }}
    .improvement-section h3 {{ font-size: 10.5pt; color: {primary}; margin-bottom: 6px; }}
    .improvement-section ul {{ margin-left: 20px; }}
    .improvement-section li {{ margin-bottom: 5px; line-height: 1.5; font-size: 9.5pt; }}
    .verdict-box {{ background: {pale}; border-left: 4px solid {primary}; padding: 14px 18px; margin: 12px 0; border-radius: 0 6px 6px 0; }}
    .verdict-box p {{ font-size: 10.5pt; margin: 0; line-height: 1.6; }}
    .appendix {{ background: #f8f8f8; border: 1px solid #d0e4e7; border-radius: 6px; padding: 20px 24px; margin: 24px 0; }}
    .appendix h3 {{ font-size: 10pt; color: {primary}; }}
    .appendix table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 8.5pt; }}
    .appendix th {{ background: {primary}; color: #fff; padding: 6px 8px; text-align: left; }}
    .appendix td {{ padding: 6px 8px; border-bottom: 1px solid #d0e4e7; }}
    .disclaimer-text {{ font-size: 7.5pt; color: #7a9095; line-height: 1.5; margin-top: 24px; padding-top: 12px; border-top: 1px solid #d0e4e7; }}
    .mission-critical-banner {{ background: #fdf0f0; border: 1px solid #e0a0a0; border-radius: 6px; padding: 10px 14px; margin: 8px 0; font-size: 9pt; color: #8b1c1c; }}
    .teaser-blur-wrapper {{ position: relative; overflow: hidden; border-radius: 6px; }}
    .teaser-blur-content {{ filter: blur(2px); user-select: none; pointer-events: none; }}
    .teaser-blur-overlay {{
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(238,247,241,0.26);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 14px 20px;
      border-radius: 6px;
      border: 1.5px dashed {accent};
    }}
    .teaser-blur-overlay .blur-lock,
    .teaser-blur-overlay .blur-cta-heading,
    .teaser-blur-overlay .blur-cta-sub,
    .teaser-blur-overlay .blur-cta-actions {{
      background: rgba(238,247,241,0.92);
      border-radius: 4px;
      padding: 2px 8px;
    }}
    .blur-lock {{ font-size: 18pt; margin-bottom: 5px; }}
    .blur-cta-heading {{ font-size: 10pt; font-weight: 700; color: {primary}; margin-bottom: 5px; }}
    .blur-cta-sub {{ font-size: 7.5pt; color: #3a5a60; line-height: 1.45; margin-bottom: 9px; max-width: 380px; }}
    .blur-cta-actions {{ font-size: 9pt; font-weight: 600; color: {primary}; }}
    .blur-phone {{ font-weight: 700; }}
    .blur-demo-link {{ color: #0a5c70; font-weight: 700; text-decoration: underline; }}
    """


def _cover_block(
    entity: str, location: str, generated: str,
    score: Optional[int], grade: str, band: str,
    result: AnalysisResult, primary: str, pale: str, accent: str,
) -> str:
    score_display = str(score) if score is not None else "—"
    q_color = {
        "Q1": "#6fcf97", "Q2": "#56b8d9", "Q3": "#f2994a", "Q4": "#eb5757",
    }.get(grade, accent)
    mqcr = result.fqhc_mqcr
    if mqcr is not None:
        mqcr_block_html = f"""
        <div class="cover-mqcr-box">
          <div style="font-size:8pt;text-transform:uppercase;letter-spacing:.1em;color:rgba(255,255,255,.65);margin-bottom:6px">Mission Query Capture</div>
          <div class="cover-mqcr-num">{int(round(mqcr * 100))}%</div>
          <div class="cover-mqcr-caption">When patients ask AI where they can afford care, {result.entity_name or 'this center'} appears in {int(round(mqcr * 100))}% of tested queries.</div>
        </div>"""
    else:
        mqcr_block_html = f"""
        <div class="cover-mqcr-box">
          <div style="font-size:8pt;text-transform:uppercase;letter-spacing:.1em;color:rgba(255,255,255,.65);margin-bottom:6px">Mission Query Capture</div>
          <div class="cover-mqcr-pending">Not yet<br>computed</div>
          <div class="cover-mqcr-caption">Requires battery run — this will be the leading metric at re-assessment.</div>
        </div>"""

    # ── 5-pillar strip ────────────────────────────────────────────────────
    from .fqhc_scoring import pillar1_score as _p1_score
    ps = result.fqhc_pillar_scores or FqhcPillarScores()
    sub = ps.as_dict()
    pillar_defs = [
        ("Access &\nFindability",          _p1_score(sub),                          "25 pts"),
        ("Eligibility &\nCost Accuracy",   sub.get("eligibility_cost_accuracy"),    "25 pts"),
        ("Site & Service\nCompleteness",   sub.get("site_service_completeness"),    "20 pts"),
        ("Experience &\nReputation",       sub.get("experience_reputation"),        "20 pts"),
        ("Institutional\nSignals",         sub.get("institutional_signals"),        "10 pts"),
    ]
    pillar_items = ""
    for name, val, pts in pillar_defs:
        val_html = str(val) if val is not None else "—"
        name_html = _e(name).replace("\n", "<br>")
        pillar_items += (
            f'<div class="cover-pillar-item">'
            f'<div class="cover-pillar-score">{val_html}</div>'
            f'<div class="cover-pillar-name">{name_html}</div>'
            f'<div class="cover-pillar-pts">{pts}</div>'
            f'</div>'
        )

    # ── Focus areas (top 3 improvement section titles) ───────────────────
    focus_items_html = ""
    if result.improvement_sections:
        for sec in result.improvement_sections[:3]:
            title = _e(sec.title)
            desc  = _e(sec.description) if sec.description else ""
            focus_items_html += (
                f'<div class="cover-focus-item">'
                f'<div class="cover-focus-bullet"></div>'
                f'<div class="cover-focus-text"><strong>{title}</strong>'
                + (f' — {desc}' if desc else '') +
                f'</div></div>'
            )
    elif result.practical_advice:
        for advice in result.practical_advice[:3]:
            focus_items_html += (
                f'<div class="cover-focus-item">'
                f'<div class="cover-focus-bullet"></div>'
                f'<div class="cover-focus-text">{_e(advice)}</div></div>'
            )
    focus_block = (
        f'<div class="cover-focus">'
        f'<div class="cover-focus-label">Priority focus areas</div>'
        f'{focus_items_html}</div>'
        if focus_items_html else ""
    )

    # ── Key-facts pills (fills whitespace before footer) ─────────────────
    prov_for_cover = result.rankings[0] if result.rankings else None
    kf_pills = []
    site_count = len(prov_for_cover.consolidated_locations) if prov_for_cover else 0
    if site_count:
        kf_pills.append(f'<span class="cover-kf-pill"><span class="cover-kf-val">{site_count}</span> HRSA sites</span>')
    if prov_for_cover and prov_for_cover.google_footprint.front_door.rating is not None:
        rating = prov_for_cover.google_footprint.front_door.rating
        count  = prov_for_cover.google_footprint.front_door.count or 0
        kf_pills.append(f'<span class="cover-kf-pill"><span class="cover-kf-val">{rating:.1f}★</span> Google ({count} reviews)</span>')
    intake = result.fqhc_intake or {}
    if intake.get("is_330"):
        kf_pills.append('<span class="cover-kf-pill"><span class="cover-kf-val">✓</span> Section 330 Grantee</span>')
    if intake.get("sliding_fee_scale"):
        kf_pills.append('<span class="cover-kf-pill"><span class="cover-kf-val">✓</span> Sliding-Fee Scale</span>')
    if intake.get("accepts_uninsured"):
        kf_pills.append('<span class="cover-kf-pill"><span class="cover-kf-val">✓</span> Uninsured Welcome</span>')
    kf_block = (
        f'<div class="cover-kf">{"".join(kf_pills)}</div>'
        if kf_pills else ""
    )

    return f"""
<div class="cover">
  <div class="cover-edition">Pulse — Community Health Edition v1.0</div>
  <div class="cover-title">{entity}</div>
  <div class="cover-location">{location}</div>
  <div class="cover-score-row">
    <div class="cover-score-box">
      <div style="line-height:1">
        <span class="cover-score-num">{score_display}</span><span class="cover-score-out-of">/100</span>
      </div>
      <div class="cover-score-lbl">AI Visibility Score</div>
      <div class="cover-quartile-badge" style="border-left:3px solid {q_color}">
        <div class="cover-quartile-badge-lbl">National Quartile</div>
        <div class="cover-quartile-badge-val" style="color:{q_color}">{grade} <span style="font-size:9pt;font-weight:500;color:rgba(255,255,255,0.7)">&middot;&nbsp;{band}</span></div>
      </div>
    </div>
    {mqcr_block_html}
  </div>
  <div class="cover-pillar-strip">{pillar_items}</div>
  {focus_block}
  {kf_block}
  <div class="cover-meta">
    <span>Generated {generated} &nbsp;·&nbsp; Community Health Center</span>
    <span>Pulse AI Visibility Report &nbsp;·&nbsp; Community Health Edition v1.0</span>
  </div>
</div>"""


def _pillar_scorecard(ps: FqhcPillarScores, primary: str, pale: str) -> str:
    sub = ps.as_dict()

    # Compute Pillar 1 display score
    from .fqhc_scoring import pillar1_score as _p1
    p1 = _p1(sub)

    pillars = [
        ("Pillar 1\nAccess &\nFindability", p1, "25 pts"),
        ("Pillar 2\nEligibility &\nCost Accuracy", sub.get("eligibility_cost_accuracy"), "25 pts"),
        ("Pillar 3\nSite & Service\nCompleteness", sub.get("site_service_completeness"), "20 pts"),
        ("Pillar 4\nExperience &\nReputation", sub.get("experience_reputation"), "20 pts"),
        ("Pillar 5\nInstitutional\nSignals", sub.get("institutional_signals"), "10 pts"),
    ]

    cards = []
    for label, val, weight in pillars:
        if val is not None:
            score_html = f'<div class="pillar-card-score">{val}</div>'
            cls = "pillar-card"
        else:
            score_html = '<div class="pillar-card-score">—</div>'
            cls = "pillar-card not-scored"
        label_html = _e(label).replace("\n", "<br>")
        cards.append(
            f'<div class="{cls}">{score_html}'
            f'<div class="pillar-card-label">{label_html}</div>'
            f'<div class="pillar-card-weight">{weight}</div></div>'
        )

    return f"""
<h2>AI Visibility Scorecard</h2>
<div class="pillar-scorecard">{"".join(cards)}</div>"""


def _mqcr_block(result: AnalysisResult, primary: str, accent: str) -> str:
    mqcr = result.fqhc_mqcr
    entity_esc = _e(result.entity_name or "this center")
    if mqcr is not None:
        pct = int(round(mqcr * 100))
        caption = (
            f"When patients ask AI assistants where they can afford to be seen, "
            f"{entity_esc} appears in {pct}% of tested queries."
        )
        headline = f"MISSION QUERY CAPTURE: {pct}%"
        body = f'<div class="mqcr-caption">{_e(caption)}</div>'
    else:
        headline = "MISSION QUERY CAPTURE: Not Yet Computed"
        body = (
            '<div class="mqcr-pending">'
            "The Mission Query Capture Rate (MQCR) for this center has not yet been measured. "
            "MQCR is the leading metric for the Community Health Edition — it will be "
            "computed when the battery run is completed. This number is recomputed from the "
            "same logged query battery at re-assessment to demonstrate engagement value."
            "</div>"
        )

    return f"""
<div class="mqcr-block">
  <div class="mqcr-headline">{_e(headline)}</div>
  {body}
</div>"""


def _verdict_block(result: AnalysisResult) -> str:
    verdict = _strip_md(result.ai_visibility_verdict or "")
    if not verdict:
        return ""

    if result.fqhc_mqcr is not None:
        pct = int(round(result.fqhc_mqcr * 100))
        entity = result.entity_name or "this center"
        # Remove the boilerplate Round-1 sentence the LLM was instructed to write
        verdict = re.sub(
            r"The Mission Query Capture Rate for [^.—]+(?:—[^.]+)?\.?\s*",
            "",
            verdict,
            count=1,
            flags=re.IGNORECASE,
        ).lstrip()
        # Prepend the actual measured MQCR as the new opening sentence
        verdict = (
            f"The Mission Query Capture Rate for {entity} is {pct}% — "
            f"it appeared in {pct}% of tested mission-frame queries. "
        ) + verdict

    return f"""
<h2>AI Visibility Verdict</h2>
<div class="verdict-box"><p>{_e(verdict)}</p></div>"""


def _pillar1_block(result: AnalysisResult, ps: FqhcPillarScores, primary: str) -> str:
    sub = ps.as_dict()
    lines = []

    svc_adj = sub.get("service_adjacent_score")
    lines.append(f"<h3>Service-Adjacent Findability — sub-score: {svc_adj if svc_adj is not None else '—'}/100 (5 pts)</h3>")
    lines.append("<p>Measures surfacing for service-adjacent queries: MAT, behavioral health sliding scale, WIC-adjacent, mobile clinics.</p>")

    mqcr = result.fqhc_mqcr
    if mqcr is not None:
        from .fqhc_scoring import mqcr_to_score
        mqcr_sub = mqcr_to_score(mqcr)
        pct = int(round(mqcr * 100))
        city = (result.location or "").split(",")[0].strip()
        not_directed = 10 - int(round(mqcr * 10))
        lines.append(
            f"<h3>Mission Query Capture (MQCR) — sub-score: {mqcr_sub}/100 (12 pts)</h3>"
        )
        lines.append(
            f"<p>Battery result: <strong>{pct}%</strong> ({int(round(mqcr * 10))}/10 queries surfaced). "
            f"<strong>{not_directed} of 10 patients</strong> asking an AI assistant for affordable care "
            f"in {_e(city) if city else 'this area'} were not directed here.</p>"
        )
        lines.append("<p>See the Query Results Exhibit below for the full query-by-query breakdown.</p>")
    else:
        lines.append("<h3>Mission Query Capture (MQCR) — 12 pts — Requires Battery Run</h3>")
        lines.append("<p>MQCR will be computed from logged mission-frame battery runs. See the Missed Queries exhibit below for representative queries where this center should appear.</p>")

    multi_score = ps.multilingual_score
    if multi_score is not None:
        lines.append(f"<h3>Multilingual Capture — sub-score: {multi_score}/100 (8 pts)</h3>")
        lines.append(
            "<p>Spanish-language battery result: see Query Results Exhibit for the three Spanish "
            "queries and whether this center surfaced in AI responses to Spanish-speaking patients.</p>"
        )
    else:
        lines.append("<h3>Multilingual Capture — 8 pts — Requires Battery Run</h3>")
        lines.append("<p>Multilingual capture rate will be assessed when the MQCR battery runs (Spanish-language queries included).</p>")

    return f"<h2>Pillar 1 — Access &amp; Findability (25 pts)</h2>\n" + "\n".join(lines)


def _query_results_block(result: AnalysisResult, battery_rows: list, primary: str) -> str:
    """Query Results Exhibit when battery has been run; Missed Queries Exhibit otherwise."""
    if battery_rows:
        en_rows = [r for r in battery_rows if r.get("language", "en") == "en"]
        es_rows = [r for r in battery_rows if r.get("language") == "es"]
        en_surfaced = sum(1 for r in en_rows if r.get("surfaced"))
        es_surfaced = sum(1 for r in es_rows if r.get("surfaced"))
        en_total = len(en_rows)
        es_total = len(es_rows)
        en_pct = int(round(en_surfaced / en_total * 100)) if en_total else 0

        def _row_html(r: dict) -> str:
            surfaced = r.get("surfaced")
            icon = '<span style="color:#1a8f4a;font-weight:bold">✓ Surfaced</span>' if surfaced \
                   else '<span style="color:#c0392b;font-weight:bold">✗ Not found</span>'
            cat = r.get("category", "general").replace("_", " ").title()
            return (
                f"<tr>"
                f'<td><span class="missed-query-text">"{_e(r.get("query", ""))}"</span></td>'
                f"<td>{_e(cat)}</td>"
                f"<td>{icon}</td>"
                f"</tr>"
            )

        en_rows_html = "".join(_row_html(r) for r in en_rows)
        es_section = ""
        if es_rows:
            es_pct = int(round(es_surfaced / es_total * 100)) if es_total else 0
            es_rows_html = "".join(_row_html(r) for r in es_rows)
            es_section = f"""
<h3 style="margin-top:14px">Spanish-Language Queries (Multilingual Capture)</h3>
<p>{es_surfaced} of {es_total} Spanish queries surfaced this center ({es_pct}%).</p>
<table class="missed-table">
  <thead><tr><th>Query (Spanish)</th><th>Category</th><th>Result</th></tr></thead>
  <tbody>{es_rows_html}</tbody>
</table>"""

        return f"""
<h2>Query Results Exhibit</h2>
<p>Results from the MQCR battery. English MQCR: {en_surfaced} of {en_total} queries surfaced this center ({en_pct}%).</p>
<table class="missed-table">
  <thead><tr><th>Query (English)</th><th>Category</th><th>Result</th></tr></thead>
  <tbody>{en_rows_html}</tbody>
</table>{es_section}"""

    # Fall back to Missed Queries Exhibit (Round 1, no battery)
    queries = result.fqhc_missed_queries
    if not queries:
        return ""

    rows = []
    for q in queries[:8]:
        cat = q.get("category", "general")
        cat_display = {
            "eligibility": "Eligibility",
            "service-adjacent": "Service-Adjacent",
            "multilingual": "Multilingual",
            "general": "General",
        }.get(cat, cat)
        rows.append(
            f"<tr>"
            f'<td><span class="missed-query-text">"{_e(q.get("query", ""))}"</span></td>'
            f"<td>{_e(q.get('language', 'English'))}</td>"
            f"<td>{_e(q.get('assistant', ''))}</td>"
            f'<td><span class="missed-cat">{_e(cat_display)}</span></td>'
            f"</tr>"
        )

    return f"""
<h2>Missed Queries Exhibit</h2>
<p>Mission-frame queries where this center did not surface. Ask your phone these right now.</p>
<table class="missed-table">
  <thead><tr><th>Query</th><th>Language</th><th>Assistant</th><th>Category</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""


def _pillar2_block(result: AnalysisResult, ps: FqhcPillarScores, primary: str) -> str:
    score = ps.eligibility_cost_accuracy
    audit_rows = result.fqhc_fact_audit

    mission_critical = [r for r in audit_rows if r.get("severity") == "MISSION-CRITICAL" and r.get("flag") == "✗"]

    mc_banner = ""
    if mission_critical:
        mc_items = "".join(f"<li>{_e(r.get('claim', ''))}</li>" for r in mission_critical)
        mc_banner = f"""
<div class="mission-critical-banner">
  <strong>MISSION-CRITICAL findings:</strong> The following attested facts are being misrepresented by AI assistants, creating a barrier to care:
  <ul style="margin-left:16px;margin-top:6px">{mc_items}</ul>
</div>"""

    methodology_note = (
        "<p style='font-size:8.5pt;color:#4a6a70;margin-bottom:8px'>"
        "<strong>Methodology:</strong> Each row tests whether this center's client-attested facts are "
        "visible and accurately represented in AI-searchable public content. "
        "The AI Representation column reflects what Claude with web search actually found when "
        "researching this center online — it is not based on the center's internal records. "
        "<strong>✗ Not surfaced</strong> means the fact was absent from AI-accessible public content, "
        "creating a potential barrier for patients relying on AI assistants. "
        "This audit is distinct from the MQCR battery (Pillar 1), which measures whether the center "
        "appears when patients ask mission-frame queries."
        "</p>"
    )

    if not audit_rows:
        audit_html = methodology_note + "<p>No intake provided — Pillar 2 items cannot be assessed. Provide client-attested intake to enable full Eligibility &amp; Cost Accuracy audit.</p>"
    else:
        def _flag_span(flag: str) -> str:
            if flag == "✓":
                return f'<span class="flag-pass">✓</span>'
            if flag == "◐":
                return f'<span class="flag-partial">◐</span>'
            return f'<span class="flag-fail">✗</span>'

        def _sev_span(sev: str) -> str:
            if sev == "MISSION-CRITICAL":
                return f'<span class="severity-mc">MISSION-CRITICAL</span>'
            if sev == "notable":
                return f'<span class="severity-notable">Notable</span>'
            return f'<span class="severity-minor">Minor</span>'

        rows = []
        for r in audit_rows:
            pts = r.get("points")
            pts_str = f"{pts:.0f} pts" if pts is not None else ""
            rows.append(
                f"<tr>"
                f"<td>{_e(r.get('claim', ''))}</td>"
                f"<td>{_e(r.get('ai_representation', ''))}</td>"
                f"<td style='text-align:center'>{_flag_span(r.get('flag','◐'))}</td>"
                f"<td>{_sev_span(r.get('severity','minor'))}</td>"
                f"<td style='text-align:center;color:#4a6a70;font-size:8.5pt'>{pts_str}</td>"
                f"</tr>"
            )

        audit_html = methodology_note + f"""
<table class="fact-audit-table">
  <thead><tr>
    <th style="width:30%">Attested Fact (CLIENT-ATTESTED)</th>
    <th style="width:38%">AI Representation</th>
    <th style="width:8%;text-align:center">Flag</th>
    <th style="width:14%">Severity</th>
    <th style="width:10%;text-align:center">Points</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""

    score_str = f"Score: {score}/100" if score is not None else "Score: —"

    return f"""
<h2>Pillar 2 — Eligibility &amp; Cost Accuracy (25 pts) &nbsp;<small style="font-weight:normal;font-size:9pt;color:#7a9095">{score_str}</small></h2>
{mc_banner}
{audit_html}"""


def _pillar3_block(result: AnalysisResult, ps: FqhcPillarScores, prov, primary: str) -> str:
    score = ps.site_service_completeness
    score_str = f"Score: {score}/100" if score is not None else "Score: —"

    sites_html = ""
    if prov and prov.consolidated_locations:
        rows = []
        for loc in prov.consolidated_locations:
            rating = f"{loc.google_rating:.1f}★" if loc.google_rating else "—"
            rows.append(
                f"<tr>"
                f"<td>{_e(loc.name)}</td>"
                f"<td>{_e(loc.address or '')}</td>"
                f"<td style='text-align:center'>{rating}</td>"
                f"<td style='text-align:center'>{loc.google_review_count or '—'}</td>"
                f"</tr>"
            )
        sites_html = f"""
<table class="site-table">
  <thead><tr><th>Site Name</th><th>Address</th><th style="text-align:center">Google ★</th><th style="text-align:center">Reviews</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""

    return f"""
<h2>Pillar 3 — Site &amp; Service Completeness (20 pts) &nbsp;<small style="font-weight:normal;font-size:9pt;color:#7a9095">{score_str}</small></h2>
<p>Site census: {len(prov.consolidated_locations) if prov else 0} site(s) enumerated. Measures site discoverability, service-line attribution per site, and directory consistency across Google, HRSA Find-a-Health-Center, and Medicaid MCO directories.</p>
{sites_html}"""


def _pillar4_block(result: AnalysisResult, ps: FqhcPillarScores, prov, primary: str) -> str:
    score = ps.experience_reputation
    score_str = f"Score: {score}/100" if score is not None else "Score: —"

    fd_html = ""
    if prov:
        fd = prov.google_footprint.front_door
        if fd.rating is not None:
            fd_html = (
                f"<p><strong>Google front door:</strong> {fd.rating:.1f}★ ({fd.count or '—'} reviews)"
                f"{' — verified GBP' if fd.verified else ' — not verified'}</p>"
            )
        pv = prov.patient_voice_summary
        if pv:
            fd_html += f"<p><strong>Patient voice:</strong> {_e(_strip_md(pv))}</p>"
        gap = prov.google_footprint.gap_note
        if gap:
            fd_html += f"<p><strong>Footprint note:</strong> {_e(gap)}</p>"

    return f"""
<h2>Pillar 4 — Experience &amp; Reputation (20 pts) &nbsp;<small style="font-weight:normal;font-size:9pt;color:#7a9095">{score_str}</small></h2>
{fd_html}"""


def _pillar5_block(result: AnalysisResult, ps: FqhcPillarScores, prov, primary: str) -> str:
    score = ps.institutional_signals
    score_str = f"Score: {score}/100" if score is not None else "Score: —"

    creds = ""
    if prov:
        accs = prov.accreditations
        if accs:
            creds = "<p><strong>Accreditations/recognitions:</strong> " + _e(", ".join(accs)) + "</p>"
        cms = prov.cms_quality_highlights
        if cms:
            creds += f"<p><strong>Quality notes:</strong> {_e(_strip_md(cms))}</p>"

    intake = result.fqhc_intake or {}
    hrsa_lines = []
    if intake.get("is_330"):
        hrsa_lines.append("Section 330 grantee (CLIENT-ATTESTED)")
    if intake.get("is_lookalike"):
        hrsa_lines.append("HRSA look-alike designation (CLIENT-ATTESTED)")
    hrsa_html = ("<p>" + " · ".join(hrsa_lines) + "</p>") if hrsa_lines else ""

    return f"""
<h2>Pillar 5 — Institutional Signals (10 pts) &nbsp;<small style="font-weight:normal;font-size:9pt;color:#7a9095">{score_str}</small></h2>
{hrsa_html}
{creds}
<p><small style="color:#7a9095">Stakeholder perception (Category S) observations are reported unscored in Community Health Edition v1.0.</small></p>"""


_BATTERY_STALE_RE = re.compile(
    r"(run|complete|conduct|launch|schedule|initiate)\s+(the\s+)?(mission[- ]query|mqcr|battery)",
    re.IGNORECASE,
)


def _assessment_block(result: AnalysisResult, primary: str) -> str:
    sections = result.improvement_sections
    if not sections:
        return ""

    battery_ran = result.fqhc_mqcr is not None
    html_parts = [f"<h2>AI Visibility Assessment &amp; Improvement Opportunities</h2>"]

    top_rec = result.top_recommendation
    if top_rec and not (battery_ran and _BATTERY_STALE_RE.search(top_rec)):
        html_parts.append(
            f'<div class="verdict-box" style="margin-bottom:16px">'
            f'<strong>Priority recommendation:</strong> {_e(_strip_md(top_rec))}'
            f'</div>'
        )

    for sec in sections:
        # Skip entire section if its title tells the user to run the battery and it already ran
        if battery_ran and _BATTERY_STALE_RE.search(sec.title or ""):
            continue

        # Filter individual items that tell the user to run the battery
        items = sec.items or []
        if battery_ran:
            items = [i for i in items if not _BATTERY_STALE_RE.search(i)]

        # Skip section if title + description + items are all empty after filtering
        if not sec.title and not sec.description and not items:
            continue

        html_parts.append(f'<div class="improvement-section">')
        html_parts.append(f'<h3>{_e(sec.title)}</h3>')
        if sec.description:
            html_parts.append(f'<p style="font-size:9.5pt;color:#4a6a70">{_e(_strip_md(sec.description))}</p>')
        if items:
            items_html = "".join(f"<li>{_e(_strip_md(i))}</li>" for i in items)
            html_parts.append(f"<ul>{items_html}</ul>")
        html_parts.append("</div>")

    return "\n".join(html_parts)


def _appendix_block(primary: str, pale: str) -> str:
    rubric_rows = [
        ("Pillar 1", "Access & Findability", "25", "MQCR 12 + Multilingual 8 + Service-Adjacent 5"),
        ("Pillar 2", "Eligibility & Cost Accuracy", "25", "Fact-audit: sliding fee 5, NTAFA 4, insurance 5, free-clinic 4, new-patient 4, framing 3"),
        ("Pillar 3", "Site & Service Completeness", "20", "Discoverability 7, service attribution 5, directory consistency 8"),
        ("Pillar 4", "Experience & Reputation", "20", "Rating×volume 7, dispersion 4, third-party 3, recency 3, narrative framing 3"),
        ("Pillar 5", "Institutional Signals", "10", "HRSA quality 3, UDS 3, NCQA PCMH 2, 330 status 2"),
    ]
    rows_html = "".join(
        f"<tr><td><strong>{_e(p)}</strong></td><td>{_e(n)}</td><td style='text-align:center'>{_e(w)}</td><td style='font-size:8pt'>{_e(d)}</td></tr>"
        for p, n, w, d in rubric_rows
    )

    return f"""
<div class="appendix">
<h3>Appendix — Community Health Edition Prompt Battery &amp; Scoring Rubric v1.0</h3>
<p style="font-size:8.5pt;margin:8px 0">The AI Visibility Score is computed deterministically from the pillar sub-scores above. The Mission Query Capture Rate (MQCR) is the fraction of logged mission-frame battery runs where this organization or a correct site surfaced — it is never narrated or estimated. All scores are 0–100; composite = weighted average of scored sub-pillars (minimum 50% weight required to render).</p>
<table>
  <thead><tr><th>Pillar</th><th>Name</th><th style="text-align:center">Max Pts</th><th>Sub-components</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<p style="font-size:8pt;margin-top:10px;color:#4a6a70">Battery categories: A (Baseline entity knowledge), M (Mission-frame discovery — feeds MQCR), EL (Eligibility &amp; cost verification — feeds Pillar 2 audit), ML (Multilingual subset), SV (Service/site specific), CMP (Alternatives framing, 3 intents), ADV (Adverse probing), S (Stakeholder perception — reported unscored in v1).</p>
</div>"""


def _disclaimer_block(result: AnalysisResult, primary: str) -> str:
    text = _strip_md(result.disclaimer or "")
    return f'<div class="disclaimer-text">{_e(text)}</div>'
