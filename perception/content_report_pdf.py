"""Report 2 — the detailed Content Improvement report (the future CIP format).

Expands each cached ContentFinding into a full section: evidence, current vs.
expected state, and remediation type — in the SAME order and with the SAME
CIK-### ids as the teaser in Report 1 (the consistency contract). Drafted,
publication-ready content (`draft_content`) is reserved for the remediation
phase and rendered when present; in the sandbox it's absent.
"""
from __future__ import annotations

import html
import re
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
             "llms_txt": "llms.txt", "wikidata": "Wikidata", "wikipedia": "Wikipedia",
             "reputation": "Reputation & Listings", "directory": "Provider Directories",
             "service_line": "Service Lines", "safety": "Safety & Quality"}
_REMEDIATION = {"schema_markup": "Add schema.org markup", "website_fix": "Website fix",
                "wikidata_edit": "Wikidata edit (we can draft, you publish)",
                "talk_page_request": "Wikipedia talk-page request",
                "directory_update": "Directory update", "monitor_respond": "Monitor & respond",
                "reputation_program": "Reputation / review-generation program (RLDatix Reputation Management)",
                "listing_management": "Google Business Profile / listings management (RLDatix Reputation Management)",
                "leapfrog_submission": "Participate in the Leapfrog Hospital Survey",
                "quality_improvement": "Patient-experience surveys & CMS quality (RLDatix Patient Experience & Growth)"}

# Remediation types whose drafted output is literal content the client PUBLISHES
# (website/knowledge-graph). Everything else is an operational plan to implement,
# so it must NOT be labeled "ready to publish".
_PUBLISHABLE_REMEDIATION = {"schema_markup", "website_fix", "wikidata_edit", "talk_page_request"}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


# Each finding block carries a hidden, page-searchable marker so a second render
# pass can find the exact printed page a finding lands on (Chromium doesn't expose
# page numbers at build time). The marker (e.g. ZZCIK001ZZ) is unique to the block
# and does NOT appear in the Contents list, so it never false-matches the cover.
_SL_ANCHOR = "ZZSERVICELINEZZ"


def _anchor_token(fid) -> str:
    return "ZZ" + re.sub(r"[^A-Za-z0-9]", "", str(fid or "").upper()) + "ZZ"


def _hidden_marker(token: str) -> str:
    return f'<span style="color:#fff;font-size:1px;line-height:0">{token}</span>'


# Display normalization so the header reads in proper title case, matching the
# Deep Diagnostic (which uses the analyzed canonical name). Applied to the
# entity/report title and the location — never to user content elsewhere.
import re as _re

_SMALL_WORDS = {"a", "an", "and", "the", "of", "for", "to", "at", "in", "on",
                "by", "or", "nor", "vs", "de", "la"}


def _cap_word(w: str, first: bool) -> str:
    if not any(ch.isalpha() for ch in w):
        return w                              # leave hashes/numbers/punct alone
    if w.isupper() and sum(ch.isalpha() for ch in w) <= 4:
        return w                              # preserve acronyms/state codes (USA, MD, NC)
    if not first and w.lower() in _SMALL_WORDS:
        return w.lower()
    if w.isupper():
        w = w.lower()                         # ATRIUM -> atrium, then capitalize below

    def _capfirst(s: str) -> str:
        for i, ch in enumerate(s):
            if ch.isalpha():
                return s[:i] + ch.upper() + s[i + 1:]
        return s
    # capitalize each hyphen-separated part; keep internal caps (McDonald, RLDatix)
    return "-".join(_capfirst(part) for part in w.split("-"))


def _display_name(s: str) -> str:
    """Title-case an entity/report name, preserving acronyms and casing small words."""
    s = (s or "").strip()
    if not s:
        return s
    out, seen = [], False
    for tok in _re.split(r"(\s+)", s):
        if not tok.strip():
            out.append(tok)
            continue
        out.append(_cap_word(tok, not seen))
        seen = True
    return "".join(out)


