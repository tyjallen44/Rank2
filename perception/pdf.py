from __future__ import annotations

import base64
import html as _html_lib
import re
from pathlib import Path

from .models import AffiliationType, AnalysisResult, RankedProvider, SizeCategory
from .scoring import TIER_LABELS, PRACTICE_TIER_LABELS, PRACTICE_PROFILE_DISPLAY, grade_from_score
from .strings import (
    COVER_MARKET, COVER_PATIENT, COVER_PATIENT_TEASER,
    COVER_INDIVIDUAL, COVER_INDIVIDUAL_TEASER,
    COVER_COMPARISON, COVER_REPORT_SUB,
    SCORE_LABEL, SCORE_DESCRIPTOR,
    SECTION_VERDICT, SECTION_ASSESSMENT,
    SECTION_COMPARISON_OVERVIEWS,
    SECTION_COMPARISON_SCORE_LABEL, SECTION_COMPARISON_VERDICT_LABEL,
    ROADMAP_TITLE,
    BLUR_CTA_INDIVIDUAL, BLUR_CTA_COMPARISON,
    RANKED_TEASER_SUBTITLE, RANKED_PATIENT_SUBTITLE,
    MARKET_ADVICE_CTA, DEEP_DIVE_HEADER_TPL,
)


def _tier_labels(profile: str | None) -> dict[str, str]:
    """Return the correct tier-label dict for a given weighting profile."""
    if profile and profile.startswith("practice_"):
        return PRACTICE_TIER_LABELS.get(profile, PRACTICE_TIER_LABELS["practice_procedural"])
    return TIER_LABELS.get(profile or "procedural", TIER_LABELS["procedural"])

# RLDatix brand palette (original)
_TEAL        = "#0F4146"
_QUARTILE_COLORS = {"Q1": "#2e9e5b", "Q2": "#2e7d9a", "Q3": "#e09b2a", "Q4": "#d94f4f"}
# Reader-facing quartile labels — "Q2" is ambiguous (reads as fiscal quarter).
_QUARTILE_LABELS = {"Q1": "1st Quartile", "Q2": "2nd Quartile", "Q3": "3rd Quartile", "Q4": "4th Quartile"}


def _quartile_label(q: str) -> str:
    return _QUARTILE_LABELS.get(q, q)


def _score_bar_color(score: int | None) -> str:
    """Threshold colors for a 0-100 score bar. Single source of truth shared by
    the four pillar bars (every report card) and the Network Pulse Score
    Breakdown so they always match: <35 red, 35-64 amber, ≥65 green (None → red)."""
    if score is None or score < 35:
        return "#d94f4f"   # red   — bottom
    if score < 65:
        return "#e09b2a"   # amber — middle
    return "#2e9e5b"       # green — strong
_PALE_GREEN  = "#EEF7F1"
_SEAFOAM     = "#80F8E4"
_BLUE        = "#73D2E1"
_BLUE_LIGHT  = "#DCF4F8"
_GREEN       = "#5ADCA0"

# Rank badge colors (1=teal, 2=blue, 3=green, rest=muted blue)
_RANK_COLORS = {1: _TEAL, 2: _BLUE, 3: _GREEN}
_RANK_DEFAULT = "#96DDE9"

_LOGO_PATH = Path(__file__).parent / "assets" / "logo-white.svg"

# Brand configurations: color overrides + optional text logo
_BRAND_CONFIGS: dict[str, dict] = {
    "original": {
        "primary": _TEAL,
        "pale":    _PALE_GREEN,
        "accent":  _SEAFOAM,
        "logo_html": None,
    },
    "extension1": {
        "primary": "#9B1C22",
        "pale":    "#FBF0F1",
        "accent":  "#C8888C",
        "logo_html": (
            '<div class="cover-logo-wordmark">'
            'Montecito<span class="wm-sub">Medical</span>'
            '</div>'
        ),
    },
    "extension2": {
        "primary":   "#3E332A",   # espresso/walnut
        "pale":      "#F6F1E9",   # linen
        "accent":    "#8C9A82",   # dusty sage
        "logo_html": None,        # resolved lazily from logo_path below
        "logo_path": Path(__file__).parent / "assets" / "ashleigh-jane-wordmark-reverse.svg",
        "css_overrides": (
            "    .accent-bar { background: #8C9A82; }\n"
            "    .cover-meta { border-top-color: rgba(140,154,130,0.3); }\n"
        ),
    },
}


_PHYSICIAN_COUNT_MAP = {
    "small":  "small number of",
    "few":    "a few",
    "large":  "large number of",
    "many":   "many",
    "several":"several",
}

def _physician_label(count: str) -> str:
    """Turn raw physician_count into a readable pill label."""
    normalized = count.strip().lower()
    prefix = _PHYSICIAN_COUNT_MAP.get(normalized, count.strip())
    return f"{prefix} physicians"


def _logo_data_uri() -> str:
    if _LOGO_PATH.exists():
        data = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
        return f"data:image/svg+xml;base64,{data}"
    return ""


def _content_keys_section(findings) -> str:
    """Content Improvement Keys — the teaser section appended to Report 1.

    Self-contained inline styles (no dependency on the report's CSS) so it can be
    injected into the deep-dive HTML without touching the shared builder.
    `findings` is a ContentFindings object (may be empty / not_assessed)."""
    items = list(getattr(findings, "findings", []) or [])
    status = getattr(findings, "status", "not_assessed")
    snap = getattr(findings, "source_snapshot", {}) or {}

    sev_color = {"high": "#d94f4f", "medium": "#e09b2a", "low": "#7a9095"}
    st_dot = {"verified": ("#2e9e5b", "Verified"),
              "partial": ("#e09b2a", "Partial"),
              "not_assessed": ("#9aa8ac", "Not assessed")}
    plat_label = {"structured_data": "Structured data", "website": "Website",
                  "llms_txt": "llms.txt", "wikidata": "Wikidata", "wikipedia": "Wikipedia",
                  "reputation": "Reputation"}

    if not items:
        body = ('<p style="font-size:10.5pt;color:#3a5a60;margin:0">'
                'No content-visibility issues were detected, or the sources could not be '
                'assessed at analysis time. A full Content Improvement Plan can re-check on request.</p>')
        counts_strip = ""
    else:
        # counts by severity + platforms touched
        by_sev = {"high": 0, "medium": 0, "low": 0}
        plats = set()
        for f in items:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            plats.add(plat_label.get(f.platform, f.platform))
        counts_strip = (
            f'<div style="background:#f1f7f6;border:1px solid #d0e4e8;border-radius:6px;'
            f'padding:10px 14px;margin:0 0 14px;font-size:9.5pt;color:#3a5a60">'
            f'<strong>{len(items)} item{"s" if len(items)!=1 else ""} identified</strong> — '
            f'{by_sev.get("high",0)} high, {by_sev.get("medium",0)} medium, {by_sev.get("low",0)} low, '
            f'across {", ".join(sorted(plats))}.</div>'
        )
        shown = items[:8]
        rows = []
        for f in shown:
            sc = sev_color.get(f.severity, "#7a9095")
            dot_c, dot_l = st_dot.get(f.status, ("#9aa8ac", f.status))
            rows.append(
                f'<tr>'
                f'<td style="padding:7px 8px;border-bottom:1px solid #eef3f2;font-family:monospace;'
                f'font-size:8pt;color:#7a9095;white-space:nowrap">{_e(f.finding_id)}</td>'
                f'<td style="padding:7px 8px;border-bottom:1px solid #eef3f2;font-size:8.5pt;'
                f'color:#177B6E;white-space:nowrap">{_e(plat_label.get(f.platform, f.platform))}</td>'
                f'<td style="padding:7px 8px;border-bottom:1px solid #eef3f2;font-size:9pt;color:#2b3a3d">{_e(f.teaser_summary)}</td>'
                f'<td style="padding:7px 8px;border-bottom:1px solid #eef3f2;white-space:nowrap">'
                f'<span style="background:{sc};color:#fff;font-size:7.5pt;font-weight:700;'
                f'padding:2px 7px;border-radius:9px;text-transform:uppercase">{_e(f.severity)}</span></td>'
                f'<td style="padding:7px 8px;border-bottom:1px solid #eef3f2;font-size:8pt;'
                f'color:#5a6e72;white-space:nowrap"><span style="color:{dot_c}">&#9679;</span> {dot_l}</td>'
                f'</tr>'
            )
        more = (f'<div style="font-size:8.5pt;color:#7a9095;margin-top:8px">'
                f'Showing the top 8 of {len(items)} items by severity — the full Content '
                f'Improvement Plan details every one.</div>') if len(items) > 8 else ""
        body = (
            counts_strip +
            '<table style="width:100%;border-collapse:collapse;margin:0">'
            '<thead><tr style="background:#0F4146;color:#fff">'
            '<th style="text-align:left;padding:7px 8px;font-size:8pt;text-transform:uppercase;letter-spacing:.04em">ID</th>'
            '<th style="text-align:left;padding:7px 8px;font-size:8pt;text-transform:uppercase;letter-spacing:.04em">Platform</th>'
            '<th style="text-align:left;padding:7px 8px;font-size:8pt;text-transform:uppercase;letter-spacing:.04em">Finding</th>'
            '<th style="text-align:left;padding:7px 8px;font-size:8pt;text-transform:uppercase;letter-spacing:.04em">Severity</th>'
            '<th style="text-align:left;padding:7px 8px;font-size:8pt;text-transform:uppercase;letter-spacing:.04em">Status</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>' + more
        )

    from datetime import date as _date
    pages = snap.get("pages_crawled", 0)
    src_note = (f'<div style="font-size:8pt;color:#9aa8ac;margin-top:6px">Verified from '
                f'{pages} page(s) crawled plus live Wikidata and Wikipedia checks '
                f'on {_date.today():%m/%d/%Y}.</div>') if pages else ""

    cta = (
        '<div style="margin-top:16px;background:#EEF7F1;border-left:4px solid #177B6E;'
        'border-radius:6px;padding:12px 16px">'
        '<div style="font-size:10pt;font-weight:700;color:#0F4146;margin-bottom:3px">'
        'Request your Content Improvement Plan</div>'
        '<div style="font-size:9pt;color:#3a5a60;line-height:1.5">A detailed, prioritized '
        'remediation roadmap for every item above — including publication-ready content '
        'where platform policies allow.</div></div>'
    )

    return (
        '<div style="page-break-before:always;padding:0 40px">'
        '<div style="font-size:13pt;font-weight:700;color:#0F4146;margin:0 0 4px">Content Improvement Keys</div>'
        '<p style="font-size:9.5pt;color:#3a5a60;line-height:1.5;margin:0 0 14px">'
        'AI assistants form their picture of a provider from many sources beyond your website. '
        'This section identifies — from live checks, not estimates — where those sources are missing, '
        'outdated, or inconsistent.</p>'
        + body + src_note + cta +
        '</div>'
    )


def render_content_deep_dive(result: AnalysisResult, pdf_path: Path, findings,
                             brand: str = "original") -> None:
    """Report 1 for the Content Analysis sandbox: the standard Deep Diagnostic
    with the Content Improvement Keys section appended. Reuses the shared HTML
    builder untouched and injects the section before </body>."""
    from playwright.sync_api import sync_playwright
    cfg = _BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"])
    html = _build_html(result, cfg)
    section = _content_keys_section(findings)
    html = html.replace("</body>", section + "</body>", 1) if "</body>" in html else html + section
    _cached_lbl = _fmt_cached(getattr(result, "data_collected_at", None) or result.generated_at)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path), format="Letter",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True, display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                '<div style="width:100%;font-family:Arial,sans-serif;'
                'font-size:9px;color:#7a9095;display:flex;justify-content:space-between;'
                'align-items:center;padding:0 48px 8px;box-sizing:border-box">'
                f'<span>{_cached_lbl}</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                "</div>"
            ),
        )
        browser.close()


def render_pdf(result: AnalysisResult, pdf_path: Path, brand: str = "original") -> None:
    """Render a structured AnalysisResult to a branded PDF using Playwright."""
    from playwright.sync_api import sync_playwright

    cfg = _BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"])
    html = _build_html(result, cfg)
    _cached_lbl = _fmt_cached(getattr(result, "data_collected_at", None) or result.generated_at)
    # Practice-report validation: hospital-only signals must never appear in the body.
    if result.entity_type == "practice":
        import sys as _sys
        _forbidden = ("Leapfrog", "CMS Overall Star", "HCAHPS")
        for _phrase in _forbidden:
            if _phrase in html:
                print(
                    f"[pdf] ASSERTION: practice report contains hospital-only signal "
                    f"'{_phrase}' — review _outcomes_safety_weaknesses and prompt filters",
                    file=_sys.stderr,
                )
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
                'font-size:9px;color:#7a9095;display:flex;justify-content:space-between;'
                'align-items:center;padding:0 48px 8px;box-sizing:border-box">'
                f'<span>{_cached_lbl}</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                "</div>"
            ),
        )
        browser.close()


