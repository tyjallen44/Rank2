"""Network Pulse PDF renderer — multi-state hospital network AI Visibility reports.

Executive-summary format for C-suite audiences.  Dark navy (#0B1F3A) primary
with gold accent (#C9A84C) — distinct from the teal used in Community Health.

Reuses brand configs and HTML helpers from pdf.py.
"""
from __future__ import annotations

import html as _html_lib
from pathlib import Path
from typing import Optional

from .models import NetworkResult, NetworkFacility
from .network_scoring import grade_band as _grade_band
from .pdf import _BRAND_CONFIGS, _e, _strip_md

# Network Pulse brand — uses the standard RLDatix teal palette.
_NETWORK_PRIMARY = "#0F4146"
_NETWORK_ACCENT  = "#80F8E4"
_NETWORK_PALE    = "#EEF7F1"


def render_network_pdf(
    result: NetworkResult,
    pdf_path: str,
    brand: str = "original",
) -> None:
    """Render a Network Pulse report to a branded PDF."""
    from playwright.sync_api import sync_playwright

    # Always use navy/gold for network reports; ignore brand's primary/accent
    cfg = dict(_BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"]))
    cfg["primary"] = _NETWORK_PRIMARY
    cfg["accent"]  = _NETWORK_ACCENT
    cfg["pale"]    = _NETWORK_PALE

    landscape = len(result.facilities) > 20
    html = _build_network_html(result, cfg)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            landscape=landscape,
            margin={"top": "0", "bottom": "0.65in", "left": "0", "right": "0"},
            print_background=True,
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font-family:Arial,Helvetica,sans-serif;'
                'font-size:8px;color:#8a9aaa;display:flex;justify-content:space-between;'
                'align-items:center;padding:0 48px 10px;box-sizing:border-box">'
                '<span style="letter-spacing:0.05em">Prepared by Pulse | RLDatix &nbsp;&mdash;&nbsp; Confidential</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                '</div>'
            ),
        )
        browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# HTML builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_network_html(result: NetworkResult, cfg: dict) -> str:
    primary = cfg.get("primary", _NETWORK_PRIMARY)
    accent  = cfg.get("accent",  _NETWORK_ACCENT)
    pale    = cfg.get("pale",    _NETWORK_PALE)

    css          = _network_css(primary, pale, accent)
    css_overrides = cfg.get("css_overrides", "")
    logo_html    = cfg.get("logo_html") or _default_logo_html()

    cover       = _cover_block(result, primary, accent, pale, logo_html)
    exec_sum    = _exec_summary_block(result)
    score_bkdn  = _score_breakdown_block(result, primary, accent, pale)
    facility_sc = _facility_scorecard_block(result, primary, pale)
    recs        = _recommendations_block(result, primary, accent)
    appendix    = _methodology_appendix(primary, pale)

    title = _e(result.network_canonical_name or result.network_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Network Pulse — {title}</title>
<style>
{css}
{css_overrides}
</style>
</head>
<body>
{cover}
<div class="report-body">
{exec_sum}
{score_bkdn}
{facility_sc}
{recs}
{appendix}
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


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

def _network_css(primary: str, pale: str, accent: str) -> str:
    return f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; color: #1a2535; background: #fff; }}

/* ── Cover ──────────────────────────────────────────────────────────────── */
.cover {{
  background: {primary};
  color: #fff;
  padding: 40px 48px 48px;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}}
.cover-logo-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 20px;
  margin-bottom: 28px;
  border-bottom: 1px solid rgba(128,248,228,0.30);
}}
.cover-logo-img {{
  height: 38px;
  width: auto;
}}
.cover-logo-text {{
  font-size: 16pt;
  font-weight: bold;
  color: #fff;
  letter-spacing: 0.06em;
}}
.cover-report-type-label {{
  font-size: 7pt;
  text-transform: uppercase;
  letter-spacing: 0.20em;
  color: rgba(128,248,228,0.75);
  text-align: right;
  font-weight: bold;
  line-height: 1.6;
}}
.cover-edition {{
  font-size: 7.5pt;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: {accent};
  margin-bottom: 16px;
  font-weight: bold;
}}
.cover-network-name {{
  font-size: 28pt;
  font-weight: bold;
  line-height: 1.1;
  margin-bottom: 6px;
  letter-spacing: -0.01em;
}}
.cover-subtitle {{
  font-size: 11pt;
  color: rgba(255,255,255,0.60);
  margin-bottom: 8px;
}}
.cover-confidential {{
  font-size: 7pt;
  text-transform: uppercase;
  letter-spacing: 0.13em;
  color: rgba(255,255,255,0.30);
  margin-bottom: 32px;
}}
.cover-score-center {{
  text-align: center;
  margin: 0 auto 36px;
}}
.cover-score-num {{
  font-size: 72pt;
  font-weight: bold;
  color: {accent};
  line-height: 1;
  display: block;
}}
.cover-grade {{
  font-size: 22pt;
  font-weight: bold;
  color: #fff;
  margin-top: 4px;
}}
.cover-grade-band {{
  font-size: 11pt;
  color: rgba(255,255,255,0.6);
  margin-top: 2px;
}}
.cover-stat-boxes {{
  display: flex;
  gap: 16px;
  justify-content: center;
  margin-bottom: 28px;
}}
.cover-stat-box {{
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 8px;
  padding: 16px 24px;
  text-align: center;
  flex: 1;
  max-width: 200px;
}}
.cover-stat-val {{
  font-size: 20pt;
  font-weight: bold;
  color: {accent};
  line-height: 1;
}}
.cover-stat-lbl {{
  font-size: 8pt;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: rgba(255,255,255,0.55);
  margin-top: 5px;
  line-height: 1.3;
}}
.cover-benchmark {{
  background: rgba(255,255,255,0.05);
  border-left: 3px solid {accent};
  padding: 12px 16px;
  border-radius: 0 6px 6px 0;
  font-size: 9pt;
  color: rgba(255,255,255,0.7);
  margin-bottom: 24px;
}}
.cover-meta {{
  margin-top: auto;
  padding-top: 20px;
  border-top: 1px solid rgba(255,255,255,0.15);
  font-size: 8pt;
  color: rgba(255,255,255,0.4);
  display: flex;
  justify-content: space-between;
}}