def _display_location(s: str) -> str:
    """Normalize 'Charlotte , nc' -> 'Charlotte, NC' (spacing, title case, state code)."""
    s = (s or "").strip()
    if not s:
        return s
    s = _re.sub(r"\s*,\s*", ", ", s)
    parts = [_display_name(p) for p in s.split(", ")]
    if parts and _re.fullmatch(r"[A-Za-z]{2}", parts[-1]):
        parts[-1] = parts[-1].upper()
    return ", ".join(parts)


def _logo_html() -> str:
    import base64
    p = Path(__file__).parent / "assets" / "logo-white.svg"
    if p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/svg+xml;base64,{data}" style="height:32px" alt="RLDatix">'
    return '<div style="color:#fff;font-weight:700;font-size:20px">Pulse</div>'


def _page_map(pdf_path, items, has_service_line: bool) -> dict:
    """After a first render, read the produced PDF and locate the printed page each
    finding block (and the service-line section) landed on, by searching per-page
    text for the block's hidden marker. Returns {finding_id/_SL_ANCHOR: page_no}.
    Best-effort: any item we can't locate is simply omitted."""
    try:
        from pypdf import PdfReader
    except Exception:
        return {}
    try:
        reader = PdfReader(str(pdf_path))
        norm = []
        for pg in reader.pages:
            try:
                norm.append(re.sub(r"[^A-Za-z0-9]", "", (pg.extract_text() or "")).upper())
            except Exception:
                norm.append("")
    except Exception:
        return {}
    out = {}
    tokens = [(f.get("finding_id"), _anchor_token(f.get("finding_id"))) for f in items]
    if has_service_line:
        tokens.append((_SL_ANCHOR, _SL_ANCHOR))
    for key, tok in tokens:
        for i, page_text in enumerate(norm):
            if tok in page_text:
                out[key] = i + 1
                break
    return out


def render_content_report_pdf(entity_name: str, location: str, findings, pdf_path: str,
                              report_title: str = "", service_line=None) -> None:
    """Render Report 2 (detailed content findings) to a branded PDF. `service_line`,
    when given, is (ServiceLineSummary, [ServiceLineScorecard]) and adds the
    Service-Line Listing Management section (Report 2 only).

    Rendered in two passes: the first lays out the report so we can read back which
    page each finding lands on; the second fills those page numbers into the cover
    Contents list. The finding layout is identical between passes (only the tiny
    page-number strings change), so the located pages stay valid."""
    from playwright.sync_api import sync_playwright
    raw = list(getattr(findings, "findings", []) or [])
    items = [f.model_dump() if hasattr(f, "model_dump") else f for f in raw]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def _emit(page_map):
            page.set_content(
                _build_html(entity_name, location, findings, report_title, service_line, page_map),
                wait_until="networkidle")
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

        _emit(None)  # measurement pass
        pm = _page_map(pdf_path, items, bool(service_line)) if items else {}
        if pm:
            _emit(pm)  # final pass with real page numbers
        browser.close()


def _rating_color(r) -> str:
    if r is None:
        return _MUTE
    if r < 3.5:
        return "#d94f4f"
    if r < 4.0:
        return "#e09b2a"
    return "#2e9e5b"