def _e(text: str | None) -> str:
    return _html_lib.escape(str(text or ""))


def _fmt_cached(gen) -> str:
    """'Data MM/DD/YYYY' from a date or ISO string (the data-collection date),
    or '' if unavailable. Shown in the report footer so a stable, cached score
    reads as data-as-of a fixed date rather than looking freshly recomputed."""
    from datetime import datetime as _dt
    d = gen
    if isinstance(d, str):
        try:
            d = _dt.fromisoformat(d).date()
        except Exception:
            return ""
    if not d:
        return ""
    try:
        return "Data " + d.strftime("%m/%d/%Y")
    except Exception:
        return ""


_METHODOLOGY_URL = "careclimb.com/methodology"


def _methodology_box_html(pillars: list[str], n_label: str = "four pillars") -> str:
    """Compact methodology summary + link to the full public /methodology page —
    replaces the former multi-page in-report appendix (details now live online)."""
    T, S, BD, TXT, ALT = "#0F4146", "#177B6E", "#d0e4e8", "#3a5a60", "#f8fbfa"
    pill = " &middot; ".join(pillars)
    return (
        f'<div style="page-break-inside:avoid;background:{ALT};border:1px solid {BD};'
        f'border-radius:6px;padding:16px 20px;margin:22px 0;font-size:8.5pt;color:{TXT};line-height:1.55">'
        f'<div style="font-size:9.5pt;font-weight:700;color:{T};margin-bottom:8px">Methodology &mdash; Pulse AI Visibility</div>'
        f'<p style="margin:0 0 6px">The <strong>Pulse Score</strong> (0&ndash;100) measures how visibly and '
        f'favorably an organization surfaces when patients and referrers ask AI assistants (ChatGPT, Claude, '
        f'Gemini) where to get care &mdash; a market-perception measure, not a clinical-quality verdict. It is a '
        f'weighted blend of {n_label}: {pill}.</p>'
        f'<p style="margin:0 0 6px"><strong>National quartiles:</strong> 1st (&#8805;75) &middot; 2nd (68&ndash;74) '
        f'&middot; 3rd (58&ndash;67) &middot; 4th (&lt;58). &nbsp;<strong>Sources:</strong> CMS Care Compare, Google, '
        f'The Leapfrog Group, U.S. News, HRSA (Community Health).</p>'
        f'<p style="margin:0">The complete scoring rubric, data sources, and prompt battery are published at '
        f'<a href="https://{_METHODOLOGY_URL}" style="color:{S};font-weight:700;text-decoration:none">{_METHODOLOGY_URL}</a>.</p>'
        f'</div>'
    )


_MD_BOLD     = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)
_MD_ITALIC   = re.compile(r'\*(.+?)\*|_(.+?)_', re.DOTALL)
_MD_HEADER   = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_MD_CODE     = re.compile(r'`(.+?)`', re.DOTALL)
_MD_HR       = re.compile(r'^---+\s*$', re.MULTILINE)
_MD_ITEM_NUM = re.compile(r'^#\d+\s*', re.MULTILINE)  # strips LLM global counters like #1, #5


def _strip_md(text: str | None) -> str:
    """Remove markdown control characters from LLM-generated prose before HTML rendering."""
    if not text:
        return text or ""
    t = _MD_HEADER.sub("", text)
    t = _MD_ITEM_NUM.sub("", t)
    t = _MD_BOLD.sub(r'\1', t)
    t = _MD_ITALIC.sub(lambda m: m.group(1) or m.group(2), t)
    t = _MD_CODE.sub(r'\1', t)
    t = _MD_HR.sub("", t)
    return t


_VFLAG_STYLES: dict[str, tuple[str, str]] = {
    "verified":        ("#2a8a5a", "✓ Verified"),
    "partial":         ("#c87000", "◐ Partial"),
    "not_established": ("#b03030", "✗ Not established"),
}


def _vflag(status: str) -> str:
    """Render a ✓/◐/✗ verification flag badge for a scored signal."""
    color, label = _VFLAG_STYLES.get(status, ("#888888", "? Unknown"))
    return (
        f'<span style="font-size:6.5pt;font-weight:700;color:{color};'
        f'white-space:nowrap;margin-left:5px">{label}</span>'
    )


def _rank_text_color(rank: int) -> str:
    # Blue and green badges are light — use dark teal text for contrast
    return _TEAL if rank in (2, 3) else "#ffffff"


def _score_band(score: int | None) -> tuple[str, str]:
    """Return (label, css_class) for the score band indicator — thresholds match quartile cutoffs."""
    if score is None:
        return "", ""
    if score >= 75:
        return "Top Quartile", "score-band-strong"
    if score >= 68:
        return "Upper Middle", "score-band-good"
    if score >= 58:
        return "Lower Middle", "score-band-fair"
    return "Bottom Quartile", "score-band-weak"


def _trauma_teaching_pills(p: RankedProvider) -> str:
    """Trauma level and teaching status pills for the card top row."""
    parts = []
    tl = (p.trauma_level or "").strip().lower()
    if tl and tl not in ("not a trauma center", "not applicable", "null", ""):
        parts.append(f'<span class="trauma-pill">{_e(p.trauma_level)}</span>')
    ts = (p.teaching_status or "").strip()
    if ts in ("major", "minor"):
        label = "Major Teaching" if ts == "major" else "Teaching Hospital"
        parts.append(f'<span class="teaching-pill">{label}</span>')
    return "".join(parts)


def _locations_block(p: RankedProvider) -> str:
    """Consolidated locations list with per-location Google data."""
    if not p.consolidated_locations:
        return ""
    parts = []
    for loc in p.consolidated_locations:
        # Never show review counts here — we only have data for the anchor location,
        # not all siblings, so showing partial numbers would be misleading.
        addr_span = f'&ensp;<span class="loc-addr">{_e(loc.address)}</span>' if loc.address else ""
        parts.append(f'<li><span class="loc-name">{_e(loc.name)}</span>{addr_span}</li>')
    items = "".join(parts)
    return f'<div class="locations-block"><div class="locations-label">Includes locations:</div><ul class="locations-list">{items}</ul></div>'


def _rating_pill(p: RankedProvider) -> str:
    """Top-right rating pill for a card. Suppressed when the 'rating' is just a
    quartile code (Q1-Q4): that's already shown in the National Quartile detail
    in the score block below, so a top-right duplicate is redundant and reads
    like a fiscal quarter."""
    r = (p.overall_rating or "").strip()
    if not r or re.fullmatch(r"[Qq][1-4]", r):
        return ""
    return f'<span class="rating-pill">{_e(r)}</span>'


def _ai_says_block(p: RankedProvider) -> str:
    if not p.ai_says:
        return ""
    return f"""
    <div class="ai-says">
      <div class="ai-says-label">What AI Assistants Currently See
        <span style="font-size:6pt;font-weight:400;color:#7a9095;font-style:italic;margin-left:6px">
          &mdash; from training memory &amp; live retrieval &middot; Claude, ChatGPT, Gemini</span>
      </div>
      <div class="ai-says-text">{_e(_strip_md(p.ai_says))}</div>
    </div>"""


def _tier_row(label: str, value: int | None) -> str:
    width = value if isinstance(value, int) else 0
    val_txt = str(value) if isinstance(value, int) else "—"
    # Color the bar + number by threshold (same as the Network Pulse Score
    # Breakdown). A missing/unscored value ("—") is treated as red.
    color = _score_bar_color(value if isinstance(value, int) else None)
    return (
        f'<div class="tier-row"><span class="tier-name">{_e(label)}</span>'
        f'<span class="tier-track"><span class="tier-fill" style="width:{width}%;background:{color}"></span></span>'
        f'<span class="tier-val" style="color:{color}">{val_txt}</span></div>'
    )


def _aivs_block(p: RankedProvider, methodology_note: bool = True) -> str:
    """AI Visibility score + computed letter grade + weighting profile + the four tier bars.

    `methodology_note=False` drops the "Scored per Appendix A methodology" footnote
    for the simplified summary view, which has no appendix."""
    profile = p.weighting_profile or "procedural"
    labels = _tier_labels(profile)
    ts = p.tier_scores
    score = p.ai_visibility_score
    score_txt = str(score) if score is not None else "—"
    # Quartile is always computed from score — never from LLM-generated overall_rating.
    quartile, band_label = grade_from_score(score)
    q_color = _QUARTILE_COLORS.get(quartile, _TEAL)
    nat_q_html = ""
    if score is not None and quartile != "—":
        nat_q_html = (
            f'<div class="aivs-nat-q-lbl">National Quartile</div>'
            f'<div class="aivs-nat-q-val" style="color:{q_color}">'
            f'{_e(_quartile_label(quartile))} &middot; {_e(band_label)}</div>'
        )
    if profile.startswith("practice_"):
        profile_label = PRACTICE_PROFILE_DISPLAY.get(profile, "Procedural")
    elif profile == "relationship":
        profile_label = "Relationship"
    else:
        profile_label = "Procedural"
    rows = "".join([
        _tier_row(labels["clinical_outcomes_safety"], ts.clinical_outcomes_safety),
        _tier_row(labels["credentials_recognition"], ts.credentials_recognition),
        _tier_row(labels["patient_experience_reviews"], ts.patient_experience_reviews),
        _tier_row(labels["access_fit"], ts.access_fit),
    ])
    return f"""
    <div class="aivs">
      <div>
        <div class="aivs-label">{SCORE_LABEL}</div>
        <div class="aivs-sublabel">{SCORE_DESCRIPTOR}</div>
        <div class="aivs-score">{score_txt}<span class="out">/100</span></div>
        {nat_q_html}
        <div class="profile-chip">{profile_label}</div>
        {f'<div class="ceiling-note">⚠ Score capped at 74 ({_e(p.score_ceiling_reason)})</div>' if p.score_ceiling_applied else ""}
      </div>
      <div class="tier-bars">{rows}
        {'<div style="font-size:6pt;color:#aabcc0;margin-top:3px;font-style:italic">Scored per Appendix A methodology</div>' if methodology_note else ''}
      </div>
    </div>"""


def _google_stat(p: RankedProvider) -> str:
    """Front door (verified) + footprint + third-party aggregate — the wedge."""
    fd = p.google_footprint.front_door
    fp = p.google_footprint
    sa = fp.system_aggregate

    # ── Front door label: explain WHAT listing this represents ────────────
    if sa.available:
        fd_label = "Brand / Parent Listing"
        fd_context = (
            f'<span style="font-size:6.5pt;color:#7a9095;font-style:italic">'
            f'(the organization\'s primary Google Business Profile — '
            f'distinct from individual location ratings below)</span>'
        )
    else:
        fd_label = "Google Front Door"
        fd_context = ""

    fd_verified = fd.verified and fd.rating is not None
    if fd_verified:
        front_line = (
            f'<span style="font-size:6.5pt;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.08em;color:#5a7880">{fd_label}:</span> '
            f'<strong>{fd.rating:.1f}&#9733;</strong>{_vflag("verified")} {fd_context}<br>'
        )
    else:
        front_line = ""  # omit entirely — showing "Not verified" is worse than silence

    # ── System-wide aggregate ─────────────────────────────────────────────
    system_line = ""
    if sa.available:
        conf = "registry-enumerated" if sa.confidence == "registry" else "sampled"
        loc = f"{sa.location_count}{'+' if sa.capped else ''}"
        pct_diff = ""
        if fd.verified and fd.rating and sa.rating:
            diff = round(sa.rating - fd.rating, 1)
            if abs(diff) >= 0.3:
                direction = "higher" if diff > 0 else "lower"
                pct_diff = f' <span class="google-gap">({abs(diff):.1f}★ {direction} than brand listing)</span>'
        system_line = (
            f'<div style="margin-top:4px;font-size:7pt;color:#3a5a60">'
            f'<strong>System-wide (all locations):</strong> '
            f'<strong>{sa.rating:.1f}&#9733; · {sa.total_reviews:,} total reviews '
            f'across {loc} location{"s" if sa.location_count != 1 else ""}</strong>{pct_diff} '
            f'<span style="color:#7a9095;font-style:italic">(review-count-weighted, {conf})</span>'
            f'</div>'
        )

    # ── Footprint breadth ─────────────────────────────────────────────────
    footprint = _e(fp.rating_range or fp.listings_estimate or fp.consistency) or "single listing"
    consistency = f" · {_e(fp.consistency)}" if fp.consistency and (fp.rating_range or fp.listings_estimate) else ""

    # ── Third-party aggregate ─────────────────────────────────────────────
    tpa = p.third_party_aggregate
    if tpa.rating is not None and tpa.note:
        agg = f"{tpa.rating:.1f} avg — {_e(tpa.note)}"
    elif tpa.rating is not None:
        agg = f"{tpa.rating:.1f} avg"
    else:
        agg = _e(tpa.note) or "limited data"
    gap = f' <span class="google-gap">{_e(fp.gap_note)}</span>' if fp.gap_note else ""

    return f"""
    <div class="google-stat-section">
      <div class="google-stat-label">Public &amp; Social Ratings</div>
      <div class="google-stat">
        {front_line}{system_line}
        <span style="font-size:6.5pt;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#5a7880">Footprint:</span> {footprint}{consistency}<br>
        <span style="font-size:6.5pt;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#5a7880">Third-Party</span> <span style="font-size:6.5pt;color:#7a9095">(Healthgrades, Vitals, WebMD, Yelp):</span> <strong>{agg}</strong>{gap}
      </div>
    </div>"""