/* ── Report body ────────────────────────────────────────────────────────── */
.report-body {{ padding: 36px 48px; }}
h2 {{
  font-size: 11.5pt;
  font-weight: bold;
  color: {primary};
  margin: 32px 0 10px;
  padding-bottom: 6px;
  border-bottom: 2px solid {accent};
  letter-spacing: 0.04em;
  text-transform: uppercase;
}}
p {{ margin-bottom: 10px; line-height: 1.55; }}

/* ── Executive Summary ──────────────────────────────────────────────────── */
.exec-summary {{
  background: {pale};
  border-left: 5px solid {primary};
  padding: 18px 22px;
  margin: 12px 0 24px;
  border-radius: 0 8px 8px 0;
}}
.exec-summary p {{
  font-size: 11pt;
  line-height: 1.65;
  margin: 0;
  color: #1a2535;
}}

/* ── Score Breakdown ────────────────────────────────────────────────────── */
.score-breakdown {{
  margin: 16px 0 28px;
}}
.score-bar-row {{
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}}
.score-bar-label {{
  width: 220px;
  font-size: 9.5pt;
  color: #1a2535;
  flex-shrink: 0;
}}
.score-bar-label .weight {{
  font-size: 8pt;
  color: #8a9aaa;
  display: block;
}}
.score-bar-track {{
  flex: 1;
  height: 16px;
  background: #e4e8ec;
  border-radius: 8px;
  overflow: hidden;
}}
.score-bar-fill {{
  height: 100%;
  background: {primary};
  border-radius: 8px;
  transition: width 0s;
}}
.score-bar-num {{
  width: 48px;
  text-align: right;
  font-size: 11pt;
  font-weight: bold;
  color: {primary};
  flex-shrink: 0;
}}