def _finding_block(f: dict) -> str:
    sev = f.get("severity", "low")
    sc = _SEV.get(sev, "#7a9095")
    dot_c, dot_l = _STATUS.get(f.get("status", "verified"), ("#9aa8ac", f.get("status", "")))
    rows = (f.get("meta") or {}).get("rows") or []
    if rows:
        # Grouped finding (e.g. many weak-reputation locations): a per-location
        # table replaces the evidence list, and the single drafted template below
        # applies to every row — printed once, not once per location.
        trs = "".join(
            f'<tr><td style="padding:5px 8px;border-bottom:1px solid #eef4f2;font-size:9pt">{_e(r.get("name"))}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eef4f2;font-size:9pt;color:{_MUTE}">{_e(r.get("location")) or "&mdash;"}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eef4f2;text-align:center;font-weight:700;color:{_rating_color(r.get("rating"))}">'
            f'{(str(r.get("rating")) + "&#9733;") if r.get("rating") is not None else "&mdash;"}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eef4f2;text-align:center;font-size:9pt">{r.get("reviews") or 0}</td></tr>'
            for r in rows)
        ev_html = (f'<div class="lbl">Locations ({len(rows)})</div>'
                   f'<table class="rgtbl"><thead><tr><th style="text-align:left">Facility</th>'
                   f'<th style="text-align:left">Location</th><th>Current</th><th>Reviews</th></tr></thead>'
                   f'<tbody>{trs}</tbody></table>')
    else:
        ev = f.get("evidence") or []
        ev_html = ('<div class="lbl">Evidence</div><ul class="ev">'
                   + "".join(f'<li>{_e(x)}</li>' for x in ev) + '</ul>') if ev else ""
    rem = _REMEDIATION.get(f.get("remediation_type", ""), f.get("remediation_type", ""))
    draft = f.get("draft_content")
    draft_html = ""
    if draft:
        publishable = f.get("remediation_type", "") in _PUBLISHABLE_REMEDIATION
        if rows:
            # grouped operational program (e.g. weak-reputation locations)
            _lbl = "Recommended program — one template for all locations above (swap [FACILITY]/[CITY] per location)"
        elif publishable:
            _lbl = "Drafted content (ready to publish)"
        else:
            _lbl = "Recommended action plan (for your team to implement)"
        draft_html = (f'<div class="lbl">{_lbl}</div>'
                      f'<pre class="draft">{_e(draft)}</pre>')
    return f"""
    <div class="finding">
      {_hidden_marker(_anchor_token(f.get("finding_id")))}
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


_SL_STATUS_COLOR = {"managed": "#2e9e5b", "partial": "#e09b2a",
                    "unmanaged": "#d94f4f", "invisible": "#8a1f1f"}


def _grade_color(v) -> str:
    if v is None:
        return "#9aa8ac"
    if v >= 75:
        return "#2e9e5b"
    if v >= 55:
        return "#e09b2a"
    return "#d94f4f"


def _sl_cell(v) -> str:
    txt = "&mdash;" if v is None else str(v)
    return (f'<td style="text-align:center;font-weight:700;padding:6px 8px;'
            f'border-bottom:1px solid #eef4f2;color:{_grade_color(v)}">{txt}</td>')


def _service_line_section(summary, scorecards) -> str:
    """Report 2 'Service-Line Listing Management' section: summary box + scorecard table."""
    if not scorecards:
        return ""
    avg = f" &middot; avg patient-attraction score <b>{summary.avg_overall}</b>" if summary.avg_overall is not None else ""
    counts = (f'{summary.managed} managed &middot; {summary.partial} partial &middot; '
              f'{summary.unmanaged} unmanaged &middot; {summary.invisible} invisible')
    cross = "".join(f"<li>{_e(c)}</li>" for c in (summary.cross_cutting or []))
    cross_html = f'<ul class="slcross">{cross}</ul>' if cross else ""
    note = f'<div class="slnote">{_e(summary.sampling_note)}</div>' if summary.sampling_note else ""

    rows = ""
    for c in scorecards:
        dims = {d.key: d.score for d in c.dimensions}
        sc = _SL_STATUS_COLOR.get(c.management_status, "#5a6e72")
        rows += (
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eef4f2;font-size:9pt">{_e(c.canonical_label)}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eef4f2;font-size:8pt;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:.03em;color:{sc}">{_e(c.management_status)}</td>'
            f'{_sl_cell(c.overall_score)}{_sl_cell(dims.get("findability"))}{_sl_cell(dims.get("completeness"))}'
            f'{_sl_cell(dims.get("reputation"))}{_sl_cell(dims.get("content"))}</tr>')

    return f"""<div class="slwrap">
      {_hidden_marker(_SL_ANCHOR)}
      <div class="slh">Service-Line Listing Management</div>
      <div class="slbox"><b>{summary.total_lines} service line{'s' if summary.total_lines != 1 else ''} analyzed</b> &mdash; {counts}{avg}.{note}{cross_html}</div>
      <table class="sltbl">
        <thead><tr><th style="text-align:left">Service line</th><th style="text-align:left">Status</th>
        <th>Overall</th><th>Find</th><th>Complete</th><th>Reput</th><th>Content</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""


