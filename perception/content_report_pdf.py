"""Report 2 — the detailed Content Improvement report (the future CIP format).

Expands each cached ContentFinding into a full section: evidence, current vs.
expected state, and remediation type — in the SAME order and with the SAME
CIK-### ids as the teaser in Report 1 (the consistency contract). Drafted,
publication-ready content (`draft_content`) is reserved for the remediation
phase and rendered when present; in the sandbox it's absent.
"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

_TEAL = "#0F4146"
_TEAL2 = "#177B6E"
_PALE = "#EEF7F1"
_INK = "#2B3A3D"
_MUTE = "#5A6E72"

_SEV = {"high": "#d94f4f", "medium": "#e09b2a", "low": "#7a9095"}
_STATUS = {"verified": ("#2e9e5b", "Verified"),
           "partial": ("#e09b2a", "Partial"),
           "not_assessed": ("#9aa8ac", "Not assessed")}
_PLATFORM = {"structured_data": "Structured data (schema.org)", "website": "Website",
             "llms_txt": "llms.txt", "wikidata": "Wikidata", "wikipedia": "Wikipedia"}
_REMEDIATION = {"schema_markup": "Add schema.org markup", "website_fix": "Website fix",
                "wikidata_edit": "Wikidata edit (we can draft, you publish)",
                "talk_page_request": "Wikipedia talk-page request",
                "directory_update": "Directory update", "monitor_respond": "Monitor & respond"}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _logo_html() -> str:
    import base64
    p = Path(__file__).parent / "assets" / "logo-white.svg"
    if p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/svg+xml;base64,{data}" style="height:32px" alt="RLDatix">'
    return '<div style="color:#fff;font-weight:700;font-size:20px">Pulse</div>'


def render_content_report_pdf(entity_name: str, location: str, findings, pdf_path: str,
                              report_title: str = "") -> None:
    """Render Report 2 (detailed content findings) to a branded PDF."""
    from playwright.sync_api import sync_playwright
    html_str = _build_html(entity_name, location, findings, report_title)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_str, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path), format="Letter",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True, display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font-family:Arial,sans-serif;font-size:8px;'
                'color:#8a9aaa;display:flex;justify-content:space-between;align-items:center;'
                'padding:0 44px 10px;box-sizing:border-box">'
                '<span style="letter-spacing:0.05em">Prepared by Pulse | RLDatix &nbsp;&mdash;&nbsp; Confidential</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                '</div>'
            ),
        )
        browser.close()


def _finding_block(f: dict) -> str:
    sev = f.get("severity", "low")
    sc = _SEV.get(sev, "#7a9095")
    dot_c, dot_l = _STATUS.get(f.get("status", "verified"), ("#9aa8ac", f.get("status", "")))
    ev = f.get("evidence") or []
    ev_html = ""
    if ev:
        ev_html = ('<div class="lbl">Evidence</div><ul class="ev">'
                   + "".join(f'<li>{_e(x)}</li>' for x in ev) + '</ul>')
    rem = _REMEDIATION.get(f.get("remediation_type", ""), f.get("remediation_type", ""))
    draft = f.get("draft_content")
    draft_html = ""
    if draft:
        draft_html = (f'<div class="lbl">Drafted content (ready to publish)</div>'
                      f'<pre class="draft">{_e(draft)}</pre>')
    return f"""
    <div class="finding">
      <div class="fhead">
        <span class="fid">{_e(f.get("finding_id"))}</span>
        <span class="sev" style="background:{sc}">{_e(sev)}</span>
        <span class="plat">{_e(_PLATFORM.get(f.get("platform"), f.get("platform")))}</span>
        <span class="stat"><span style="color:{dot_c}">&#9679;</span> {dot_l}</span>
      </div>
      <div class="fsum">{_e(f.get("teaser_summary"))}</div>
      <div class="grid">
        <div><div class="lbl">Current state</div><div class="val">{_e(f.get("current_state")) or "&mdash;"}</div></div>
        <div><div class="lbl">Expected state</div><div class="val">{_e(f.get("expected_state")) or "&mdash;"}</div></div>
      </div>
      {ev_html}
      <div class="lbl">Recommended remediation</div>
      <div class="val">{_e(rem) or "&mdash;"}</div>
      {draft_html}
    </div>"""


def _build_html(entity_name: str, location: str, findings, report_title: str) -> str:
    items = list(getattr(findings, "findings", []) or [])
    items = [f.model_dump() if hasattr(f, "model_dump") else f for f in items]
    snap = getattr(findings, "source_snapshot", {}) or {}
    title = report_title or entity_name

    by_sev = {"high": 0, "medium": 0, "low": 0}
    for f in items:
        by_sev[f.get("severity", "low")] = by_sev.get(f.get("severity", "low"), 0) + 1

    if items:
        summary = (f'<strong>{len(items)} item{"s" if len(items)!=1 else ""}</strong> — '
                   f'{by_sev["high"]} high, {by_sev["medium"]} medium, {by_sev["low"]} low.')
        blocks = "".join(_finding_block(f) for f in items)
    else:
        summary = "No content-visibility issues were detected, or sources could not be assessed."
        blocks = '<div class="finding"><div class="fsum">Nothing to detail.</div></div>'

    urls = ", ".join(snap.get("website_urls", []) or []) or "&mdash;"
    pages = snap.get("pages_crawled", 0)

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      * {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ font-family:'Inter','Helvetica Neue',Arial,sans-serif; color:{_INK}; }}
      .band {{ background:{_TEAL}; color:#fff; padding:26px 44px; }}
      .band .top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
      .band .kick {{ font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:#9FD8CF; margin-bottom:8px; }}
      .band h1 {{ font-size:23px; font-weight:700; margin:2px 0 4px; }}
      .band .sub {{ font-size:13px; color:#CFEAE6; }}
      .meta {{ padding:14px 44px; background:{_PALE}; font-size:11px; color:{_MUTE};
               border-bottom:1px solid #d7e7e2; line-height:1.6; }}
      .meta b {{ color:{_TEAL}; }}
      .intro {{ padding:16px 44px 4px; font-size:11pt; color:{_INK}; line-height:1.55; }}
      .wrap {{ padding:8px 44px 24px; }}
      .finding {{ border:1px solid #e2ece9; border-radius:8px; padding:14px 16px; margin:12px 0;
                  page-break-inside:avoid; }}
      .fhead {{ display:flex; align-items:center; gap:10px; margin-bottom:6px; }}
      .fid {{ font-family:monospace; font-size:9pt; color:{_MUTE}; }}
      .sev {{ color:#fff; font-size:7.5pt; font-weight:700; padding:2px 8px; border-radius:9px; text-transform:uppercase; }}
      .plat {{ font-size:9pt; color:{_TEAL2}; font-weight:600; }}
      .stat {{ font-size:8.5pt; color:{_MUTE}; margin-left:auto; }}
      .fsum {{ font-size:11pt; font-weight:600; color:{_TEAL}; margin-bottom:10px; line-height:1.4; }}
      .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:8px; }}
      .lbl {{ font-size:7.5pt; font-weight:700; color:{_MUTE}; text-transform:uppercase; letter-spacing:.05em; margin:8px 0 3px; }}
      .val {{ font-size:9.5pt; color:{_INK}; line-height:1.5; }}
      ul.ev {{ margin:2px 0 0 16px; }} ul.ev li {{ font-size:9pt; color:{_MUTE}; word-break:break-all; line-height:1.5; }}
      pre.draft {{ background:{_PALE}; border:1px solid #d0e4e8; border-radius:6px; padding:10px 12px;
                   font-size:8.5pt; white-space:pre-wrap; color:{_INK}; margin-top:3px; }}
      .method {{ margin:12px 44px 24px; padding:12px 16px; background:{_PALE}; border-radius:8px;
                 font-size:9pt; color:{_MUTE}; line-height:1.55; }}
      .method b {{ color:{_TEAL}; }}
    </style></head><body>
      <div class="band">
        <div class="top">{_logo_html()}<div style="text-align:right;font-size:10px;letter-spacing:.1em;color:#9FD8CF">AI VISIBILITY<br>REPORT</div></div>
        <h1>Content Analysis &mdash; Detailed Findings and Improvement Prescriptions</h1>
        <div class="sub">{_e(title)}{(" &middot; " + _e(location)) if location else ""}</div>
      </div>
      <div class="meta">
        <b>{summary}</b><br>
        Sources analyzed: {_e(urls)} &middot; {pages} page(s) crawled &middot; live Wikidata &amp; Wikipedia checks &middot; {date.today():%B %-d, %Y}
      </div>
      <div class="intro">Each item below is a verified content-visibility finding — where the sources AI
        assistants read are missing, outdated, or inconsistent — with the evidence behind it and the
        recommended remediation. Items appear in the same order and with the same IDs as the summary in
        your Deep Diagnostic.</div>
      <div class="wrap">{blocks}</div>
      <div class="method"><b>How to read this.</b> Findings are drawn from live checks of your website
        (schema.org structured data, llms.txt, AI-crawler access), Wikidata, and Wikipedia — not estimates.
        Status: <b>Verified</b> = confirmed by direct check; <b>Partial</b> = checked but needs human
        confirmation; <b>Not assessed</b> = the source couldn't be reached at analysis time. Drafted,
        publication-ready content for each item is available in the full remediation engagement.</div>
    </body></html>"""