/* ── Facility Scorecard ─────────────────────────────────────────────────── */
.market-callouts {{
  display: flex;
  gap: 16px;
  margin: 12px 0 16px;
}}
.market-callout {{
  flex: 1;
  border-radius: 8px;
  padding: 14px 16px;
}}
.market-callout.top {{
  background: #eaf7ee;
  border: 1px solid #a8d9b2;
}}
.market-callout.gap {{
  background: #fdf0f0;
  border: 1px solid #e0a0a0;
}}
.market-callout-title {{
  font-size: 8pt;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: bold;
  margin-bottom: 6px;
}}
.market-callout.top .market-callout-title {{ color: #1a7a3c; }}
.market-callout.gap .market-callout-title {{ color: #8b1c1c; }}
.market-callout ul {{
  margin-left: 14px;
}}
.market-callout li {{
  font-size: 9pt;
  line-height: 1.5;
}}
.facility-table {{
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 9.5pt;
}}
.facility-table th {{
  background: {primary};
  color: #fff;
  padding: 8px 10px;
  text-align: left;
  font-size: 8.5pt;
}}
.facility-table td {{
  padding: 7px 10px;
  border-bottom: 1px solid #dde3ea;
  vertical-align: middle;
}}
.facility-table tr.row-red td    {{ background: #fdf0f0; }}
.facility-table tr.row-yellow td {{ background: #fffbea; }}
.facility-table tr.row-green td  {{ background: #eaf7ee; }}
thead {{ display: table-header-group; }}
tfoot {{ display: table-footer-group; }}
.grade-badge {{
  display: inline-block;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  text-align: center;
  line-height: 24px;
  font-weight: bold;
  font-size: 9pt;
  color: #fff;
}}
.grade-A {{ background: #1a7a3c; }}
.grade-B {{ background: #2e7d9a; }}
.grade-C {{ background: #b87a00; }}
.grade-D {{ background: #c05020; }}
.grade-F {{ background: #8b1c1c; }}
.grade-dash {{ background: #999; }}

/* ── Strategic Recommendations ──────────────────────────────────────────── */
.rec-card {{
  border: 1px solid #d0dae5;
  border-left: 4px solid {accent};
  border-radius: 6px;
  padding: 14px 18px;
  margin-bottom: 12px;
  background: #fff;
}}
.rec-num {{
  font-size: 8pt;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: {accent};
  font-weight: bold;
  margin-bottom: 4px;
}}
.rec-headline {{
  font-size: 10.5pt;
  font-weight: bold;
  color: {primary};
  margin-bottom: 4px;
}}
.rec-body {{
  font-size: 9.5pt;
  color: #3a4a5a;
  line-height: 1.5;
}}

/* ── Methodology appendix ───────────────────────────────────────────────── */
.appendix {{
  background: #f5f7fa;
  border: 1px solid #dde3ea;
  border-radius: 6px;
  padding: 20px 24px;
  margin: 28px 0 16px;
}}
.appendix h3 {{
  font-size: 10pt;
  color: {primary};
  margin-bottom: 8px;
}}
.appendix p {{
  font-size: 8.5pt;
  color: #4a5a6a;
  line-height: 1.55;
}}
.appendix table {{
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 8.5pt;
}}
.appendix th {{
  background: {primary};
  color: #fff;
  padding: 6px 10px;
  text-align: left;
}}
.appendix td {{
  padding: 6px 10px;
  border-bottom: 1px solid #dde3ea;
  vertical-align: top;
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _cover_block(result: NetworkResult, primary: str, accent: str, pale: str, logo_html: str = "") -> str:
    name      = _e(result.network_canonical_name or result.network_name)
    score_str = str(result.ai_visibility_score) if result.ai_visibility_score is not None else "—"
    grade     = result.grade or "—"
    band      = result.grade_band or ""
    hospitals = result.total_hospitals or len(result.facilities)
    states    = len(result.states_covered) if result.states_covered else "—"
    generated = str(result.generated_at or "")
    hq        = _e(result.hq_location or "")

    grade_css = {
        "A": "color:#6fcf97",
        "B": "color:#56b8d9",
        "C": f"color:{accent}",
        "D": "color:#f2994a",
        "F": "color:#eb5757",
    }.get(grade, "color:rgba(255,255,255,0.7)")

    return f"""
<div class="cover">
  <div class="cover-logo-header">
    {logo_html}
    <div class="cover-report-type-label">Network Pulse<br>AI Visibility Report</div>
  </div>

  <div class="cover-edition">Network AI Visibility</div>
  <div class="cover-network-name">{name}</div>
  <div class="cover-subtitle">{hq if hq else 'Multi-State Hospital Network'}</div>
  <div class="cover-confidential">Confidential &nbsp;·&nbsp; Prepared exclusively for {name}</div>

  <div class="cover-score-center">
    <span class="cover-score-num" style="color:{accent}">{score_str}</span>
    <div class="cover-grade" style="{grade_css}">{grade}</div>
    <div class="cover-grade-band">{_e(band)}</div>
  </div>

  <div class="cover-stat-boxes">
    <div class="cover-stat-box">
      <div class="cover-stat-val">{hospitals}</div>
      <div class="cover-stat-lbl">Hospitals<br>Assessed</div>
    </div>
    <div class="cover-stat-box">
      <div class="cover-stat-val">{states}</div>
      <div class="cover-stat-lbl">States<br>Covered</div>
    </div>
    <div class="cover-stat-box">
      <div class="cover-stat-val" style="font-size:12pt">{_e(generated)}</div>
      <div class="cover-stat-lbl">Report<br>Date</div>
    </div>
  </div>

  <div class="cover-benchmark">
    Score reflects AI visibility across the network's full geographic footprint — brand recognition, local market surfacing, and information accuracy in AI assistants used by patients and referring physicians.
  </div>

  <div class="cover-meta">
    <span>Pulse | RLDatix &nbsp;·&nbsp; Network AI Visibility &nbsp;·&nbsp; {_e(name)}</span>
    <span>Generated {_e(generated)}</span>
  </div>
</div>"""


def _exec_summary_block(result: NetworkResult) -> str:
    summary = _strip_md(result.executive_summary or "")
    if not summary:
        return ""
    return f"""
<h2>Executive Summary</h2>
<div class="exec-summary"><p>{_e(summary)}</p></div>"""


def _score_breakdown_block(
    result: NetworkResult, primary: str, accent: str, pale: str
) -> str:
    dims = [
        ("Brand Visibility",      "40% weight", result.brand_visibility_score,
         _strip_md(result.brand_visibility_narrative or "")),
        ("Market Coverage",       "35% weight", result.market_coverage_score,
         _strip_md(result.market_coverage_narrative or "")),
        ("Information Accuracy",  "25% weight", result.information_accuracy_score, ""),
    ]

    bars_html = ""
    for label, weight, score, narrative in dims:
        pct  = score if score is not None else 0
        num  = str(score) if score is not None else "—"
        bars_html += f"""
<div class="score-bar-row">
  <div class="score-bar-label">
    {_e(label)}
    <span class="weight">{_e(weight)}</span>
  </div>
  <div class="score-bar-track">
    <div class="score-bar-fill" style="width:{pct}%"></div>
  </div>
  <div class="score-bar-num">{num}</div>
</div>"""
        if narrative:
            bars_html += f'<p style="font-size:9pt;color:#4a5a6a;margin:0 0 10px 234px">{_e(narrative)}</p>'

    composite_str = str(result.ai_visibility_score) if result.ai_visibility_score is not None else "—"

    return f"""
<h2>Score Breakdown</h2>
<div class="score-breakdown">
{bars_html}
<p style="font-size:8.5pt;color:#8a9aaa;margin-top:8px">
  Composite score: <strong style="color:{primary}">{composite_str}/100</strong>
  &nbsp;·&nbsp; Weights: Brand Visibility 40% + Market Coverage 35% + Information Accuracy 25%
</p>
</div>"""


def _facility_scorecard_block(result: NetworkResult, primary: str, pale: str) -> str:
    if not result.facilities:
        return ""

    # Market callouts
    top_html = ""
    gap_html = ""
    if result.top_markets:
        items = "".join(f"<li>{_e(m)}</li>" for m in result.top_markets)
        top_html = f"""
<div class="market-callout top">
  <div class="market-callout-title">Top Markets</div>
  <ul>{items}</ul>
</div>"""
    if result.gap_markets:
        items = "".join(f"<li>{_e(m)}</li>" for m in result.gap_markets)
        gap_html = f"""
<div class="market-callout gap">
  <div class="market-callout-title">Gap Markets</div>
  <ul>{items}</ul>
</div>"""

    callouts = ""
    if top_html or gap_html:
        callouts = f'<div class="market-callouts">{top_html}{gap_html}</div>'

    # Facility table rows (already sorted worst→best in analyzer)
    rows_html = ""
    for fac in result.facilities:
        score = fac.ai_visibility_score
        if score is not None and score < 50:
            row_cls = "row-red"
        elif score is not None and score < 75:
            row_cls = "row-yellow"
        elif score is not None:
            row_cls = "row-green"
        else:
            row_cls = ""

        grade = fac.grade or "—"
        grade_css = "grade-" + (grade if grade in ("A", "B", "C", "D", "F") else "dash")
        score_str = str(score) if score is not None else "—"
        surfaced = "Yes" if fac.surfaced_for_local else ("No" if fac.surfaced_for_local is not None else "—")
        attributed = "Yes" if fac.attributed_to_network else ("No" if fac.attributed_to_network is not None else "—")
        key_gap = _e(fac.key_gap or "")

        rows_html += f"""<tr class="{row_cls}">
  <td>{_e(fac.name)}</td>
  <td>{_e(fac.city)}, {_e(fac.state)}</td>
  <td style="text-align:center"><strong>{score_str}</strong></td>
  <td style="text-align:center"><span class="grade-badge {grade_css}">{_e(grade)}</span></td>
  <td style="text-align:center;font-size:9pt">{surfaced}</td>
  <td style="text-align:center;font-size:9pt">{attributed}</td>
  <td style="font-size:8.5pt;color:#4a5a6a">{key_gap}</td>
</tr>"""

    return f"""
<h2>Facility Scorecard</h2>
<p style="font-size:9pt;color:#4a5a6a">
  {len(result.facilities)} hospitals assessed, sorted by AI Visibility Score (lowest first).
  <span style="color:#c0392b">■</span> Score &lt;50 &nbsp;
  <span style="color:#b87a00">■</span> 50–74 &nbsp;
  <span style="color:#1a7a3c">■</span> 75+
</p>
{callouts}
<table class="facility-table">
  <thead>
    <tr>
      <th>Hospital</th>
      <th>City, State</th>
      <th style="text-align:center">AI Score</th>
      <th style="text-align:center">Grade</th>
      <th style="text-align:center">Local Surface</th>
      <th style="text-align:center">Network Attribution</th>
      <th>Key Gap</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>"""


def _recommendations_block(result: NetworkResult, primary: str, accent: str) -> str:
    recs = result.strategic_recommendations
    if not recs:
        return ""

    cards_html = ""
    for i, rec in enumerate(recs, 1):
        rec_clean = _strip_md(rec)
        # First sentence becomes headline, remainder is body
        parts = rec_clean.split(". ", 1)
        headline = parts[0].rstrip(".")
        body     = parts[1] if len(parts) > 1 else ""
        cards_html += f"""
<div class="rec-card">
  <div class="rec-num">Recommendation {i}</div>
  <div class="rec-headline">{_e(headline)}.</div>
  {"" if not body else f'<div class="rec-body">{_e(body)}</div>'}
</div>"""

    return f"""
<h2>Strategic Recommendations</h2>
{cards_html}"""


def _methodology_appendix(primary: str, pale: str) -> str:
    return f"""
<div class="appendix">
<h3>Methodology — Network Pulse AI Visibility Scoring</h3>
<p>
  The Network AI Visibility Score is a composite of three dimensions, each scored 0–100.
  The composite is a weighted average: Brand Visibility (40%) + Market Coverage (35%) + Information Accuracy (25%).
  Scoring reflects how well this network and its member hospitals are represented in AI assistants
  when patients and referring physicians ask network-level and local discovery queries.
</p>
<table>
  <thead><tr><th>Dimension</th><th>Weight</th><th>What it measures</th></tr></thead>
  <tbody>
    <tr>
      <td><strong>Brand Visibility</strong></td>
      <td>40%</td>
      <td>How accurately and completely AI assistants represent the network when queried by name — roster, geography, and capabilities</td>
    </tr>
    <tr>
      <td><strong>Market Coverage</strong></td>
      <td>35%</td>
      <td>Whether member hospitals surface in local "[city] hospital" and "hospital near me" queries and are correctly attributed to the network</td>
    </tr>
    <tr>
      <td><strong>Information Accuracy</strong></td>
      <td>25%</td>
      <td>Correctness of key facts in AI responses: service lines, bed counts, trauma levels, teaching status, and accreditations</td>
    </tr>
  </tbody>
</table>
<p style="margin-top:8px">
  Grade bands: A (80+, Top Quartile) · B (65–79, Above Average) · C (50–64, Industry Average) · D (35–49, Below Average) · F (&lt;35, Bottom Quartile).
  Facility scores use the same thresholds. Facility table is sorted worst → best to prioritize remediation effort.
</p>
</div>"""
