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
from .pdf import _BRAND_CONFIGS, _e, _strip_md, _TEASER_PHONE, _TEASER_DEMO_URL, _quartile_label, _score_bar_color
from .scoring import grade_from_score as _grade_from_score

# Network Pulse brand — uses the standard RLDatix teal palette.
_NETWORK_PRIMARY = "#0F4146"
_NETWORK_ACCENT  = "#80F8E4"
_NETWORK_PALE    = "#EEF7F1"


def render_network_pdf(
    result: NetworkResult,
    pdf_path: str,
    brand: str = "original",
    teaser: bool = False,
) -> None:
    """Render a Network Pulse report to a branded PDF."""
    from playwright.sync_api import sync_playwright

    # Always use navy/gold for network reports; ignore brand's primary/accent
    cfg = dict(_BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"]))
    cfg["primary"] = _NETWORK_PRIMARY
    cfg["accent"]  = _NETWORK_ACCENT
    cfg["pale"]    = _NETWORK_PALE

    landscape = len(result.facilities) > 20
    html = _build_network_html(result, cfg, teaser=teaser)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        from .pdf import _fmt_cached
        _cached_lbl = _fmt_cached(getattr(result, "data_collected_at", None) or result.generated_at)
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
                f'<span>{_cached_lbl}</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                '</div>'
            ),
        )
        browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# HTML builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_network_html(result: NetworkResult, cfg: dict, teaser: bool = False) -> str:
    from .network_prompts import get_facility_config
    primary = cfg.get("primary", _NETWORK_PRIMARY)
    accent  = cfg.get("accent",  _NETWORK_ACCENT)
    pale    = cfg.get("pale",    _NETWORK_PALE)

    css          = _network_css(primary, pale, accent)
    css_overrides = cfg.get("css_overrides", "")
    logo_html    = cfg.get("logo_html") or _default_logo_html()

    ftype_cfg    = get_facility_config(result.facility_type or "hospital")

    cover       = _cover_block(result, primary, accent, pale, logo_html, ftype_cfg)
    exec_sum    = _exec_summary_block(result)
    score_bkdn  = _score_breakdown_block(result, primary, accent, pale)
    facility_sc = _facility_scorecard_block(result, primary, pale, ftype_cfg, teaser=teaser)
    recs        = _recommendations_block(result, primary, accent, teaser=teaser)
    appendix    = _methodology_appendix(primary, pale, ftype_cfg)

    title = _e(result.network_canonical_name or result.network_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hospital Network — {title}</title>
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
}}
.cover-score-out-of {{
  font-size: 26pt;
  font-weight: 700;
  color: rgba(255,255,255,0.32);
  vertical-align: top;
  display: inline-block;
  padding-top: 14px;
  margin-left: 3px;
}}
.cover-score-lbl {{
  font-size: 8pt;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: rgba(255,255,255,0.45);
  font-weight: 600;
  margin-top: 6px;
  margin-bottom: 14px;
}}
.cover-quartile-badge {{
  display: inline-block;
  border-radius: 0 8px 8px 0;
  padding: 10px 20px 10px 14px;
  background: rgba(255,255,255,0.07);
  text-align: left;
}}
.cover-quartile-badge-lbl {{
  font-size: 6.5pt;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: rgba(255,255,255,0.45);
  font-weight: 700;
  margin-bottom: 4px;
}}
.cover-quartile-badge-val {{
  font-size: 19pt;
  font-weight: 800;
  line-height: 1;
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
.facility-table tr.row-Q1 td   {{ background: #eaf7ef; }}
.facility-table tr.row-Q2 td   {{ background: #e6f2f7; }}
.facility-table tr.row-Q3 td   {{ background: #fdf6e3; }}
.facility-table tr.row-Q4 td   {{ background: #fdf0f0; }}
thead {{ display: table-header-group; }}
tfoot {{ display: table-footer-group; }}
.quartile-badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 700;
  font-size: 8.5pt;
  color: #fff;
  letter-spacing: 0.03em;
}}
.q-Q1 {{ background: #2e9e5b; }}
.q-Q2 {{ background: #2e7d9a; }}
.q-Q3 {{ background: #e09b2a; }}
.q-Q4 {{ background: #d94f4f; }}
.q-dash {{ background: #999; }}

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

/* ── Teaser blur ─────────────────────────────────────────────────────────── */
.net-teaser-blur-wrapper {{
  position: relative;
  overflow: hidden;
  border-radius: 0 0 6px 6px;
}}
.net-teaser-blur-content {{
  filter: blur(2px);
  user-select: none;
  pointer-events: none;
}}
.net-teaser-blur-overlay {{
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(238,247,241,0.26);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 14px 20px;
  border: 1.5px dashed {accent};
  border-top: none;
  border-radius: 0 0 6px 6px;
}}
.net-teaser-blur-overlay .blur-lock,
.net-teaser-blur-overlay .blur-cta-heading,
.net-teaser-blur-overlay .blur-cta-sub,
.net-teaser-blur-overlay .blur-cta-actions {{
  background: rgba(238,247,241,0.92);
  border-radius: 4px;
  padding: 2px 8px;
}}
.blur-lock {{ font-size: 18pt; margin-bottom: 5px; }}
.blur-cta-heading {{ font-size: 10pt; font-weight: 700; color: {primary}; margin-bottom: 5px; }}
.blur-cta-sub {{ font-size: 7.5pt; color: #3a5a60; line-height: 1.45; margin-bottom: 9px; max-width: 360px; }}
.blur-cta-actions {{ font-size: 9pt; font-weight: 600; color: {primary}; }}
.blur-phone {{ font-weight: 700; }}
.blur-demo-link {{ color: {primary}; font-weight: 700; text-decoration: underline; }}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _cover_block(
    result: NetworkResult, primary: str, accent: str, pale: str,
    logo_html: str = "", ftype_cfg: dict | None = None,
) -> str:
    ftype_cfg  = ftype_cfg or {}
    plural_cap = ftype_cfg.get("plural", "hospitals").capitalize()
    name      = _e(result.network_canonical_name or result.network_name)
    score_str = str(result.ai_visibility_score) if result.ai_visibility_score is not None else "—"
    quartile, q_band = _grade_from_score(result.ai_visibility_score)
    hospitals = result.total_hospitals or len(result.facilities)
    states    = len(result.states_covered) if result.states_covered else "—"
    generated = str(result.generated_at or "")
    hq        = _e(result.hq_location or "")

    q_color = {
        "Q1": "#6fcf97",
        "Q2": "#56b8d9",
        "Q3": "#f2994a",
        "Q4": "#eb5757",
    }.get(quartile, "rgba(255,255,255,0.7)")

    return f"""
<div class="cover">
  <div class="cover-logo-header">
    {logo_html}
    <div class="cover-report-type-label">Hospital Network<br>AI Visibility Report</div>
  </div>

  <div class="cover-edition">Network AI Visibility</div>
  <div class="cover-network-name">{name}</div>
  <div class="cover-subtitle">{hq if hq else ftype_cfg.get('label', 'Multi-State Healthcare Network')}</div>
  <div class="cover-confidential">Confidential &nbsp;·&nbsp; Prepared exclusively for {name}</div>

  <div class="cover-score-center">
    <div style="line-height:1">
      <span class="cover-score-num" style="color:{accent}">{score_str}</span><span class="cover-score-out-of">/100</span>
    </div>
    <div class="cover-score-lbl">AI Visibility Score</div>
    <div class="cover-quartile-badge" style="border-left:4px solid {q_color}">
      <div class="cover-quartile-badge-lbl">National Quartile</div>
      <div class="cover-quartile-badge-val" style="color:{q_color}">{_e(_quartile_label(quartile))} <span style="font-size:11pt;font-weight:500;color:rgba(255,255,255,0.7)">&middot;&nbsp;{_e(q_band)}</span></div>
    </div>
  </div>

  <div class="cover-stat-boxes">
    <div class="cover-stat-box">
      <div class="cover-stat-val">{hospitals}</div>
      <div class="cover-stat-lbl">{plural_cap}<br>Assessed</div>
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
    The Pulse Score reflects how visible and favorable this system is across four pillars — Outcomes &amp; Safety, Credentials &amp; Recognition, Experience &amp; Reviews, and Access &amp; Fit — when patients and referring physicians ask AI assistants where to get care. It is the same score this system receives in the Hospital Market report. Data is gathered through live queries run directly against ChatGPT, Claude, and Gemini.
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
    tiers = result.tier_scores or {}
    pillars = [
        ("Outcomes & Safety",          tiers.get("clinical_outcomes_safety")),
        ("Credentials & Recognition",  tiers.get("credentials_recognition")),
        ("Experience & Reviews",       tiers.get("patient_experience_reviews")),
        ("Access & Fit",               tiers.get("access_fit")),
    ]

    bars_html = ""
    for label, score in pillars:
        pct   = score if score is not None else 0     # None → red, empty bar
        num   = str(score) if score is not None else "—"
        color = _score_bar_color(score)
        bars_html += f"""
<div class="score-bar-row">
  <div class="score-bar-label">{_e(label)}</div>
  <div class="score-bar-track">
    <div class="score-bar-fill" style="width:{pct}%;background:{color}"></div>
  </div>
  <div class="score-bar-num" style="color:{color}">{num}</div>
</div>"""

    composite_str = str(result.ai_visibility_score) if result.ai_visibility_score is not None else "—"
    quartile, q_band = _grade_from_score(result.ai_visibility_score)
    system_name = _e(result.network_canonical_name or result.network_name)

    ai_says = _strip_md(result.ai_says or "")
    ai_says_html = ""
    if ai_says:
        ai_says_html = f"""
<div style="background:{pale};border-left:3px solid {accent};padding:12px 16px;margin-top:12px;border-radius:4px">
  <div style="font-size:8.5pt;font-weight:700;letter-spacing:0.05em;color:{primary};text-transform:uppercase;margin-bottom:4px">What AI Assistants Currently See</div>
  <p style="font-size:9.5pt;color:#3a4a5a;margin:0">{_e(ai_says)}</p>
</div>"""

    return f"""
<h2>System-Level AI Visibility</h2>
<p style="font-size:9pt;color:#4a5a6a;margin-top:-4px">
  How AI assistants view {system_name} as a single system — the same four-pillar Pulse Score
  this system receives in the Hospital Market report.
</p>
<div class="score-breakdown">
{bars_html}
<p style="font-size:8.5pt;color:#8a9aaa;margin-top:8px">
  Pulse Score: <strong style="color:{primary}">{composite_str}/100</strong>
  &nbsp;·&nbsp; {_e(_quartile_label(quartile))} ({_e(q_band)})
</p>
</div>
{ai_says_html}"""


def _facility_scorecard_block(
    result: NetworkResult, primary: str, pale: str, ftype_cfg: dict | None = None,
    teaser: bool = False,
) -> str:
    if not result.facilities:
        return ""
    ftype_cfg = ftype_cfg or {}
    singular_cap = ftype_cfg.get("singular", "hospital").capitalize()
    plural       = ftype_cfg.get("plural", "hospitals")
    quality_cols = ftype_cfg.get("quality_cols", ["google", "cms", "leapfrog"])
    show_cms = "cms" in quality_cols

    # Market callouts (commentary — where facilities surface well vs. poorly)
    top_html = ""
    gap_html = ""
    if result.top_markets:
        items = "".join(f"<li>{_e(m)}</li>" for m in result.top_markets)
        top_html = f"""
<div class="market-callout top">
  <div class="market-callout-title">Strong Local Markets</div>
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

    # Facility roster rows (geographic order; no competing per-facility score)
    rows_html = ""
    for fac in result.facilities:
        surfaced = "Yes" if fac.surfaced_for_local else ("No" if fac.surfaced_for_local is not None else "—")

        # Google rating
        g_rating = fac.google_rating
        g_count  = fac.google_review_count
        if g_rating is not None:
            cnt_str     = f"({g_count:,})" if g_count else ""
            google_cell = (f'<span style="font-size:9pt">{g_rating:.1f}★</span>'
                           f' <span style="font-size:7.5pt;color:#7a8a9a">{cnt_str}</span>')
        else:
            google_cell = '<span style="font-size:8pt;color:#b0b8c0">—</span>'

        # CMS star rating (hospitals only)
        if show_cms:
            cms = fac.cms_star_rating
            if cms is not None:
                cms_cell = "★" * cms + '<span style="color:#ccc">' + "★" * (5 - cms) + "</span>"
            else:
                cms_cell = '<span style="font-size:8pt;color:#b0b8c0">—</span>'
            cms_td = f'<td style="text-align:center;font-size:9pt">{cms_cell}</td>'
        else:
            cms_td = ""

        rows_html += f"""<tr>
  <td>{_e(fac.name)}</td>
  <td>{_e(fac.city)}, {_e(fac.state)}</td>
  {cms_td}
  <td style="text-align:center">{google_cell}</td>
  <td style="text-align:center;font-size:9pt">{surfaced}</td>
</tr>"""

    cms_th = '<th style="text-align:center">CMS Stars</th>' if show_cms else ""

    thead_html = f"""
  <thead>
    <tr>
      <th>{singular_cap}</th>
      <th>City, State</th>
      {cms_th}
      <th style="text-align:center">Google Rating</th>
      <th style="text-align:center">Surfaces Locally</th>
    </tr>
  </thead>"""

    if teaser:
        table_html = f"""
<table class="facility-table" style="margin-bottom:0;border-bottom:none">
{thead_html}
</table>
<div class="net-teaser-blur-wrapper">
  <div class="net-teaser-blur-content">
    <table class="facility-table" style="margin-top:0;border-top:none">
      <tbody>{rows_html}</tbody>
    </table>
  </div>
  <div class="net-teaser-blur-overlay">
    <div class="blur-lock">&#128274;</div>
    <div class="blur-cta-heading">Full facility detail available upon request</div>
    <div class="blur-cta-sub">Contact us to receive the complete Hospital Network report with the full facility roster and strategic recommendations.</div>
    <div class="blur-cta-actions">
      <span class="blur-phone">{_TEASER_PHONE}</span>
      &nbsp;&nbsp;&middot;&nbsp;&nbsp;
      <a href="{_TEASER_DEMO_URL}" class="blur-demo-link">Book a Demo &rarr;</a>
    </div>
  </div>
</div>"""
    else:
        table_html = f"""
<table class="facility-table">
{thead_html}
  <tbody>{rows_html}</tbody>
</table>"""

    return f"""
<h2>Facility Detail</h2>
<p style="font-size:9pt;color:#4a5a6a">
  {len(result.facilities)} {plural} in the network — individual facility detail for reference.
  Per-facility AI scores are not shown; the system-level Pulse Score above reflects the network as a whole.
</p>
{callouts}
{table_html}"""


def _recommendations_block(result: NetworkResult, primary: str, accent: str,
                           teaser: bool = False) -> str:
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

    if teaser:
        body_html = f"""
<div class="net-teaser-blur-wrapper" style="border-radius:6px">
  <div class="net-teaser-blur-content">{cards_html}</div>
  <div class="net-teaser-blur-overlay" style="border-top:1.5px dashed {accent};border-radius:6px">
    <div class="blur-lock">&#128274;</div>
    <div class="blur-cta-heading">Strategic recommendations available upon request</div>
    <div class="blur-cta-sub">Request the full report to receive tailored recommendations for improving AI visibility across your network.</div>
    <div class="blur-cta-actions">
      <span class="blur-phone">{_TEASER_PHONE}</span>
      &nbsp;&nbsp;&middot;&nbsp;&nbsp;
      <a href="{_TEASER_DEMO_URL}" class="blur-demo-link">Book a Demo &rarr;</a>
    </div>
  </div>
</div>"""
    else:
        body_html = cards_html

    return f"""
<h2>Strategic Recommendations</h2>
{body_html}"""


def _methodology_appendix(primary: str, pale: str, ftype_cfg: dict | None = None) -> str:
    from .pdf import _methodology_box_html
    return _methodology_box_html(
        ["Outcomes &amp; Safety", "Credentials &amp; Recognition",
         "Experience &amp; Reviews", "Access &amp; Fit"])
    ftype_cfg = ftype_cfg or {}
    plural    = ftype_cfg.get("plural", "hospitals")
    local_q   = ftype_cfg.get("local_query", "[city] hospital")
    quality_note = ftype_cfg.get("methodology_quality", "Google Rating — verified via Google Places API.")
    return f"""
<div class="appendix">
<h3>Methodology — Pulse AI Visibility Scoring</h3>
<p>
  The Pulse Score is a 0–100 measure of how visible and favorable a system is when patients and
  referring professionals ask AI assistants (ChatGPT, Claude, Gemini) where to get care. It is a
  weighted blend of four pillars, and is the <strong>same score this system receives in the Hospital
  Market report</strong> — a system reads an identical score in both reports.
</p>
<table>
  <thead><tr><th>Pillar</th><th>What it measures</th></tr></thead>
  <tbody>
    <tr>
      <td><strong>Outcomes &amp; Safety</strong></td>
      <td>Clinical quality, safety, and reputation (e.g. CMS Overall Hospital Quality Star Rating).</td>
    </tr>
    <tr>
      <td><strong>Credentials &amp; Recognition</strong></td>
      <td>Accreditations, awards, national rankings, and affiliations.</td>
    </tr>
    <tr>
      <td><strong>Experience &amp; Reviews</strong></td>
      <td>Patient sentiment and verified review volume and ratings.</td>
    </tr>
    <tr>
      <td><strong>Access &amp; Fit</strong></td>
      <td>Location, availability, and how well the system matches what patients ask for.</td>
    </tr>
  </tbody>
</table>
<p style="margin-top:8px">
  National quartiles (calibrated against scored entities in the Rank2 database):
  Q1 Top Quartile (&#8805;75) · Q2 Upper Middle (68–74) · Q3 Lower Middle (58–67) · Q4 Bottom Quartile (&lt;58).
  The facility detail table lists each of the network's {plural} for reference and is not separately scored.
  <strong>External signals:</strong> {quality_note}
</p>
</div>"""