def _patient_voice_block(p: RankedProvider) -> str:
    if not p.patient_voice_summary:
        return ""
    return f"""
    <div class="patient-voice">
      <div class="pv-label">Patient Voice</div>
      <div class="pv-text">{_e(_strip_md(p.patient_voice_summary))}</div>
    </div>"""


def _outcomes_data_status(p: RankedProvider) -> tuple[bool, bool]:
    """Returns (has_leapfrog_grade, has_cms_rating)."""
    grade = (p.leapfrog_grade or "").strip()
    first = grade[0].upper() if grade else ""
    has_leapfrog = bool(first and first in "ABCDF")
    has_cms = bool(p.cms_star_rating and 1 <= p.cms_star_rating <= 5)
    return has_leapfrog, has_cms


def _outcomes_safety_weaknesses(p: RankedProvider) -> list[str]:
    """When both Leapfrog and CMS data are absent, return items for Areas for Improvement.

    Returns empty list for practice reports — hospital-only signals are not applicable.
    """
    if getattr(p, "report_type", "hospital") == "practice":
        return []
    has_leapfrog, has_cms = _outcomes_data_status(p)
    if has_leapfrog or has_cms:
        return []
    return [
        "Leapfrog Hospital Safety Grade not published",
        "CMS Overall Star Rating not published",
    ]


def _outcomes_safety_block(p: RankedProvider) -> str:
    """Leapfrog + CMS quality row. Returns empty string when both values are absent."""
    has_leapfrog, has_cms = _outcomes_data_status(p)
    if not has_leapfrog and not has_cms:
        return ""

    grade = (p.leapfrog_grade or "").strip()
    first = grade[0].upper() if grade else ""
    if first and first in "ABCDF":
        css_cls = f"qs-badge qs-leapfrog-{first}"
        leapfrog_cell = f'<span class="{css_cls}">{first}</span>{_vflag("verified")}'
    elif grade.lower() in ("not rated", "not_rated"):
        leapfrog_cell = f'<span class="os-absent">Not rated in current survey cycle</span>{_vflag("partial")}'
    else:
        leapfrog_cell = f'<span class="os-absent">Not currently participating in Leapfrog survey</span>{_vflag("not_established")}'

    if has_cms:
        _star_css = {5: "qs-cms-5", 4: "qs-cms-4", 3: "qs-cms-3", 2: "qs-cms-2", 1: "qs-cms-1"}
        stars = "★" * p.cms_star_rating + "☆" * (5 - p.cms_star_rating)
        cms_cell = f'<span class="qs-badge {_star_css[p.cms_star_rating]}">{stars} ({p.cms_star_rating} of 5)</span>{_vflag("verified")}'
    else:
        cms_cell = f'<span class="os-absent">No CMS Overall Star Rating published</span>{_vflag("not_established")}'

    return f"""
    <div class="outcomes-safety">
      <div class="os-label">Outcomes &amp; Safety</div>
      <div class="os-row">
        <span class="os-key">Leapfrog Hospital Safety Grade</span>
        {leapfrog_cell}
      </div>
      <div class="os-row">
        <span class="os-key">CMS Overall Star Rating</span>
        {cms_cell}
      </div>
      <div class="os-verify">Verify current grades at <em>leapfroggroup.org</em> and <em>medicare.gov/care-compare</em> — updated periodically.</div>
    </div>"""


def _mips_flag(text: str) -> str:
    """Infer ✓/◐/✗ flag for a MIPS/QPP quality-highlights line from its text content."""
    t = text.lower()
    if any(p in t for p in ("not found", "not available", "no published", "not reported",
                             "not applicable", "no mips", "no qpp", "unable to confirm")):
        return _vflag("not_established")
    if any(p in t for p in ("score:", "performance:", "participated", "final score",
                             "reporting", "submitted")):
        return _vflag("verified")
    return _vflag("partial")


def _quality_signals_block(p: RankedProvider) -> str:
    # CMS stars and Leapfrog grade are surfaced in _outcomes_safety_block above each card section
    usnews_html = ""
    for u in p.us_news_rankings:
        if u.recognition_type == "nationally_ranked" and u.rank:
            usnews_html += f'<span class="qs-badge qs-usnews-ranked">#{u.rank} {_e(u.category)}</span>{_vflag("verified")}'
        elif u.recognition_type == "nationally_ranked":
            usnews_html += f'<span class="qs-badge qs-usnews-ranked">Natl. Ranked · {_e(u.category)}</span>{_vflag("verified")}'
        else:
            usnews_html += f'<span class="qs-badge qs-usnews-hp">High-Perf. · {_e(u.category)}</span>{_vflag("verified")}'

    accred_html = "".join(
        f'<span class="qs-badge qs-accred">{_e(a)}</span>{_vflag("verified")}'
        for a in p.accreditations
    )

    has_badges = bool(usnews_html or accred_html)
    has_quality = bool(p.cms_quality_highlights)
    if not has_badges and not has_quality:
        return ""

    badges_html = (
        f'<div class="qs-badges">{usnews_html}{accred_html}</div>'
        if has_badges else ""
    )
    quality_html = (
        f'<div class="qs-quality">{_e(p.cms_quality_highlights)}'
        f'{_mips_flag(p.cms_quality_highlights)}</div>'
        if has_quality else ""
    )
    return f"""
    <div class="quality-signals">
      <div class="qs-label">Quality &amp; Accreditation</div>
      {badges_html}
      {quality_html}
    </div>"""


def _provider_card(p: RankedProvider, display_rank: int) -> str:
    bg = _RANK_COLORS.get(display_rank, _RANK_DEFAULT)
    text_color = _rank_text_color(display_rank)
    strengths_html = "".join(f"<li>{_e(_strip_md(s))}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(_strip_md(w))}</li>"
        for w in list(p.notable_weaknesses) + _outcomes_safety_weaknesses(p)
    )
    disq_html = (
        f'<div class="disqualifier">⚠ Disqualifiers: {_e("; ".join(p.disqualifiers))}</div>'
        if p.disqualifiers else ""
    )
    _pc = (p.physician_count or "").strip()
    physician_pill = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60 else ""
    )
    return f"""
    <div class="card">
      <div class="card-rank" style="background:{bg}; color:{text_color}">
        <span class="rank-num">{display_rank}</span>
      </div>
      <div class="card-body">
        <div class="card-top">
          <h3 class="provider-name">{_e(p.name)}</h3>
          {physician_pill}
          {_trauma_teaching_pills(p)}
          {_rating_pill(p)}
        </div>
        {f'<div class="provider-url"><a href="{_e(p.website_url)}">{_e(p.website_url)}</a></div>' if p.website_url else ""}
        {_aivs_block(p)}
        {_ai_says_block(p)}
        {_google_stat(p)}
        {_patient_voice_block(p)}
        {_outcomes_safety_block(p)}
        {_quality_signals_block(p)}
        {disq_html}
        {_locations_block(p)}
        <div class="traits">
          <div class="trait-col">
            <div class="trait-label strengths-label">Strengths</div>
            <ul>{strengths_html}</ul>
          </div>
          <div class="trait-col">
            <div class="trait-label weaknesses-label">Areas for Improvement</div>
            <ul>{weaknesses_html}</ul>
          </div>
        </div>
        <div class="best-for"><strong>Best for:</strong> {_e(p.best_suited_for)}</div>
      </div>
    </div>"""


def _individual_entity_card(p: RankedProvider) -> str:
    """Full-width card for individual entity reports — no rank badge."""
    strengths_html = "".join(f"<li>{_e(_strip_md(s))}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(_strip_md(w))}</li>"
        for w in list(p.notable_weaknesses) + _outcomes_safety_weaknesses(p)
    )
    disq_html = (
        f'<div class="disqualifier">⚠ Disqualifiers: {_e("; ".join(p.disqualifiers))}</div>'
        if p.disqualifiers else ""
    )
    # Only show physician pill for brief counts, not descriptive sentences
    _pc = (p.physician_count or "").strip()
    physician_pill = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60
        else ""
    )
    return f"""
    <div class="card" style="border:2px solid {_TEAL}">
      <div class="card-body" style="padding:16px 20px">
        <div class="card-top">
          <h3 class="provider-name" style="font-size:13pt">{_e(p.name)}</h3>
          {physician_pill}
          {_trauma_teaching_pills(p)}
          {_rating_pill(p)}
        </div>
        {f'<div class="provider-url"><a href="{_e(p.website_url)}">{_e(p.website_url)}</a></div>' if p.website_url else ""}
        {_aivs_block(p)}
        {_ai_says_block(p)}
        {_google_stat(p)}
        {_patient_voice_block(p)}
        {_outcomes_safety_block(p)}
        {_quality_signals_block(p)}
        {disq_html}
        {_locations_block(p)}
        <div class="traits">
          <div class="trait-col">
            <div class="trait-label strengths-label">Strengths</div>
            <ul>{strengths_html}</ul>
          </div>
          <div class="trait-col">
            <div class="trait-label weaknesses-label">Areas for Improvement</div>
            <ul>{weaknesses_html}</ul>
          </div>
        </div>
        <div class="best-for"><strong>Best for:</strong> {_e(p.best_suited_for)}</div>
      </div>
    </div>"""


def _individual_teaser_card(p: RankedProvider) -> str:
    """Blurred individual entity card for teaser version."""
    _pc = (p.physician_count or "").strip()
    physician_html = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60 else ""
    )
    strengths_html = "".join(f"<li>{_e(_strip_md(s))}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(_strip_md(w))}</li>"
        for w in list(p.notable_weaknesses) + _outcomes_safety_weaknesses(p)
    )
    return f"""
    <div class="card" style="border:2px solid {_TEAL}">
      <div class="card-body" style="padding:16px 20px">
        <div class="card-top">
          <h3 class="provider-name" style="font-size:13pt">{_e(p.name)}</h3>
          {physician_html}
          {_trauma_teaching_pills(p)}
          {_rating_pill(p)}
        </div>
        {_aivs_block(p)}
        {_ai_says_block(p)}
        <div class="teaser-blur-wrapper">
          <div class="teaser-blur-content">
            {_google_stat(p)}
            {_patient_voice_block(p)}
            {_outcomes_safety_block(p)}
            {_quality_signals_block(p)}
            <div class="traits">
              <div class="trait-col">
                <div class="trait-label strengths-label">Strengths</div>
                <ul>{strengths_html}</ul>
              </div>
              <div class="trait-col">
                <div class="trait-label weaknesses-label">Areas for Improvement</div>
                <ul>{weaknesses_html}</ul>
              </div>
            </div>
          </div>
          <div class="teaser-blur-overlay">
            <div class="blur-lock">&#128274;</div>
            <div class="blur-cta-heading">Full analysis available upon request</div>
            <div class="blur-cta-sub">{BLUR_CTA_INDIVIDUAL}</div>
            <div class="blur-cta-actions">
              <span class="blur-phone">{_TEASER_PHONE}</span>
              &nbsp;&nbsp;&middot;&nbsp;&nbsp;
              <a href="{_TEASER_DEMO_URL}" class="blur-demo-link">Book a Demo &rarr;</a>
            </div>
          </div>
        </div>
      </div>
    </div>"""


def _individual_rankings_section(providers: list[RankedProvider]) -> str:
    if not providers:
        return ""
    return "\n".join(_individual_entity_card(p) for p in providers)


def _individual_teaser_section(providers: list[RankedProvider]) -> str:
    if not providers:
        return ""
    return "\n".join(_individual_teaser_card(p) for p in providers) + _teaser_roadmap_section()