def _contents_section(items, has_service_line: bool, page_map) -> str:
    """The cover-page Contents list: every item from the Deep Diagnostic summary,
    with the page it's addressed on. `page_map` is {finding_id: page_no} (and
    _SL_ANCHOR for the service-line section) once the first render pass has located
    each block; None on the measurement pass (page numbers show as a dash)."""
    if not items and not has_service_line:
        return ""

    def _pg(key) -> str:
        n = (page_map or {}).get(key)
        return f"p.&nbsp;{n}" if n else "&mdash;"

    rows = ""
    for f in items:
        sev = f.get("severity", "low")
        sc = _SEV.get(sev, "#7a9095")
        plat = _PLATFORM.get(f.get("platform"), f.get("platform")) or ""
        rows += (
            f'<div class="toc-row">'
            f'<span class="toc-id">{_e(f.get("finding_id"))}</span>'
            f'<span class="toc-sev" style="background:{sc}">{_e(sev)}</span>'
            f'<span class="toc-title">{_e(f.get("teaser_summary"))}'
            f'<span class="toc-plat">{_e(plat)}</span></span>'
            f'<span class="toc-pg">{_pg(f.get("finding_id"))}</span></div>')
    if has_service_line:
        rows += (
            f'<div class="toc-row">'
            f'<span class="toc-id">&mdash;</span>'
            f'<span class="toc-sev" style="background:{_TEAL2}">deep&nbsp;dive</span>'
            f'<span class="toc-title">Service-Line Listing Management'
            f'<span class="toc-plat">Per service line: findability, completeness, reputation, content</span></span>'
            f'<span class="toc-pg">{_pg(_SL_ANCHOR)}</span></div>')

    n = len(items) + (1 if has_service_line else 0)
    return f"""<div class="toc">
      <div class="toc-lead">The {n} item{'s' if n != 1 else ''} below map to your Deep Diagnostic summary.
        Here's where each one is addressed in detail:</div>
      <div class="toc-list">{rows}</div>
    </div>"""


def _content_css(include_reset: bool = True) -> str:
    """The Content Report's CSS. `include_reset=False` omits the global *,body
    rules so the block can be embedded inside another document (e.g. the Hospital
    Network Full Detail report) without overriding that document's base styles."""
    reset = (f"""
      * {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ font-family:'Inter','Helvetica Neue',Arial,sans-serif; color:{_INK}; }}""" if include_reset else "")
    return reset + f"""
      .band {{ background:{_TEAL}; color:#fff; padding:26px 44px; }}
      .band .top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
      .band .kick {{ font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:#9FD8CF; margin-bottom:8px; }}
      .band h1 {{ font-size:23px; font-weight:700; margin:2px 0 4px; }}
      .band .sub {{ font-size:13px; color:#CFEAE6; }}
      .meta {{ padding:14px 44px; background:{_PALE}; font-size:11px; color:{_MUTE};
               border-bottom:1px solid #d7e7e2; line-height:1.6; }}
      .meta b {{ color:{_TEAL}; }}
      .intro {{ padding:16px 44px 4px; font-size:11pt; color:{_INK}; line-height:1.55; }}
      .toc {{ padding:12px 44px 20px; page-break-after:always; }}
      .toc-lead {{ font-size:10.5pt; color:{_INK}; line-height:1.5; margin-bottom:14px; }}
      .toc-list {{ border-top:2px solid {_TEAL}; }}
      .toc-row {{ display:flex; align-items:center; gap:11px; padding:9px 2px;
                  border-bottom:1px solid #e8f0ee; page-break-inside:avoid; }}
      .toc-id {{ font-family:monospace; font-size:8.5pt; color:{_MUTE}; min-width:56px; }}
      .toc-sev {{ color:#fff; font-size:7pt; font-weight:700; padding:2px 8px; border-radius:9px;
                  text-transform:uppercase; white-space:nowrap; }}
      .toc-title {{ flex:1; font-size:10pt; font-weight:600; color:{_TEAL}; line-height:1.35; }}
      .toc-plat {{ display:block; font-size:8pt; font-weight:600; color:{_TEAL2};
                   text-transform:none; margin-top:2px; }}
      .toc-pg {{ font-size:9.5pt; font-weight:700; color:{_INK}; white-space:nowrap; min-width:42px; text-align:right; }}
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
      .slwrap {{ padding:6px 44px 8px; }}
      .slh {{ font-size:13pt; font-weight:700; color:{_TEAL}; margin:10px 0 8px; }}
      .slbox {{ background:#f5faf8; border:1px solid #d7e7e2; border-radius:8px;
                padding:12px 16px; font-size:9.5pt; color:{_INK}; line-height:1.5; }}
      .slnote {{ font-size:8.5pt; color:{_MUTE}; margin-top:4px; }}
      .slcross {{ margin:8px 0 0 18px; }} .slcross li {{ font-size:9pt; color:{_INK}; line-height:1.5; }}
      .sltbl {{ width:100%; border-collapse:collapse; margin-top:12px; }}
      .sltbl th {{ background:#eef6f3; font-size:7.5pt; font-weight:700; text-transform:uppercase;
                   letter-spacing:.04em; color:{_MUTE}; padding:7px 8px; text-align:center; }}
      .rgtbl {{ width:100%; border-collapse:collapse; margin:4px 0 6px; }}
      .rgtbl th {{ background:#f0f6f7; font-size:7pt; font-weight:700; text-transform:uppercase;
                   letter-spacing:.04em; color:{_MUTE}; padding:5px 8px; text-align:center; }}
    """