def _rankings_section(providers: list[RankedProvider], title: str, subtitle: str) -> str:
    if not providers:
        return ""
    cards = "\n".join(_provider_card(p, i + 1) for i, p in enumerate(providers))
    return f"""
  <div class="rankings">
    <div class="section-title">{_e(title)}</div>
    <div class="section-subtitle">{_e(subtitle)}</div>
    {cards}
  </div>"""


_TEASER_DEMO_URL = "https://web.rldatix.com/pxg-report?hs_preview=sSVWpYTV-439532565740"
_TEASER_PHONE    = "866.338.8270"


def _teaser_card(p: RankedProvider, display_rank: int) -> str:
    bg = _RANK_COLORS.get(display_rank, _RANK_DEFAULT)
    text_color = _rank_text_color(display_rank)
    _pc = (p.physician_count or "").strip()
    physician_html = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60 else ""
    )
    strengths_html = "".join(f"<li>{_e(_strip_md(s))}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(_strip_md(w))}</li>"
        for w in list(p.notable_weaknesses) + _outcomes_safety_weaknesses(p)
    )
    return f"""
    <div class="card">
      <div class="card-rank" style="background:{bg}; color:{text_color}">
        <span class="rank-num">{display_rank}</span>
      </div>
      <div class="card-body">
        <div class="card-top">
          <h3 class="provider-name">{_e(p.name)}</h3>
          {physician_html}
          {_trauma_teaching_pills(p)}
          {_rating_pill(p)}
        </div>
        {_aivs_block(p)}
        {_ai_says_block(p)}
        <div class="teaser-blur-wrapper">
          <div class="teaser-blur-content">
            {_google_stat(p)}
            {_patient_voice_block(p)}
            {_outcomes_safety_block(p)}
            {_quality_signals_block(p)}
            <div class="traits">
              <div class="trait-col">
                <div class="trait-label strengths-label">Strengths</div>
                <ul>{strengths_html}</ul>
              </div>
              <div class="trait-col">
                <div class="trait-label weaknesses-label">Areas for Improvement</div>
                <ul>{weaknesses_html}</ul>
              </div>
            </div>
            <div class="best-for"><strong>Best for:</strong> {_e(p.best_suited_for)}</div>
            <div class="summary">{_e(p.recommendation_summary)}</div>
          </div>
          <div class="teaser-blur-overlay">
            <div class="blur-lock">&#128274;</div>
            <div class="blur-cta-heading">Full analysis available upon request</div>
            <div class="blur-cta-sub">{BLUR_CTA_COMPARISON}</div>
            <div class="blur-cta-actions">
              <span class="blur-phone">{_TEASER_PHONE}</span>
              &nbsp;&nbsp;&middot;&nbsp;&nbsp;
              <a href="{_TEASER_DEMO_URL}" class="blur-demo-link">Book a Demo &rarr;</a>
            </div>
          </div>
        </div>
      </div>
    </div>"""


def _teaser_roadmap_section() -> str:
    """Locked improvement roadmap appended after the provider cards."""
    tiers = [
        ("Outcomes &amp; Safety",       "Clinical quality metrics, safety grades, and performance indicators that AI assistants weight most heavily for hospital and surgical care."),
        ("Credentials &amp; Recognition","Rankings, board certifications, accreditations, and academic affiliations that establish trust signals across AI platforms."),
        ("Experience &amp; Reviews",     "Google rating strategy, review volume, recency, and footprint consistency — the reputation wedge most under management control."),
        ("Access &amp; Fit",             "Network breadth, new-patient availability, location footprint, and telehealth presence that determine how patients can actually reach you."),
    ]
    items = ""
    for tier_name, tier_desc in tiers:
        items += f"""
        <div class="roadmap-item">
          <div class="roadmap-tier-header">
            <span class="roadmap-tier-name">{tier_name}</span>
            <span class="roadmap-locked-badge">&#128274; LOCKED</span>
          </div>
          <div class="roadmap-tier-desc">{tier_desc}</div>
          <div class="roadmap-blur-content">
            Priority action items and competitive benchmarks for this tier are included
            in the full report. Contact RLDatix to receive your personalized improvement
            roadmap with specific, ranked recommendations and projected score impact.
          </div>
        </div>"""

    return f"""
  <div class="roadmap-section">
    <div class="roadmap-header">
      <div class="roadmap-title">&#128274;&nbsp; {ROADMAP_TITLE}</div>
      <div class="roadmap-subtitle">Your personalized action plan by tier &mdash; unlock the full report to see exactly where to focus and what moves the needle.</div>
    </div>
    <div class="roadmap-items">{items}</div>
    <div class="roadmap-cta">
      <div class="roadmap-cta-text">
        Ready to improve how your organization surfaces to AI assistants?
        The full report includes prioritized recommendations for each tier,
        competitive gap analysis, and a clear path to ranking higher when
        patients ask AI assistants for a recommendation.
      </div>
      <div class="roadmap-cta-actions">
        <strong>{_TEASER_PHONE}</strong>
        &nbsp;&nbsp;&middot;&nbsp;&nbsp;
        <a href="{_TEASER_DEMO_URL}">Book a Demo at rldatix.com &rarr;</a>
      </div>
    </div>
  </div>"""


def _teaser_rankings_section(providers: list[RankedProvider], title: str, subtitle: str) -> str:
    if not providers:
        return ""
    cards = "\n".join(_teaser_card(p, i + 1) for i, p in enumerate(providers))
    return f"""
  <div class="rankings">
    <div class="section-title">{_e(title)}</div>
    <div class="section-subtitle">{_e(subtitle)}</div>
    {cards}
  </div>
  {_teaser_roadmap_section()}"""


def _simplified_card(p: RankedProvider, display_rank: int, obscure: bool = True) -> str:
    """Compact Patient Pulse card showing ONLY the AI Visibility block (four tier
    bars + score) and 'What AI Assistants Currently See'.

    obscure=True (Enticement): the target/prospect renders in full; every other
    entity has its whole card body — name included — obscured with the blur.
    obscure=False (Market Summary): every entity renders clearly, no target badge.
    """
    bg = _RANK_COLORS.get(display_rank, _RANK_DEFAULT)
    text_color = _rank_text_color(display_rank)
    _pc = (p.physician_count or "").strip()
    physician_html = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60 else ""
    )
    inner = f"""
        <div class="card-top">
          <h3 class="provider-name">{_e(p.name)}</h3>
          {physician_html}
          {_trauma_teaching_pills(p)}
          {_rating_pill(p)}
        </div>
        {_aivs_block(p, methodology_note=False)}
        {_ai_says_block(p)}"""

    if not obscure:
        # Market Summary: every provider shown clearly, no target badge.
        body = inner
    elif p.is_target:
        body = f"""
        <div style="display:inline-block;background:{_TEAL};color:#fff;font-size:7pt;
                    font-weight:700;letter-spacing:0.06em;text-transform:uppercase;
                    padding:3px 10px;border-radius:12px;margin-bottom:8px">&#9733; Your Organization</div>
        {inner}"""
    else:
        body = f"""
        <div class="teaser-blur-wrapper">
          <div class="simplified-blur-content">{inner}</div>
          <div class="teaser-blur-overlay">
            <div class="blur-lock">&#128274;</div>
            <div class="blur-cta-heading">Competitor &mdash; hidden</div>
          </div>
        </div>"""

    return f"""
    <div class="card">
      <div class="card-rank" style="background:{bg}; color:{text_color}">
        <span class="rank-num">{display_rank}</span>
      </div>
      <div class="card-body">
        {body}
      </div>
    </div>"""


def _simplified_patient_section(providers: list[RankedProvider], title: str, subtitle: str,
                                obscure: bool = True) -> str:
    """Simplified Patient Pulse: entities ranked most→least AI-visible, each a compact
    two-block card. Enticement (obscure=True) shows the target in full and obscures
    competitors, with a closing CTA; Market Summary (obscure=False) shows all clearly."""
    if not providers:
        return ""
    cards = "\n".join(_simplified_card(p, i + 1, obscure=obscure) for i, p in enumerate(providers))
    cta = f"""
    <div style="margin:16px 20px 8px;padding:14px 18px;border:1.5px dashed {_SEAFOAM};
                border-radius:8px;background:{_PALE_GREEN};text-align:center">
      <div style="font-size:9pt;color:#3a5a60;margin-bottom:6px">
        Competitor identities and the full analysis are available on request.</div>
      <div style="font-size:9.5pt;font-weight:700;color:{_TEAL}">
        {_TEASER_PHONE} &nbsp;&middot;&nbsp;
        <a href="{_TEASER_DEMO_URL}" style="color:#0a5c70;text-decoration:underline">Book a Demo &rarr;</a></div>
    </div>""" if obscure else ""
    return f"""
  <div class="rankings">
    <div class="section-title">{_e(title)}</div>
    <div class="section-subtitle">{_e(subtitle)}</div>
    {cards}
    {cta}
  </div>"""


def _appendix_html() -> str:
    """Appendix — compact methodology summary + link to the full /methodology page.
    (The former multi-page prompt-battery/rubric detail now lives online.)"""
    return _methodology_box_html(
        ["Outcomes &amp; Safety", "Credentials &amp; Recognition",
         "Experience &amp; Reviews", "Access &amp; Fit"])


def _practice_appendix_html() -> str:
    """Appendix — compact methodology summary + link (Practice Edition)."""
    return _methodology_box_html(
        ["Practitioner Credentials &amp; Clinical Quality", "Reviews &amp; Reputation",
         "Identity &amp; Machine-Readability", "Access &amp; Fit"])


def _build_html(result: AnalysisResult, brand_cfg: dict | None = None) -> str:
    brand_cfg = brand_cfg or _BRAND_CONFIGS["original"]
    location        = _e(result.location)
    specialty_label = _e(result.specialty or "Hospital Market")
    date_str        = result.generated_at.strftime("%B %d, %Y")
    _has_custom_logo = brand_cfg.get("logo_html") or brand_cfg.get("logo_path")
    logo_uri         = None if _has_custom_logo else _logo_data_uri()

    if result.simplified:
        # Simplified Patient Pulse: compact two-block cards, ranked most→least
        # AI-visible; target rendered in full, every competitor obscured.
        all_ranked = sorted(result.rankings, key=lambda p: (-(p.ai_visibility_score or 0), p.rank))
        section_title = f"{result.specialty} Providers" if result.specialty else "Hospitals & Health Systems"
        rankings_html = _simplified_patient_section(
            all_ranked, section_title,
            "Ranked by AI visibility — most to least visible",
            obscure=result.obscure_competitors,
        )
    elif result.individual_report and result.teaser_report:
        all_ranked = sorted(result.rankings, key=lambda p: p.rank)
        rankings_html = _individual_teaser_section(all_ranked)
    elif result.individual_report:
        all_ranked = sorted(result.rankings, key=lambda p: p.rank)
        rankings_html = _individual_rankings_section(all_ranked)
    elif result.teaser_report:
        # Teaser: summary-only cards, flat rank order
        all_ranked = sorted(result.rankings, key=lambda p: p.rank)
        section_title = f"{result.specialty} Providers" if result.specialty else "Hospitals & Health Systems"
        rankings_html = _teaser_rankings_section(
            all_ranked, section_title,
            RANKED_TEASER_SUBTITLE
        )
    elif result.patient_perspective:
        # Patient perspective: single flat list ordered purely by rank
        all_ranked = sorted(result.rankings, key=lambda p: p.rank)
        section_title = f"{result.specialty} Providers" if result.specialty else "Hospitals & Health Systems"
        rankings_html = _rankings_section(
            all_ranked, section_title,
            RANKED_PATIENT_SUBTITLE
        )
    elif result.specialty:
        # Specialty analysis: split by affiliation type
        independent = [p for p in result.rankings if p.affiliation_type == AffiliationType.independent]
        affiliated  = [p for p in result.rankings if p.affiliation_type == AffiliationType.hospital_affiliated]
        unclassified = [p for p in result.rankings if p.affiliation_type == AffiliationType.unknown]
        rankings_html = (
            _rankings_section(independent, "Independent Practices", "Privately owned and operated by physicians")
            + _rankings_section(affiliated, "Hospital & Academic-Affiliated Groups", "Employed by or owned by a hospital, health system, or academic medical center")
            + _rankings_section(unclassified, "Additional Providers", "Affiliation not classified")
        )
    else:
        # Hospital analysis: split by size category
        large     = [p for p in result.rankings if p.size_category == SizeCategory.large]
        community = [p for p in result.rankings if p.size_category == SizeCategory.community]
        unclassified = [p for p in result.rankings if p.size_category == SizeCategory.unknown]
        rankings_html = (
            _rankings_section(large, "Large & Major Hospitals", "Academic medical centers, major teaching hospitals, and large regional referral centers")
            + _rankings_section(community, "Community & Smaller Hospitals", "Community hospitals, critical access hospitals, and specialty facilities")
            + _rankings_section(unclassified, "Additional Hospitals", "Size not classified")
        )

    _MARKET_ADVICE_CTA = MARKET_ADVICE_CTA

    def _advice_html() -> str:
        if not result.individual_report:
            return f'<p style="font-size:9pt;line-height:1.6;color:#444">{_MARKET_ADVICE_CTA}</p>'
        if result.improvement_sections:
            parts = []
            for sec in result.improvement_sections:
                items_li = "\n".join(f"<li>{_e(_strip_md(item))}</li>" for item in sec.items)
                parts.append(
                    f'<div class="advice-group">'
                    f'<div class="advice-group-title">{_e(_strip_md(sec.title))}</div>'
                    f'<div class="advice-group-desc">{_e(_strip_md(sec.description))}</div>'
                    f'<ol>{items_li}</ol>'
                    f'</div>'
                )
            return "\n".join(parts)
        flat = "\n".join(f"<li>{_e(_strip_md(a))}</li>" for a in result.practical_advice)
        return f"<ol>{flat}</ol>"
    if brand_cfg.get("logo_html"):
        logo_tag = brand_cfg["logo_html"]
    elif brand_cfg.get("logo_path") and brand_cfg["logo_path"].exists():
        _lp   = brand_cfg["logo_path"]
        _ldat = base64.b64encode(_lp.read_bytes()).decode()
        _luri = f"data:image/svg+xml;base64,{_ldat}"
        logo_tag = (
            f'<img src="{_luri}" alt="Logo"'
            f' style="height:52px;width:auto;display:block;margin-bottom:28px">'
        )
    else:
        logo_tag = f'<img class="cover-logo" src="{logo_uri}" alt="RLDatix">' if logo_uri else ""

    # Cover eyebrow and optional report-type sub-label
    cover_report_sub = ""
    if result.individual_report and result.teaser_report:
        cover_eyebrow = (
            f'{COVER_INDIVIDUAL_TEASER} '
            f'<a href="{_TEASER_DEMO_URL}" style="color:{_SEAFOAM};text-decoration:underline;">Here</a>'
        )
    elif result.individual_report:
        cover_eyebrow  = COVER_INDIVIDUAL
        cover_report_sub = f'<div class="cover-report-sub">{COVER_REPORT_SUB}</div>'
    elif result.teaser_report:
        cover_eyebrow = (
            f'{COVER_PATIENT_TEASER} '
            f'<a href="{_TEASER_DEMO_URL}" style="color:{_SEAFOAM};text-decoration:underline;">Here</a>'
        )
    elif result.simplified:
        cover_eyebrow = COVER_PATIENT
        _pp_fmt = "Enticement" if result.obscure_competitors else "Market Summary"
        cover_report_sub = f'<div class="cover-report-sub">{_pp_fmt}</div>'
    elif result.patient_perspective:
        cover_eyebrow = COVER_PATIENT
        cover_report_sub = '<div class="cover-report-sub">Full Report</div>'
    else:
        cover_eyebrow = COVER_MARKET

    # Cover location/specialty/sub differ for individual reports
    if result.individual_report:
        _raw_entity = result.report_title or result.entity_name or result.location
        # D8: For practice reports whose entity_name embeds a street address
        # (e.g., "Desert Orthopaedic Center 2800 E Desert Inn Rd, Las Vegas, NV 89121"),
        # split at the first street-number to show only the practice name as the cover title.
        _addr_match = re.search(r'\s+\d+\s', _raw_entity or "")
        if result.entity_type == "practice" and _addr_match:
            _display_name = _raw_entity[:_addr_match.start()].strip()
            _addr_part    = _raw_entity[_addr_match.start():].strip()
            cover_loc  = _e(_display_name)
            cover_sub  = (
                f'<div class="cover-zip-scope">{_e(_addr_part)}</div>'
                f'<div class="cover-zip-scope">{location}</div>'
            )
        else:
            cover_loc  = _e(_raw_entity)
            cover_sub  = f'<div class="cover-zip-scope">{location}</div>'
        cover_spec = _e(result.specialty or (
            "Specialty Practice" if result.entity_type == "practice" else "Hospital / Health System"
        ))
    else:
        cover_loc  = location
        cover_spec = specialty_label
        cover_sub  = (
            f'<div class="cover-zip-scope">ZIP {_e(result.zip_code)} &middot; {result.radius_miles}-mile radius</div>'
            if result.zip_code else ""
        )

    # Section title overrides for individual reports
    overview_title       = "Organization Overview" if result.individual_report else "Market Overview"
    recommendation_title = SECTION_ASSESSMENT      if result.individual_report else "Top Recommendation"
    # Individual reports: assessment+roadmap are one section; advice_title is empty to avoid
    # a duplicate SECTION_ASSESSMENT header after recommendation_title already rendered it.
    advice_title         = (
        ""
        if result.individual_report
        else "Improve Your AI Visibility"
    )

    def _paras(text: str) -> str:
        return "".join(
            f"<p>{_e(para.strip())}</p>"
            for para in _strip_md(text or "").split("\n")
            if para.strip()
        )

    def _first_sentences(text: str, n: int) -> str:
        parts = re.split(r"(?<=[.!?])\s+", _strip_md(text or "").strip())
        return " ".join(p for p in parts[:n] if p).strip()

    overview_html = ""
    if result.market_overview:
        # Simplified summary view: keep the market overview to 2–3 sentences.
        _ov = _first_sentences(result.market_overview, 3) if result.simplified else result.market_overview
        overview_html = (
            f'<div class="overview"><div class="section-title">{overview_title}</div>'
            + _paras(_ov) + "</div>"
        )
    # The Pulse verdict is omitted from the simplified summary view.
    verdict_html = ""
    if result.ai_visibility_verdict and not result.simplified:
        verdict_html = (
            f'<div class="verdict"><div class="section-title" style="margin-bottom:8px;">{SECTION_VERDICT}</div>'
            + _paras(result.ai_visibility_verdict) + "</div>"
        )

    # The simplified summary view omits the methodology appendix.
    if result.simplified:
        appendix_html = ""
    else:
        appendix_html = _practice_appendix_html() if result.entity_type == "practice" else _appendix_html()

    _html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    @page {{ size: Letter; }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: 'Inter', Helvetica, Arial, sans-serif;
      font-size: 10pt;
      line-height: 1.55;
      color: {_TEAL};
      background: #fff;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}

    /* ── Cover ─────────────────────────────────────── */
    .cover {{
      background: {_TEAL};
      color: #fff;
      padding: 44px 56px 36px;
    }}
    .cover-logo {{
      height: 30px;
      margin-bottom: 34px;
      display: block;
    }}
    .cover-logo-wordmark {{
      font-family: 'Barlow Condensed', Impact, sans-serif;
      font-size: 22pt;
      font-weight: 700;
      color: #fff;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      line-height: 1.0;
      margin-bottom: 34px;
      display: block;
    }}
    .cover-logo-wordmark .wm-sub {{
      font-size: 11pt;
      font-weight: 300;
      letter-spacing: 0.2em;
      opacity: 0.82;
      display: block;
      margin-top: 2px;
    }}
    .cover-eyebrow {{
      font-size: 7.5pt;
      font-weight: 500;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: {_SEAFOAM};
      margin-bottom: 4px;
    }}
    .cover-report-sub {{
      font-size: 6.5pt;
      font-weight: 400;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: rgba(128,248,228,0.6);
      margin-bottom: 10px;
    }}
    .cover-location {{
      font-family: 'Barlow Condensed', Impact, sans-serif;
      font-size: 36pt;
      font-weight: 700;
      line-height: 1.0;
      color: #fff;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }}
    .cover-specialty {{
      font-size: 13pt;
      font-weight: 300;
      color: {_PALE_GREEN};
      margin-bottom: 12px;
    }}
    .cover-zip-scope {{
      font-size: 10pt;
      font-weight: 400;
      color: {_SEAFOAM};
      letter-spacing: 0.04em;
      margin-bottom: 26px;
    }}
    .cover-meta {{
      font-size: 7.5pt;
      color: {_SEAFOAM};
      border-top: 1px solid rgba(128,248,228,0.25);
      padding-top: 12px;
      display: flex;
      justify-content: space-between;
    }}

    /* ── Accent bar ─────────────────────────────────── */
    .accent-bar {{
      height: 4px;
      background: linear-gradient(to right, {_SEAFOAM}, {_BLUE}, {_GREEN});
    }}

    /* ── Content area ───────────────────────────────── */
    .content {{
      padding: 30px 56px 0;
    }}

    .section-title {{
      font-size: 7.5pt;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #7a9095;
      margin-bottom: 12px;
      padding-bottom: 5px;
      border-bottom: 2px solid {_PALE_GREEN};
    }}

    /* ── Provider cards ─────────────────────────────── */
    .rankings {{ margin-bottom: 28px; }}

    .section-subtitle {{
      font-size: 7.5pt;
      color: #5a7880;
      font-style: italic;
      margin-top: -8px;
      margin-bottom: 10px;
    }}

    .card {{
      display: flex;
      border: 1px solid #d0e4e8;
      border-radius: 6px;
      margin-bottom: 11px;
      overflow: hidden;
      break-inside: avoid;
    }}

    .card-rank {{
      width: 52px;
      min-width: 52px;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding-top: 14px;
    }}
    .rank-num {{
      font-family: 'Barlow Condensed', Impact, sans-serif;
      font-size: 26pt;
      font-weight: 700;
      line-height: 1;
    }}

    .card-body {{
      padding: 12px 16px 12px 14px;
      flex: 1;
      min-width: 0;
    }}

    .card-top {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }}
    .provider-name {{
      font-size: 11pt;
      font-weight: 700;
      color: {_TEAL};
      flex: 1;
    }}
    .provider-url {{
      font-size: 7.5pt;
      margin: -4px 0 8px 0;
    }}
    .provider-url a {{
      color: {_SEAFOAM};
      text-decoration: none;
    }}
    .rating-pill {{
      font-size: 7pt;
      font-weight: 700;
      background: {_PALE_GREEN};
      color: {_TEAL};
      border: 1px solid {_SEAFOAM};
      border-radius: 20px;
      padding: 2px 9px;
      white-space: nowrap;
    }}
    .surgeon-pill {{
      font-size: 7pt;
      font-weight: 500;
      background: {_BLUE_LIGHT};
      color: {_TEAL};
      border: 1px solid {_BLUE};
      border-radius: 20px;
      padding: 2px 9px;
      white-space: nowrap;
    }}

    .traits {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-bottom: 8px;
    }}
    .trait-label {{
      font-size: 6.5pt;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 4px;
    }}
    .strengths-label  {{ color: #1d6b4a; }}
    .weaknesses-label {{ color: #4a7080; }}

    .trait-col ul {{
      padding-left: 13px;
      font-size: 8pt;
      color: #2a5055;
    }}
    .trait-col li {{ margin-bottom: 2px; }}

    .locations-block {{
      background: {_BLUE_LIGHT};
      border-radius: 4px;
      padding: 5px 10px;
      margin-bottom: 8px;
      border-left: 3px solid {_BLUE};
    }}
    .locations-label {{
      font-size: 6.5pt;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #4a7080;
      margin-bottom: 3px;
    }}
    .locations-list {{
      padding-left: 13px;
      margin: 0;
    }}
    .locations-list li {{
      font-size: 7.5pt;
      color: #2a5055;
      margin-bottom: 1px;
    }}
    .loc-name {{ font-weight: 500; }}
    .loc-rating {{ color: #0F4146; font-weight: 600; }}
    .loc-google {{ color: {_TEAL}; font-weight: 600; }}
    .loc-addr {{ color: #7a9095; font-style: italic; }}

    .best-for {{
      font-size: 8pt;
      color: #3a5a60;
      margin-bottom: 4px;
    }}
    .summary {{
      font-size: 8pt;
      color: {_TEAL};
      font-style: italic;
      line-height: 1.45;
    }}
    /* ── Teaser blur mechanics ─────────────────────── */
    .teaser-blur-wrapper {{
      position: relative;
      margin-top: 10px;
      border-radius: 6px;
      overflow: hidden;
    }}
    .teaser-blur-content {{
      filter: blur(2px);
      user-select: none;
      pointer-events: none;
    }}
    /* Simplified Patient Pulse obscures competitors harder than the teaser (2px). */
    .simplified-blur-content {{
      filter: blur(2.42px);
      user-select: none;
      pointer-events: none;
    }}
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
      border: 1.5px dashed {_SEAFOAM};
    }}
    .teaser-blur-overlay .blur-lock,
    .teaser-blur-overlay .blur-cta-heading,
    .teaser-blur-overlay .blur-cta-sub,
    .teaser-blur-overlay .blur-cta-actions {{
      background: rgba(238,247,241,0.92);
      border-radius: 4px;
      padding: 2px 8px;
    }}
    .blur-lock {{
      font-size: 18pt;
      margin-bottom: 5px;
    }}
    .blur-cta-heading {{
      font-size: 10pt;
      font-weight: 700;
      color: {_TEAL};
      margin-bottom: 5px;
    }}
    .blur-cta-sub {{
      font-size: 7.5pt;
      color: #3a5a60;
      line-height: 1.45;
      margin-bottom: 9px;
      max-width: 360px;
    }}
    .blur-cta-actions {{
      font-size: 9pt;
      font-weight: 600;
      color: {_TEAL};
    }}
    .blur-phone {{ font-weight: 700; }}
    .blur-demo-link {{
      color: #0a5c70;
      font-weight: 700;
      text-decoration: underline;
    }}

    /* ── Improvement Roadmap section ───────────────── */
    .roadmap-section {{
      margin: 20px 20px 8px;
      background: {_PALE_GREEN};
      border-radius: 8px;
      border: 1.5px solid {_SEAFOAM};
      padding: 16px 20px;
      page-break-inside: avoid;
    }}
    .roadmap-header {{
      margin-bottom: 14px;
      padding-bottom: 10px;
      border-bottom: 1px solid {_SEAFOAM};
    }}
    .roadmap-title {{
      font-size: 11pt;
      font-weight: 700;
      color: {_TEAL};
      margin-bottom: 3px;
    }}
    .roadmap-subtitle {{
      font-size: 7.5pt;
      color: #3a5a60;
      line-height: 1.4;
    }}
    .roadmap-items {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .roadmap-item {{
      background: #fff;
      border-radius: 6px;
      padding: 10px 12px;
      border: 1px solid {_SEAFOAM};
    }}
    .roadmap-tier-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 3px;
    }}
    .roadmap-tier-name {{
      font-size: 8pt;
      font-weight: 700;
      color: {_TEAL};
    }}
    .roadmap-locked-badge {{
      font-size: 6pt;
      font-weight: 700;
      color: #0a5c70;
      background: {_BLUE_LIGHT};
      padding: 1px 5px;
      border-radius: 3px;
      white-space: nowrap;
    }}
    .roadmap-tier-desc {{
      font-size: 6.5pt;
      color: #7a9095;
      margin-bottom: 6px;
      line-height: 1.35;
    }}
    .roadmap-blur-content {{
      filter: blur(2.1px);
      font-size: 7pt;
      color: {_TEAL};
      line-height: 1.4;
      user-select: none;
    }}
    .roadmap-cta {{
      text-align: center;
      border-top: 1px solid {_SEAFOAM};
      padding-top: 12px;
    }}
    .roadmap-cta-text {{
      font-size: 8pt;
      color: #3a5a60;
      line-height: 1.5;
      margin-bottom: 8px;
    }}
    .roadmap-cta-actions {{
      font-size: 9pt;
      font-weight: 600;
      color: {_TEAL};
    }}
    .roadmap-cta-actions a {{
      color: #0a5c70;
      text-decoration: underline;
    }}

    /* ── Market overview + AI Visibility verdict ────── */
    .overview p, .verdict p {{
      font-size: 9pt;
      color: {_TEAL};
      line-height: 1.55;
      margin-bottom: 7px;
    }}
    .verdict {{
      background: {_PALE_GREEN};
      border-radius: 6px;
      padding: 11px 15px;
      margin-bottom: 22px;
      break-inside: avoid;
    }}
    .overview {{ margin-bottom: 22px; }}

    /* ── AI Visibility score + tier bars ────────────── */
    .aivs {{ display: flex; align-items: center; gap: 14px; margin: 2px 0 10px; }}
    .aivs-score {{
      font-size: 22pt; font-weight: 800; line-height: 1; color: {_TEAL};
      min-width: 64px; text-align: center;
    }}
    .aivs-score .out {{ font-size: 9pt; font-weight: 600; color: #7a9095; }}
    .aivs-label {{
      font-size: 6.5pt; font-weight: 700; letter-spacing: 0.1em;
      text-transform: uppercase; color: #177B6E; margin-bottom: 1px;
    }}
    .aivs-sublabel {{
      font-size: 5.5pt; font-weight: 600; letter-spacing: 0.09em;
      text-transform: uppercase; color: #5a8090; margin-bottom: 4px;
    }}
    .aivs-nat-q-lbl {{
      font-size: 5.5pt; font-weight: 700; letter-spacing: 0.09em;
      text-transform: uppercase; color: #5a8090; margin-top: 5px; margin-bottom: 1px;
    }}
    .aivs-nat-q-val {{
      font-size: 9.5pt; font-weight: 800; line-height: 1.2;
    }}
    .tier-bars {{ flex: 1; }}
    .tier-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }}
    .tier-name {{ font-size: 6.5pt; color: #3a5a60; width: 110px; text-align: right; }}
    .tier-track {{ flex: 1; height: 7px; background: #E3E8E8; border-radius: 4px; overflow: hidden; }}
    .tier-fill {{ display: block; height: 7px; background: #177B6E; border-radius: 4px; }}
    .tier-val {{ font-size: 6.5pt; font-weight: 700; color: {_TEAL}; width: 18px; }}

    /* ── Google footprint stat line ─────────────────── */
    .google-stat {{
      font-size: 7.5pt; color: #3a5a60; margin-bottom: 6px;
      padding: 3px 0 5px; border-bottom: 1px solid #E3E8E8;
    }}
    .google-stat strong {{ color: {_TEAL}; }}
    .google-gap {{ color: #B45309; font-style: italic; }}
    .disqualifier {{
      font-size: 7pt; font-weight: 700; color: #B42318;
      margin-bottom: 6px;
    }}

    /* ── Top recommendation ────────────────────────── */
    .recommendation {{
      background: {_PALE_GREEN};
      border-left: 4px solid {_SEAFOAM};
      border-radius: 0 6px 6px 0;
      padding: 13px 17px;
      margin-bottom: 22px;
      break-inside: avoid;
    }}
    .recommendation p {{
      font-size: 9.5pt;
      color: {_TEAL};
      line-height: 1.55;
    }}

    /* ── Practical advice ───────────────────────────── */
    .advice {{ margin-bottom: 22px; }}
    .advice ol {{ padding-left: 18px; margin-top: 0; }}
    .advice li {{
      font-size: 8.5pt;
      margin-bottom: 6px;
      color: {_TEAL};
      line-height: 1.5;
    }}
    .advice-group {{ margin-bottom: 18px; }}
    .advice-group-title {{
      font-size: 9.5pt;
      font-weight: 700;
      color: {_TEAL};
      margin: 14px 0 2px;
      letter-spacing: 0.1px;
    }}
    .advice-group-desc {{
      font-size: 8pt;
      color: #555;
      font-style: italic;
      margin-bottom: 5px;
    }}

    /* ── Patient Voice ─────────────────────────────── */
    .patient-voice {{
      background: {_PALE_GREEN};
      border-left: 3px solid {_SEAFOAM};
      border-radius: 0 4px 4px 0;
      padding: 5px 10px;
      margin-bottom: 6px;
    }}
    .pv-label {{
      font-size: 6.5pt;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #177B6E;
      margin-bottom: 2px;
    }}
    .pv-text {{
      font-size: 7.5pt;
      color: {_TEAL};
      line-height: 1.45;
    }}

    /* ── Quality & Accreditation signals ────────────── */
    .quality-signals {{
      margin-bottom: 8px;
      padding-bottom: 6px;
      border-bottom: 1px solid #E3E8E8;
    }}
    .qs-label {{
      font-size: 6.5pt;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #7a9095;
      margin-bottom: 4px;
    }}
    .qs-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-bottom: 4px;
    }}
    .qs-badge {{
      font-size: 6.5pt;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 3px;
      border: 1.5px solid;
      white-space: nowrap;
    }}
    .qs-leapfrog-A {{ color: #1d6b4a; border-color: #1d6b4a; background: #f0faf4; }}
    .qs-leapfrog-B {{ color: #2a7a5e; border-color: #2a7a5e; background: #f0faf4; }}
    .qs-leapfrog-C {{ color: #7a5e00; border-color: #b38b00; background: #fffbeb; }}
    .qs-leapfrog-D {{ color: #8b4000; border-color: #c05c1a; background: #fff5ee; }}
    .qs-leapfrog-F {{ color: #8b0000; border-color: #c00000; background: #fff0f0; }}
    .qs-leapfrog-N {{ color: #7a9095; border-color: #c0d4d8; background: {_PALE_GREEN}; }}
    .outcomes-safety {{
      margin: 4px 0 6px;
      padding: 6px 8px;
      background: #f5f8fa;
      border: 1px solid #c8dde2;
      border-radius: 4px;
    }}
    .os-label {{
      font-size: 6.5pt;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #6a8a90;
      margin-bottom: 4px;
    }}
    .os-row {{
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 3px;
    }}
    .os-key {{
      font-size: 7pt;
      font-weight: 600;
      color: #3a5a60;
      min-width: 148px;
    }}
    .os-absent {{
      font-size: 7pt;
      color: #8aacb2;
      font-style: italic;
    }}
    .os-verify {{
      font-size: 6pt;
      color: #9ab0b5;
      margin-top: 3px;
    }}
    .qs-accred {{
      color: #0a5c70;
      border-color: {_BLUE};
      background: {_BLUE_LIGHT};
    }}
    .qs-cms-5 {{ color: #1d6b4a; border-color: #1d6b4a; background: #f0faf4; }}
    .qs-cms-4 {{ color: #2a7a5e; border-color: #2a7a5e; background: #f0faf4; }}
    .qs-cms-3 {{ color: #7a5e00; border-color: #b38b00; background: #fffbeb; }}
    .qs-cms-2 {{ color: #8b4000; border-color: #c05c1a; background: #fff5ee; }}
    .qs-cms-1 {{ color: #8b0000; border-color: #c00000; background: #fff0f0; }}
    .qs-usnews-ranked {{
      color: #1a3a6e; border-color: #2a5ab0; background: #eef3fc;
    }}
    .qs-usnews-hp {{
      color: #2a4a80; border-color: #6a8ac0; background: #f4f6fc;
    }}
    .qs-quality {{
      font-size: 7pt;
      color: #3a5a60;
      line-height: 1.4;
    }}

    /* ── Score band + profile chip ───────────────────── */
    .score-band {{
      font-size: 6.5pt; font-weight: 700; letter-spacing: 0.06em;
      text-transform: uppercase; border-radius: 3px; padding: 1px 5px;
      margin-left: 5px; vertical-align: middle;
    }}
    .score-band-strong {{ background: #e8f5ee; color: #1a6b3e; }}
    .score-band-good   {{ background: #e6f4f2; color: #1a5f5a; }}
    .score-band-fair   {{ background: #fef3cd; color: #7a5a00; }}
    .score-band-limited {{ background: #fef0e6; color: #7a3a00; }}
    .score-band-weak   {{ background: #fde8e8; color: #7a0000; }}
    .profile-chip {{
      font-size: 6pt; font-weight: 600; letter-spacing: 0.08em;
      text-transform: uppercase; color: #7a9095; margin-top: 3px;
    }}
    .ceiling-note {{
      font-size: 5.5pt; font-weight: 600; color: #8b3a00;
      background: #fef0e6; border-radius: 3px; padding: 2px 5px;
      margin-top: 4px; display: inline-block;
    }}

    /* ── Public & Social Ratings section header ─────── */
    .google-stat-section {{ margin-bottom: 6px; }}
    .google-stat-label {{
      font-size: 6.5pt; font-weight: 700; letter-spacing: 0.1em;
      text-transform: uppercase; color: #7a9095; margin-bottom: 3px;
    }}

    /* ── What AI Assistants Currently See ───────────── */
    .ai-says {{
      background: {_PALE_GREEN};
      border-left: 3px solid {_TEAL};
      border-radius: 0 4px 4px 0;
      padding: 7px 10px;
      margin-bottom: 8px;
    }}
    .ai-says-label {{
      font-size: 6.5pt; font-weight: 700; letter-spacing: 0.1em;
      text-transform: uppercase; color: #177B6E; margin-bottom: 1px;
    }}
    .ai-says-source {{
      font-size: 6pt; font-weight: 600; letter-spacing: 0.06em;
      text-transform: uppercase; color: #7a9095; margin-bottom: 4px;
    }}
    .ai-says-text {{
      font-size: 7.5pt; color: {_TEAL}; line-height: 1.45; font-style: italic;
      margin-bottom: 4px;
    }}
    .ai-says-footnote {{
      font-size: 6.5pt; color: #7a9095; line-height: 1.4;
    }}

    /* ── Trauma / Teaching pills ────────────────────── */
    .trauma-pill {{
      font-size: 6.5pt; font-weight: 700; padding: 2px 7px;
      border-radius: 3px; border: 1.5px solid #c05c1a;
      color: #8b4000; background: #fff5ee; white-space: nowrap;
    }}
    .teaching-pill {{
      font-size: 6.5pt; font-weight: 700; padding: 2px 7px;
      border-radius: 3px; border: 1.5px solid #6a5ab0;
      color: #3a2a80; background: #f0eeff; white-space: nowrap;
    }}

    /* ── Disclaimer ─────────────────────────────────── */
    .disclaimer {{
      border-top: 1px solid {_PALE_GREEN};
      padding-top: 12px;
      font-size: 6.5pt;
      color: #7a9095;
      line-height: 1.5;
      margin-bottom: 40px;
    }}
    .disclaimer strong {{ color: #5a7880; }}
  </style>
</head>
<body>

<div class="cover">
  {logo_tag}
  <div class="cover-eyebrow">{cover_eyebrow}</div>
  {cover_report_sub}
  <div class="cover-location">{cover_loc}</div>
  <div class="cover-specialty">{cover_spec}</div>
  {cover_sub}
  <div class="cover-meta">
    <span>Generated {date_str}</span>
    <span>Confidential — For Client Use Only</span>
  </div>
</div>

<div class="accent-bar"></div>

<div class="content">

  {overview_html}
  {verdict_html}
  {rankings_html}

  <div class="recommendation">
    <div class="section-title" style="margin-bottom:10px;">{recommendation_title}</div>
    <p>{_e(_strip_md(result.top_recommendation))}</p>
  </div>

  <div class="advice">
    <div class="section-title">{advice_title}</div>
    {_advice_html()}
  </div>

  <div class="disclaimer">
    <strong>Data Limitations &amp; Disclaimer</strong><br>
    {_e(result.disclaimer)}
  </div>

  {_practice_reputation_table_html(result.practice_composite_rows, result.generated_at.strftime("%B %d, %Y")) if result.practice_composite_rows else ""}

  {appendix_html}

</div>
</body>
</html>"""
    # Apply brand color overrides via string replacement
    _primary = brand_cfg["primary"]
    _pale    = brand_cfg["pale"]
    _accent  = brand_cfg["accent"]
    if _primary != _TEAL:
        _html = _html.replace(_TEAL, _primary)
    if _pale != _PALE_GREEN:
        _html = _html.replace(_PALE_GREEN, _pale)
    if _accent != _SEAFOAM:
        _html = _html.replace(_SEAFOAM, _accent)
    # Inject any brand-specific CSS overrides before the closing </style>
    if brand_cfg.get("css_overrides"):
        _html = _html.replace("</style>", brand_cfg["css_overrides"] + "  </style>", 1)
    return _html


# ── Comparison PDF ────────────────────────────────────────────────────────────

def _comparison_overview_block(result: AnalysisResult, label: str, mixed_rubric_note: bool = False) -> str:
    """Organization overview + AI Visibility verdict for one entity in the comparison.

    Pillar labels are driven by each entity's own weighting profile so that
    practice-rubric entities show practice pillar names and hospital-rubric entities
    show hospital pillar names — never a hardcoded set.
    """
    from .scoring import TIER_KEYS
    p = result.rankings[0] if result.rankings else None
    name = _e(result.report_title or result.entity_name or result.location)
    score_html = ""
    if p and p.ai_visibility_score is not None:
        quartile, band_label = grade_from_score(p.ai_visibility_score)
        q_color = _QUARTILE_COLORS.get(quartile, _TEAL)
        score_html = (
            f'<div style="line-height:1">'
            f'<span style="font-size:24pt;font-weight:800;color:{_TEAL}">{p.ai_visibility_score}</span>'
            f'<span style="font-size:10pt;font-weight:700;color:#aabcc0;vertical-align:top;display:inline-block;padding-top:5px;margin-left:2px">/100</span>'
            f'</div>'
            f'<div style="margin-top:10px;border-left:3px solid {q_color};background:#f5f8fa;border-radius:0 5px 5px 0;padding:7px 12px 7px 10px">'
            f'<div style="font-size:5.5pt;font-weight:700;letter-spacing:0.09em;text-transform:uppercase;color:#7a9095;margin-bottom:3px">National Quartile</div>'
            f'<div style="font-size:10pt;font-weight:800;color:{q_color}">{_e(_quartile_label(quartile))} <span style="font-size:7.5pt;font-weight:500;color:#5a7880">&middot;&nbsp;{_e(band_label)}</span></div>'
            f'</div>'
        )
    tier_html = ""
    if p:
        ts = p.tier_scores
        # Use profile-correct pillar labels — single source of truth.
        tier_labels = _tier_labels(p.weighting_profile)
        bars = []
        for key in TIER_KEYS:
            tier_name = tier_labels.get(key, key)
            val = getattr(ts, key, None)
            if val is None:
                continue
            pct = max(0, min(100, val))
            bars.append(
                f'<div style="margin-bottom:6px">'
                f'<div style="font-size:8pt;color:#666;margin-bottom:2px">{_e(tier_name)}</div>'
                f'<div style="background:#e8f5f3;border-radius:4px;height:8px;overflow:hidden">'
                f'<div style="background:{_TEAL};height:100%;width:{pct}%"></div>'
                f'</div>'
                f'<div style="font-size:8pt;color:{_TEAL};font-weight:600;text-align:right">{val}</div>'
                f'</div>'
            )
        tier_html = "".join(bars)

    verdict = _e(result.ai_visibility_verdict or result.market_overview or "")
    mixed_note_html = (
        '<div style="font-size:7.5pt;color:#7a9095;font-style:italic;margin-top:8px">'
        'Note: the two entities are scored on different rubrics (hospital and practice editions) '
        '— pillar names and weights differ; scores are not directly comparable.</div>'
    ) if mixed_rubric_note else ""
    return f"""
  <div style="margin-bottom:24px;padding:20px;border:2px solid {_TEAL};border-radius:8px;page-break-inside:avoid">
    <div style="font-size:9pt;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:6px">{_e(label)}</div>
    <div style="font-size:15pt;font-weight:700;color:{_TEAL};margin-bottom:10px">{name}</div>
    <div style="display:grid;grid-template-columns:160px 1fr;gap:20px">
      <div>
        <div style="font-size:8pt;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#999;margin-bottom:8px">{SECTION_COMPARISON_SCORE_LABEL}</div>
        <div style="margin-bottom:14px">{score_html}</div>
        {tier_html}
      </div>
      <div>
        <div style="font-size:8pt;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#999;margin-bottom:8px">{SECTION_COMPARISON_VERDICT_LABEL}</div>
        <div style="font-size:9pt;line-height:1.65;color:#333">{verdict}</div>
      </div>
    </div>
    {mixed_note_html}
  </div>"""


def _comparison_summary_block(comparison) -> str:
    """Renders the comparison summary section."""
    sims = "".join(f"<li>{_e(s)}</li>" for s in comparison.similarities)
    diffs = "".join(f"<li>{_e(d)}</li>" for d in comparison.differences)
    verdict_paras = "".join(
        f"<p style='margin:0 0 10px'>{_e(para.strip())}</p>"
        for para in (comparison.verdict or "").split("\n")
        if para.strip()
    )
    return f"""
  <div style="margin-bottom:28px;page-break-inside:avoid">
    <div class="section-title">Comparison Summary</div>
    {f'<div style="font-size:11pt;font-weight:600;color:{_TEAL};margin-bottom:16px">{_e(comparison.headline)}</div>' if comparison.headline else ''}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:18px">
      <div style="background:#f0faf7;border-radius:6px;padding:14px 16px">
        <div style="font-size:8pt;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#1a7a4a;margin-bottom:8px">Where They Are Similar</div>
        <ul style="margin:0;padding-left:16px;font-size:9pt;line-height:1.7;color:#333">{sims}</ul>
      </div>
      <div style="background:#fdf4f4;border-radius:6px;padding:14px 16px">
        <div style="font-size:8pt;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#991b1b;margin-bottom:8px">Key Differences</div>
        <ul style="margin:0;padding-left:16px;font-size:9pt;line-height:1.7;color:#333">{diffs}</ul>
      </div>
    </div>
    <div style="font-size:9pt;line-height:1.65;color:#333">{verdict_paras}</div>
  </div>"""


def _entity_deep_dive(result: AnalysisResult, include_roadmap: bool = True) -> str:
    """Full individual-report content for one entity: card + assessment + improvement."""
    p = result.rankings[0] if result.rankings else None
    name = _e(result.report_title or result.entity_name or result.location)

    card_html = _individual_entity_card(p) if p else ""

    # AI Visibility Assessment
    assessment = _e(_strip_md(result.top_recommendation or ""))
    assessment_html = f"""
  <div class="recommendation" style="margin-top:20px">
    <div class="section-title" style="margin-bottom:10px">{SECTION_ASSESSMENT}</div>
    <p>{assessment}</p>
  </div>"""

    improvement_html = ""
    if include_roadmap:
        if result.improvement_sections:
            parts = []
            for sec in result.improvement_sections:
                items_li = "\n".join(f"<li>{_e(_strip_md(item))}</li>" for item in sec.items)
                parts.append(
                    f'<div class="advice-group">'
                    f'<div class="advice-group-title">{_e(_strip_md(sec.title))}</div>'
                    f'<div class="advice-group-desc">{_e(_strip_md(sec.description))}</div>'
                    f'<ol>{items_li}</ol>'
                    f'</div>'
                )
            improvement_body = "\n".join(parts)
        elif result.practical_advice:
            flat = "\n".join(f"<li>{_e(_strip_md(a))}</li>" for a in result.practical_advice)
            improvement_body = f"<ol>{flat}</ol>"
        else:
            improvement_body = ""

        improvement_html = f"""
  <div class="advice" style="margin-top:20px">
    <div class="section-title">AI Visibility Improvement Roadmap</div>
    {improvement_body}
  </div>"""

    disclaimer_html = f"""
  <div class="disclaimer" style="margin-top:20px">
    <strong>Data Limitations &amp; Disclaimer</strong><br>
    {_e(result.disclaimer)}
  </div>"""

    return f"""
  <div style="page-break-before:always">
    <div class="section-title" style="font-size:13pt;margin-bottom:16px">
      {DEEP_DIVE_HEADER_TPL.format(name=name)}
    </div>
    {card_html}
    {assessment_html}
    {improvement_html}
    {disclaimer_html}
  </div>"""


def _build_comparison_html(
    result_a: AnalysisResult,
    result_b: AnalysisResult,
    comparison,
    brand_cfg: dict,
    teaser: bool = False,
) -> str:
    """Build the full HTML for a comparison PDF."""
    import base64 as _b64
    date_str = result_a.generated_at.strftime("%B %d, %Y")
    # Prefer the org brand (report_title) over the incidental search-anchor name —
    # for an aggregate practice the anchor may be one clinic (e.g. "OrthoCarolina
    # University"), but the report is about the whole organization.
    name_a = _e(result_a.report_title or result_a.entity_name or result_a.location)
    name_b = _e(result_b.report_title or result_b.entity_name or result_b.location)

    _has_custom_logo = brand_cfg.get("logo_html") or brand_cfg.get("logo_path")
    logo_uri = None if _has_custom_logo else _logo_data_uri()
    if brand_cfg.get("logo_html"):
        logo_tag = brand_cfg["logo_html"]
    elif brand_cfg.get("logo_path") and brand_cfg["logo_path"].exists():
        _lp = brand_cfg["logo_path"]
        _ldat = _b64.b64encode(_lp.read_bytes()).decode()
        logo_tag = f'<img src="data:image/svg+xml;base64,{_ldat}" alt="Logo" style="height:52px;width:auto;display:block;margin-bottom:28px">'
    else:
        logo_tag = f'<img class="cover-logo" src="{logo_uri}" alt="RLDatix">' if logo_uri else ""

    # Reuse the base HTML/CSS from a minimal individual result (entity_a) then override content
    base_html = _build_html(result_a, brand_cfg)
    # Extract everything up to and including <body>
    body_start = base_html.index("<body>") + len("<body>")
    head_section = base_html[:body_start]
    # Strip the existing body content — we'll replace entirely
    body_content = f"""
<div class="cover">
  {logo_tag}
  <div class="cover-eyebrow">{COVER_COMPARISON}</div>
  <div class="cover-report-sub">{COVER_REPORT_SUB}</div>
  <div class="cover-location">{name_a}</div>
  <div class="cover-specialty" style="font-size:13pt">vs.</div>
  <div class="cover-location" style="font-size:18pt">{name_b}</div>
  <div class="cover-meta">
    <span>Generated {date_str}</span>
    <span>Confidential — For Client Use Only</span>
  </div>
</div>

<div class="accent-bar"></div>

<div class="content">

  <div class="section-title" style="font-size:13pt;margin-bottom:20px">{SECTION_COMPARISON_OVERVIEWS}</div>

  {_comparison_overview_block(result_a, "Entity A", mixed_rubric_note=(result_a.entity_type != result_b.entity_type))}
  {_comparison_overview_block(result_b, "Entity B", mixed_rubric_note=(result_a.entity_type != result_b.entity_type))}

  {_comparison_summary_block(comparison)}

  {_entity_deep_dive(result_a, include_roadmap=False)}
  {_practice_reputation_table_html(result_a.practice_composite_rows, result_a.generated_at.strftime("%B %d, %Y")) if result_a.practice_composite_rows else ""}

  {_entity_deep_dive(result_b, include_roadmap=False)}
  {_practice_reputation_table_html(result_b.practice_composite_rows, result_b.generated_at.strftime("%B %d, %Y")) if result_b.practice_composite_rows else ""}

  {_practice_appendix_html() if (result_a.entity_type == "practice" or result_b.entity_type == "practice") else _appendix_html()}

</div>
</body>
</html>"""

    html = head_section + body_content
    # Apply brand color overrides
    _primary = brand_cfg["primary"]
    _pale    = brand_cfg["pale"]
    _accent  = brand_cfg["accent"]
    if _primary != _TEAL:
        html = html.replace(_TEAL, _primary)
    if _pale != _PALE_GREEN:
        html = html.replace(_PALE_GREEN, _pale)
    if _accent != _SEAFOAM:
        html = html.replace(_SEAFOAM, _accent)
    if brand_cfg.get("css_overrides"):
        html = html.replace("</style>", brand_cfg["css_overrides"] + "  </style>", 1)
    return html


def render_comparison_pdf(
    result_a: AnalysisResult,
    result_b: AnalysisResult,
    comparison,
    pdf_path: "Path",
    brand: str = "original",
    teaser: bool = False,
) -> None:
    """Render a comparison report PDF from two AnalysisResult objects."""
    from playwright.sync_api import sync_playwright

    cfg = _BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"])
    html = _build_comparison_html(result_a, result_b, comparison, cfg, teaser=teaser)
    _cached_lbl = _fmt_cached(getattr(result_a, "data_collected_at", None) or result_a.generated_at)
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
                'font-size:9px;color:#7a9095;display:flex;justify-content:space-between;'
                'align-items:center;padding:0 48px 8px;box-sizing:border-box">'
                f'<span>{_cached_lbl}</span>'
                '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
                "</div>"
            ),
        )
        browser.close()


# ── Practice Composite reputation table ──────────────────────────────────────

_PC_PLATFORM_LABELS = {
    "google": "Google",
    "healthgrades": "Healthgrades",
    "vitals": "Vitals",
    "webmd": "WebMD",
    "yelp": "Yelp",
    "ratemds": "RateMDs",
}


def _practice_reputation_table_html(rows: list[dict], run_date: str = "") -> str:
    """HTML for the Practice Composite appendix table.

    Columns: Practice (Entity) | Avg Rating | Total Reviews | Platforms Found | Collected
    Practice names are plain text. Each platform name in Platforms Found links to that
    platform's captured profile page; platforms without a captured URL render unlinked.
    Zero-platform rows are entirely plain text.
    """
    if not rows:
        return ""

    T   = "#0F4146"
    M   = "#7a9095"
    BD  = "#d0e4e8"
    ALT = "#f8fbfa"
    LA  = "#1a6e9e"  # link colour

    def _link(text: str, url: str | None) -> str:
        if not url:
            return _e(text)
        safe_url = _e(url)
        return (
            f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
            f'class="pc-link" style="color:{LA};text-decoration:underline">{_e(text)}</a>'
        )

    def _rating_cell(row: dict) -> str:
        if row.get("not_established"):
            return '<span style="color:#7a9095;font-style:italic">Not established</span>'
        # Anchor row: use the single verified Google rating so it matches the
        # report header and footprint line (one stored value per Item 2).
        if row.get("is_anchor"):
            g = row.get("google_rating")
            if g is None:
                return "&#8212;"
            return f'<strong>{g:.1f}</strong>&#9733; Google'
        avg = row.get("avg_rating")
        if avg is None:
            return "&#8212;"
        unverified = not row.get("affiliation_verified", True)
        suffix = (
            ' <span style="font-size:8pt;color:#7a9095">(unverified affiliation)</span>'
            if unverified else ""
        )
        return f'<strong>{avg:.1f}</strong> / 5{suffix}'

    def _platforms_cell(row: dict) -> str:
        count = row.get("platforms_found", 0)
        if not count:
            return "&#8212;"
        entries = row.get("platform_entries")
        if entries:
            linked = []
            for pk, _pc, pu in entries:
                label = _PC_PLATFORM_LABELS.get(pk, pk.capitalize())
                linked.append(_link(label, pu))
            return f"{count}: {', '.join(linked)}"
        # Fallback for rows loaded from DB (no platform_entries): plain text
        return f"{count}: {_e(row.get('platforms_list', ''))}"

    # Pin anchor rows first; sort remaining rows by total_reviews desc
    anchor_rows_sorted = [r for r in rows if r.get("is_anchor")]
    other_rows_sorted  = sorted(
        [r for r in rows if not r.get("is_anchor")],
        key=lambda r: (r.get("not_established", False), -(r.get("total_reviews") or 0)),
    )
    rows = anchor_rows_sorted + other_rows_sorted

    # Oldest collection date across practice rows + physician sub-rows; used in disclaimer.
    _all_dates = [r.get("collection_date", "") for r in rows]
    for r in rows:
        _all_dates += [ph.get("collection_date", "") for ph in (r.get("physicians") or [])]
    _oldest_date = min((d for d in _all_dates if d), default="")

    has_anchor = bool(anchor_rows_sorted)
    non_anchor_rows = other_rows_sorted
    no_affiliates = has_anchor and len(non_anchor_rows) == 0

    # Alternate-row index counts only non-anchor rows so banding stays consistent
    alt_counter = 0
    rows_html = ""
    for row in rows:
        is_anchor = row.get("is_anchor", False)
        if is_anchor:
            row_style = f'border-bottom:1px solid {BD};background:#edf6f7'
        else:
            row_style = f'border-bottom:1px solid {BD};' + (f'background:{ALT}' if alt_counter % 2 == 1 else '')
            alt_counter += 1
        collection = row.get("collection_date", "")
        raw_name = row.get("practice_name", "")
        name_suffix = (
            f' <span style="font-size:8pt;color:#7a9095;font-weight:400">(analyzed)</span>'
            if is_anchor else ""
        )
        name = f'<span style="font-weight:{"700" if is_anchor else "500"}">{_e(raw_name)}</span>{name_suffix}'
        rows_html += f"""
    <tr style="{row_style}">
      <td style="padding:9px 12px">{name}</td>
      <td style="padding:9px 12px">{_rating_cell(row)}</td>
      <td style="padding:9px 12px;text-align:right">{row.get('total_reviews') or '&#8212;'}</td>
      <td style="padding:9px 12px">{_platforms_cell(row)}</td>
      <td style="padding:9px 12px;font-size:8pt;color:{M}">{_e(collection)}</td>
    </tr>"""

        # ── Physician sub-rows (sorted by total_reviews desc) ────────────────
        physicians = sorted(
            row.get("physicians") or [],
            key=lambda p: (p.get("not_established", False), -(p.get("total_reviews") or 0)),
        )
        for ph in physicians:
            _raw_ph = ph.get("physician_name", "")
            _ph_lower = _raw_ph.lower()
            if not any(_ph_lower.startswith(t) for t in ("dr.", "pa-", "np ", "rn ", "do ", "md ")):
                _raw_ph = "Dr. " + _raw_ph
            ph_name = _e(_raw_ph)
            ph_coll = ph.get("collection_date", "")
            # Hold list: render partial-hold state instead of rating data
            try:
                from .holds import is_held as _is_held
                _ph_held = _is_held(
                    ph.get("physician_name", ""),
                    entity=row.get("practice_name", ""),
                )
            except Exception:
                _ph_held = False
            if _ph_held:
                _ph_rating_cell = (
                    '<span style="color:#7a9095;font-style:italic">'
                    '&#9680; Partial &#8212; identity verification pending</span>'
                )
                _ph_reviews = "&#8212;"
                _ph_platforms = "&#8212;"
            else:
                _ph_rating_cell = _rating_cell(ph)
                _ph_reviews = str(ph.get("total_reviews") or "&#8212;")
                _ph_platforms = _platforms_cell(ph)
            rows_html += f"""
    <tr style="border-bottom:1px solid {BD};background:#fafcfc">
      <td style="padding:6px 12px 6px 28px;font-size:9pt;color:#4a6568">{ph_name}</td>
      <td style="padding:6px 12px;font-size:9pt">{_ph_rating_cell}</td>
      <td style="padding:6px 12px;text-align:right;font-size:9pt">{_ph_reviews}</td>
      <td style="padding:6px 12px;font-size:9pt">{_ph_platforms}</td>
      <td style="padding:6px 12px;font-size:8pt;color:{M}">{_e(ph_coll)}</td>
    </tr>"""

    if no_affiliates:
        rows_html += f"""
    <tr style="border-bottom:1px solid {BD}">
      <td colspan="5" style="padding:9px 12px;font-style:italic;color:{M}">No affiliated entities established</td>
    </tr>"""

    footer = ""
    if run_date:
        footer = (
            f'<p style="font-size:8pt;color:{M};margin-top:10px">'
            f'Run date: {_e(run_date)} &ensp;&middot;&ensp; '
            f'Platform data sourced from publicly available listings, current as of collection date. '
            f'Ratings may become stale after 90 days.</p>'
        )

    intro = (
        "Publicly available reputation data for this practice and affiliated entities "
        "under the same parent organization. The highlighted row is the analyzed practice. "
        "Avg Rating = review-count-weighted average across all platforms found. "
        "Platforms: Google, Healthgrades, Vitals, WebMD, Yelp, RateMDs (Zocdoc excluded)."
    ) if has_anchor else (
        "Publicly available reputation data for practices and facilities associated with this "
        "health system. Avg Rating = review-count-weighted average across all platforms found. "
        "Platforms: Google, Healthgrades, Vitals, WebMD, Yelp, RateMDs (Zocdoc excluded)."
    )

    _disclaimer_text = (
        "Reputation data reflects publicly available ratings and reviews at the time of "
        "collection and may lag current values. Results gathered via AI models may "
        "additionally be limited by those models’ training data dates. "
        "Ratings shown are point-in-time and are not updated in real time."
    )
    _date_suffix = f" (data collected as of {_e(_oldest_date)})" if _oldest_date else ""

    return f"""
<div style="margin-top:32px">
  <div style="font-size:12pt;font-weight:700;color:{T};margin-bottom:6px">
    Practice Composite &#8212; Associated Practice Reputation
  </div>
  <p style="font-size:9pt;color:{M};margin-bottom:14px">{_e(intro)}</p>
  <p style="font-size:8.5pt;color:{M};font-style:italic;margin-bottom:12px">{_disclaimer_text}{_date_suffix}</p>
  <table style="width:100%;border-collapse:collapse;font-size:10pt">
    <thead>
      <tr style="background:{T};color:#fff;font-size:9pt;font-weight:700;text-transform:uppercase;letter-spacing:0.06em">
        <th style="padding:9px 12px;text-align:left">Practice (Entity)</th>
        <th style="padding:9px 12px;text-align:left">Avg Rating</th>
        <th style="padding:9px 12px;text-align:right">Total Reviews</th>
        <th style="padding:9px 12px;text-align:left">Platforms Found</th>
        <th style="padding:9px 12px;text-align:left">Collected</th>
      </tr>
    </thead>
    <tbody>{rows_html}
    </tbody>
  </table>
  {footer}
</div>
"""