def _content_body_html(entity_name: str, location: str, findings, report_title: str,
                       service_line=None, page_map=None) -> str:
    """The Content Report's inner body (band → meta → intro → contents index →
    service-line → findings → method note). Reused both standalone and embedded in
    the Hospital Network Full Detail report."""
    items = list(getattr(findings, "findings", []) or [])
    items = [f.model_dump() if hasattr(f, "model_dump") else f for f in items]
    snap = getattr(findings, "source_snapshot", {}) or {}
    title = _display_name(report_title or entity_name)
    location = _display_location(location)

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
    sl_section = ""
    if service_line:
        _summary, _cards = service_line
        sl_section = _service_line_section(_summary, _cards)
    toc = _contents_section(items, bool(sl_section), page_map)

    return f"""
      <div class="band">
        <div class="top">{_logo_html()}<div style="text-align:right;font-size:10px;letter-spacing:.1em;color:#9FD8CF">AI VISIBILITY<br>REPORT</div></div>
        <h1>Content Analysis &mdash; Detailed Findings and Improvement Prescriptions</h1>
        <div class="sub">{_e(title)}{(" &middot; " + _e(location)) if location else ""}</div>
      </div>
      <div class="meta">
        <b>{summary}</b><br>
        Sources analyzed: {_e(urls)} &middot; {pages} page(s) crawled &middot; live Wikidata &amp; Wikipedia checks &middot; {date.today():%B %-d, %Y}
      </div>
      <div class="intro">This report goes deep on every content-visibility finding from your Deep
        Diagnostic — where the sources AI assistants read are missing, outdated, or inconsistent — with the
        evidence behind each one and the specific fix. Findings keep the same order and IDs as your Deep
        Diagnostic summary.</div>
      {toc}
      {sl_section}
      <div class="wrap">{blocks}</div>
      <div class="method"><b>How to read this.</b> Findings are drawn from live checks of your website
        (schema.org structured data, llms.txt, AI-crawler access), Wikidata, and Wikipedia — not estimates.
        Status: <b>Verified</b> = confirmed by direct check; <b>Partial</b> = checked but needs human
        confirmation; <b>Not assessed</b> = the source couldn't be reached at analysis time.
        <b>Drafted content (ready to publish)</b> is copy you can put live (schema markup, llms.txt,
        Wikidata/Wikipedia edits); a <b>Recommended action plan</b> is an operational playbook for your
        team (e.g. reputation programs, Google Business Profile fixes) — steps to implement, not copy to publish.</div>"""


def _build_html(entity_name: str, location: str, findings, report_title: str,
                service_line=None, page_map=None) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_content_css(True)}</style>'
            f'</head><body>{_content_body_html(entity_name, location, findings, report_title, service_line, page_map)}'
            f'</body></html>')
