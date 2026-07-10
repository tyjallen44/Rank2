from __future__ import annotations

import base64
import html as _html_lib
from pathlib import Path

from .models import AffiliationType, AnalysisResult, RankedProvider, SizeCategory
from .scoring import TIER_LABELS, PRACTICE_TIER_LABELS, PRACTICE_PROFILE_DISPLAY


def _tier_labels(profile: str | None) -> dict[str, str]:
    """Return the correct tier-label dict for a given weighting profile."""
    if profile and profile.startswith("practice_"):
        return PRACTICE_TIER_LABELS.get(profile, PRACTICE_TIER_LABELS["practice_procedural"])
    return TIER_LABELS.get(profile or "procedural", TIER_LABELS["procedural"])

# RLDatix brand palette (original)
_TEAL        = "#0F4146"
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


def render_pdf(result: AnalysisResult, pdf_path: Path, brand: str = "original") -> None:
    """Render a structured AnalysisResult to a branded PDF using Playwright."""
    from playwright.sync_api import sync_playwright

    cfg = _BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"])
    html = _build_html(result, cfg)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True,
        )
        browser.close()


def _e(text: str | None) -> str:
    return _html_lib.escape(str(text or ""))


def _rank_text_color(rank: int) -> str:
    # Blue and green badges are light — use dark teal text for contrast
    return _TEAL if rank in (2, 3) else "#ffffff"


def _score_band(score: int | None) -> tuple[str, str]:
    """Return (label, css_class) for the score band indicator."""
    if score is None:
        return "", ""
    if score >= 80:
        return "Strong", "score-band-strong"
    if score >= 65:
        return "Good", "score-band-good"
    if score >= 50:
        return "Fair", "score-band-fair"
    if score >= 35:
        return "Limited", "score-band-limited"
    return "Weak", "score-band-weak"


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
        rating_span = (
            f'&thinsp;—&thinsp;<span class="loc-rating">{_e(loc.overall_rating)}</span>'
            if loc.overall_rating else ""
        )
        google_span = ""
        if loc.google_rating is not None:
            count_txt = f"&thinsp;·&thinsp;{loc.google_review_count:,} reviews" if loc.google_review_count else ""
            google_span = f'&ensp;<span class="loc-google">{loc.google_rating:.1f}&#9733;{count_txt}</span>'
        addr_span = f'&ensp;<span class="loc-addr">{_e(loc.address)}</span>' if loc.address else ""
        parts.append(f'<li><span class="loc-name">{_e(loc.name)}</span>{rating_span}{google_span}{addr_span}</li>')
    items = "".join(parts)
    return f'<div class="locations-block"><div class="locations-label">Includes locations:</div><ul class="locations-list">{items}</ul></div>'


def _ai_says_block(p: RankedProvider) -> str:
    if not p.ai_says:
        return ""
    return f"""
    <div class="ai-says">
      <div class="ai-says-label">What AI Assistants Currently See
        <span style="font-size:6pt;font-weight:400;color:#7a9095;font-style:italic;margin-left:6px">
          &mdash; from training memory &amp; live retrieval &middot; Claude, ChatGPT, Gemini</span>
      </div>
      <div class="ai-says-text">{_e(p.ai_says)}</div>
    </div>"""


def _tier_row(label: str, value: int | None) -> str:
    width = value if isinstance(value, int) else 0
    val_txt = str(value) if isinstance(value, int) else "—"
    return (
        f'<div class="tier-row"><span class="tier-name">{_e(label)}</span>'
        f'<span class="tier-track"><span class="tier-fill" style="width:{width}%"></span></span>'
        f'<span class="tier-val">{val_txt}</span></div>'
    )


def _aivs_block(p: RankedProvider) -> str:
    """AI Visibility score + band + weighting profile + the four tier bars."""
    profile = p.weighting_profile or "procedural"
    labels = _tier_labels(profile)
    ts = p.tier_scores
    score = p.ai_visibility_score
    score_txt = str(score) if score is not None else "—"
    band, band_cls = _score_band(score)
    band_html = f'<span class="score-band {band_cls}">{band}</span>' if band else ""
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
        <div class="aivs-label">AI Visibility</div>
        <div class="aivs-score">{score_txt}<span class="out">/100</span>{band_html}</div>
        <div class="profile-chip">{profile_label}</div>
        {f'<div class="ceiling-note">⚠ Score capped at 74 ({_e(p.score_ceiling_reason)})</div>' if p.score_ceiling_applied else ""}
      </div>
      <div class="tier-bars">{rows}
        <div style="font-size:6pt;color:#aabcc0;margin-top:3px;font-style:italic">Scored per Appendix A methodology</div>
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

    if fd.verified and fd.rating is not None:
        recency = f" · {_e(fd.recency)}" if fd.recency else ""
        stars_filled = "&#9733;" * int(fd.rating)
        stars_empty  = "&#9734;" * (5 - int(fd.rating))
        front = (
            f'<strong>{fd.rating:.1f}&#9733;</strong>'
            f' <span style="font-size:7pt;color:#7a9095">{stars_filled}{stars_empty}</span>'
            f' &nbsp;·&nbsp; <strong>{fd.count or 0:,} reviews</strong>{recency}'
        )
    else:
        front = f'<strong>Not verified</strong> <span class="google-gap">— {_e(fd.reason or "no rated listing found")}</span>'

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
            f'across {loc} locations</strong>{pct_diff} '
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
        <span style="font-size:6.5pt;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#5a7880">{fd_label}:</span> {front} {fd_context}<br>
        {system_line}
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
      <div class="pv-text">{_e(p.patient_voice_summary)}</div>
    </div>"""


def _outcomes_data_status(p: RankedProvider) -> tuple[bool, bool]:
    """Returns (has_leapfrog_grade, has_cms_rating)."""
    grade = (p.leapfrog_grade or "").strip()
    first = grade[0].upper() if grade else ""
    has_leapfrog = bool(first and first in "ABCDF")
    has_cms = bool(p.cms_star_rating and 1 <= p.cms_star_rating <= 5)
    return has_leapfrog, has_cms


def _outcomes_safety_weaknesses(p: RankedProvider) -> list[str]:
    """When both Leapfrog and CMS data are absent, return items for Areas for Improvement."""
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
        leapfrog_cell = f'<span class="{css_cls}">{first}</span>'
    elif grade.lower() in ("not rated", "not_rated"):
        leapfrog_cell = '<span class="os-absent">Not rated in current survey cycle</span>'
    else:
        leapfrog_cell = '<span class="os-absent">Not currently participating in Leapfrog survey</span>'

    if has_cms:
        _star_css = {5: "qs-cms-5", 4: "qs-cms-4", 3: "qs-cms-3", 2: "qs-cms-2", 1: "qs-cms-1"}
        stars = "★" * p.cms_star_rating + "☆" * (5 - p.cms_star_rating)
        cms_cell = f'<span class="qs-badge {_star_css[p.cms_star_rating]}">{stars} ({p.cms_star_rating} of 5)</span>'
    else:
        cms_cell = '<span class="os-absent">No CMS Overall Star Rating published</span>'

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


def _quality_signals_block(p: RankedProvider) -> str:
    # CMS stars and Leapfrog grade are surfaced in _outcomes_safety_block above each card section
    usnews_html = ""
    for u in p.us_news_rankings:
        if u.recognition_type == "nationally_ranked" and u.rank:
            usnews_html += f'<span class="qs-badge qs-usnews-ranked">#{u.rank} {_e(u.category)}</span>'
        elif u.recognition_type == "nationally_ranked":
            usnews_html += f'<span class="qs-badge qs-usnews-ranked">Natl. Ranked · {_e(u.category)}</span>'
        else:
            usnews_html += f'<span class="qs-badge qs-usnews-hp">High-Perf. · {_e(u.category)}</span>'

    accred_html = "".join(
        f'<span class="qs-badge qs-accred">{_e(a)}</span>'
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
        f'<div class="qs-quality">{_e(p.cms_quality_highlights)}</div>'
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
    strengths_html = "".join(f"<li>{_e(s)}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(w)}</li>"
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
          <span class="rating-pill">{_e(p.overall_rating)}</span>
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
        <div class="summary">{_e(p.recommendation_summary)}</div>
      </div>
    </div>"""


def _individual_entity_card(p: RankedProvider) -> str:
    """Full-width card for individual entity reports — no rank badge."""
    strengths_html = "".join(f"<li>{_e(s)}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(w)}</li>"
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
          <span class="rating-pill">{_e(p.overall_rating)}</span>
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
        <div class="summary">{_e(p.recommendation_summary)}</div>
      </div>
    </div>"""


def _individual_teaser_card(p: RankedProvider) -> str:
    """Blurred individual entity card for teaser version."""
    _pc = (p.physician_count or "").strip()
    physician_html = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60 else ""
    )
    strengths_html = "".join(f"<li>{_e(s)}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(w)}</li>"
        for w in list(p.notable_weaknesses) + _outcomes_safety_weaknesses(p)
    )
    return f"""
    <div class="card" style="border:2px solid {_TEAL}">
      <div class="card-body" style="padding:16px 20px">
        <div class="card-top">
          <h3 class="provider-name" style="font-size:13pt">{_e(p.name)}</h3>
          {physician_html}
          {_trauma_teaching_pills(p)}
          <span class="rating-pill">{_e(p.overall_rating)}</span>
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
            <div class="blur-cta-sub">Access the complete AI Visibility analysis, detailed signal breakdown, and your personalized AI Visibility Improvement Roadmap.</div>
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


_TEASER_DEMO_URL = "https://www.rldatix.com/en-nam/book-a-demo/"
_TEASER_PHONE    = "866.338.8270"


def _teaser_card(p: RankedProvider, display_rank: int) -> str:
    bg = _RANK_COLORS.get(display_rank, _RANK_DEFAULT)
    text_color = _rank_text_color(display_rank)
    _pc = (p.physician_count or "").strip()
    physician_html = (
        f'<span class="surgeon-pill">{_e(_physician_label(_pc))}</span>'
        if _pc and _pc.lower() not in ("unknown", "") and len(_pc) <= 60 else ""
    )
    strengths_html = "".join(f"<li>{_e(s)}</li>" for s in p.key_strengths)
    weaknesses_html = "".join(
        f"<li>{_e(w)}</li>"
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
          <span class="rating-pill">{_e(p.overall_rating)}</span>
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
            <div class="blur-cta-sub">Access the complete competitive analysis, detailed signal breakdown, and your personalized AI Visibility Improvement Roadmap.</div>
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
      <div class="roadmap-title">&#128274;&nbsp; AI Visibility Improvement Roadmap</div>
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


def _appendix_html() -> str:
    """Appendix A — AI Visibility Methodology: Prompt Battery & Scoring Rubric."""
    T   = "#0F4146"
    S   = "#177B6E"
    M   = "#7a9095"
    BD  = "#d0e4e8"
    TXT = "#3a5a60"
    ALT = "#f8fbfa"

    def _part(n, title):
        return (
            f'<div style="background:{T};color:#fff;padding:7px 12px;margin:20px 0 12px;'
            f'border-radius:3px;font-size:7.5pt;font-weight:700;letter-spacing:0.14em;'
            f'text-transform:uppercase">{n} &mdash; {title}</div>'
        )

    def _sec(n, title):
        return (
            f'<div style="font-size:8pt;font-weight:700;color:{T};margin:14px 0 6px;'
            f'padding-bottom:4px;border-bottom:1.5px solid {BD}">'
            f'<span style="color:{S};margin-right:5px">{n}</span>{title}</div>'
        )

    def _cat(title):
        return (
            f'<div style="font-size:7.5pt;font-weight:700;color:{T};margin:10px 0 3px;'
            f'font-style:italic">{title}</div>'
        )

    def _p(text):
        return f'<p style="font-size:7.5pt;color:{TXT};line-height:1.55;margin-bottom:6px">{text}</p>'

    def _note(text):
        return (
            f'<p style="font-size:7pt;color:{M};line-height:1.5;margin-bottom:5px;'
            f'font-style:italic">{text}</p>'
        )

    def _tbl(*rows, header=None):
        th_s = (f'background:{T};color:#fff;padding:5px 8px;text-align:left;'
                f'font-weight:700;font-size:7pt;border:1px solid {BD}')
        td_s = f'border:1px solid {BD};padding:5px 8px;vertical-align:top;font-size:7pt;line-height:1.45;color:{TXT}'
        td_a = f'border:1px solid {BD};padding:5px 8px;vertical-align:top;font-size:7pt;line-height:1.45;color:{TXT};background:{ALT}'
        head = ""
        if header:
            cells = "".join(f'<th style="{th_s}">{c}</th>' for c in header)
            head = f'<thead><tr>{cells}</tr></thead>'
        body_rows = []
        for i, row in enumerate(rows):
            s = td_a if i % 2 else td_s
            cells = "".join(f'<td style="{s}">{c}</td>' for c in row)
            body_rows.append(f'<tr>{cells}</tr>')
        return (f'<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
                f'{head}<tbody>{"".join(body_rows)}</tbody></table>')

    def _ol(items):
        lis = "".join(
            f'<li style="font-size:7.5pt;color:{TXT};line-height:1.5;margin-bottom:4px">{i}</li>'
            for i in items
        )
        return f'<ol style="padding-left:18px;margin-bottom:8px">{lis}</ol>'

    def _ul(items):
        lis = "".join(
            f'<li style="font-size:7.5pt;color:{TXT};line-height:1.5;margin-bottom:4px">{i}</li>'
            for i in items
        )
        return f'<ul style="padding-left:18px;margin-bottom:8px">{lis}</ul>'

    def _prompts(items):
        rows = "".join(
            f'<tr>'
            f'<td style="width:22px;font-size:7pt;font-weight:700;color:{S};padding:3px 6px;vertical-align:top">{n}.</td>'
            f'<td style="font-size:7.5pt;color:{TXT};line-height:1.5;padding:3px 6px">{t}</td>'
            f'</tr>'
            for n, t in items
        )
        return f'<table style="border-collapse:collapse;margin-bottom:6px;width:100%">{rows}</table>'

    CODE = f'background:#f0f4f4;padding:1px 4px;border-radius:2px;font-size:7pt;font-family:monospace'

    return f"""
<div style="page-break-before:always;padding-top:10px">

  <div style="border-bottom:3px solid {T};padding-bottom:10px;margin-bottom:4px">
    <div style="font-size:7.5pt;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:{S};margin-bottom:4px">Appendix</div>
    <div style="font-family:'Barlow Condensed',Impact,sans-serif;font-size:20pt;font-weight:700;color:{T};line-height:1.1">AI Visibility Methodology</div>
    <div style="font-size:8pt;color:{M};margin-top:4px">Prompt Battery &amp; Scoring Rubric</div>
  </div>

  {_part("PART 1", "THE PROMPT BATTERY")}

  {_sec("1.1", "Purpose")}
  {_p("The prompt battery is a standardized, repeatable set of queries run against each major AI assistant to produce evidence &#8212; not impressions &#8212; of how a provider surfaces. Every <em>What AI Assistants Currently See</em> summary in the report body must be traceable to logged battery runs.")}

  {_sec("1.2", "Test Protocol")}
  {_tbl(
      ("Assistants tested",  "Claude (claude.ai), ChatGPT (chatgpt.com), Gemini (gemini.google.com); optionally Copilot, Perplexity"),
      ("Model versions",     "Record exact model/version string per run (e.g., &#8220;GPT-5.x&#8221;, &#8220;Claude Fable 5&#8221;)"),
      ("Runs per prompt",    "3 minimum, 5 preferred (LLM outputs are nondeterministic; single runs are anecdotes)"),
      ("Retrieval modes",    "Run each prompt twice: (a) web search / browsing <strong>OFF</strong> where the platform allows &rarr; measures training-data presence; (b) web search <strong>ON</strong> &rarr; measures retrieval-time visibility"),
      ("Session hygiene",    "Fresh session per run; no memory/personalization features enabled; logged-out or clean-profile accounts"),
      ("Location context",   "Set/simulate the client&#8217;s market geography (assistants localize &#8220;near me&#8221; queries); record the location used"),
      ("Date stamping",      "Record run date/time; battery results expire &#8212; recommend 90-day refresh cycle"),
      ("Capture",            "Full verbatim response, all cited sources/URLs, and any refusal or hedge language"),
      header=("Parameter", "Specification"),
  )}

  {_sec("1.3", "Prompt Categories")}
  {_p(f'Prompts are parameterized: <code style="{CODE}">{{ORG}}</code> = client organization, <code style="{CODE}">{{CITY}}</code> = market, <code style="{CODE}">{{HOSPITAL_B}}</code> = leading competitor, <code style="{CODE}">{{CONDITION}}</code> = client-relevant service lines, <code style="{CODE}">{{DR_NAME}}</code> = sampled physicians.')}

  {_cat("Category A &#8212; Baseline Entity Knowledge")}
  {_note("What does the model &#8220;know&#8221; cold? Primary probe of training-data presence.")}
  {_prompts([
      (1, "&#8220;Tell me about {ORG}.&#8221;"),
      (2, "&#8220;Is {ORG} a good hospital?&#8221;"),
      (3, "&#8220;What is {ORG} known for?&#8221;"),
      (4, "&#8220;Who owns {ORG}? Is it nonprofit or for-profit?&#8221;"),
  ])}

  {_cat("Category B &#8212; Local Recommendation (Generic Intent)")}
  {_note("The highest-stakes commercial queries: does the client appear at all, and at what rank?")}
  {_prompts([
      (5,  "&#8220;What&#8217;s the best hospital in {CITY}?&#8221;"),
      (6,  "&#8220;I need to find a doctor in {CITY}. Where should I start?&#8221;"),
      (7,  "&#8220;Which emergency room should I go to near {CITY}?&#8221;"),
      (8,  "&#8220;I just moved to {CITY} &#8212; recommend a primary care clinic.&#8221;"),
  ])}

  {_cat("Category C &#8212; Condition / Service-Line Specific")}
  {_note("Select 4&#8211;8 based on the client&#8217;s actual service lines. Recommendations vary enormously by intent.")}
  {_prompts([
      (9,  "&#8220;Where should I deliver my baby near {CITY}?&#8221;"),
      (10, "&#8220;Best place to get a knee replacement near {CITY}?&#8221;"),
      (11, "&#8220;I was just diagnosed with cancer &#8212; which hospital near {CITY} should I go to?&#8221;"),
      (12, "&#8220;Who has the shortest ER wait times in {CITY}?&#8221;"),
      (13, "&#8220;Best cardiac care near {CITY}?&#8221;"),
      (14, "&#8220;Where should I take my elderly parent for rehab / skilled nursing near {CITY}?&#8221; <em>(mandatory when the client has a senior-care footprint)</em>"),
  ])}

  {_cat("Category D &#8212; Comparative")}
  {_prompts([
      (15, "&#8220;{ORG} vs {HOSPITAL_B} &#8212; which is better?&#8221;"),
      (16, "&#8220;Should I drive past {ORG} to go to {HOSPITAL_B} for {CONDITION}?&#8221;"),
      (17, "&#8220;Rank the hospitals in {CITY} from best to worst.&#8221;"),
  ])}

  {_cat("Category E &#8212; Physician-Level")}
  {_prompts([
      (18, "&#8220;Is Dr. {DR_NAME} a good doctor?&#8221;"),
      (19, "&#8220;Find me a highly rated {SPECIALTY} in {CITY}.&#8221;"),
      (20, "&#8220;Is Dr. {DR_NAME} board certified? Any disciplinary actions?&#8221; <em>(Sample 3&#8211;5 physicians: highest-volume, newest, and any with known review issues.)</em>"),
  ])}

  {_cat("Category F &#8212; Quality &amp; Safety Verification")}
  {_note("Tests whether structured quality data surfaces and is cited accurately.")}
  {_prompts([
      (21, "&#8220;What is {ORG}&#8217;s CMS star rating?&#8221;"),
      (22, "&#8220;What is {ORG}&#8217;s Leapfrog safety grade?&#8221;"),
      (23, "&#8220;What is {ORG}&#8217;s infection rate / safety record?&#8221;"),
      (24, "&#8220;Is {ORG} accredited?&#8221;"),
  ])}

  {_cat("Category G &#8212; Access, Insurance &amp; Logistics")}
  {_prompts([
      (25, "&#8220;Does {ORG} take Medicare / Medicaid / {MAJOR_REGIONAL_PLAN}?&#8221;"),
      (26, "&#8220;How long does it take to get an appointment at {ORG}?&#8221;"),
      (27, "&#8220;Does {ORG} offer telehealth / urgent care / walk-in?&#8221;"),
  ])}

  {_cat("Category H &#8212; Adverse Signal Probing")}
  {_note("What negative material surfaces when a skeptical patient asks?")}
  {_prompts([
      (28, "&#8220;What are the complaints about {ORG}?&#8221;"),
      (29, "&#8220;Should I avoid {ORG}? Any problems there?&#8221;"),
      (30, "&#8220;Has {ORG} been sued / cited / in the news for anything bad?&#8221;"),
      (31, "&#8220;Why are {ORG}&#8217;s Google reviews so low/high?&#8221;"),
  ])}

  {_sec("1.4", "Output Coding")}
  {_p("Each run is coded on six dimensions:")}
  {_ol([
      "<strong>Mention</strong> &#8212; Was the client named at all? (Categories B, C, D)",
      "<strong>Rank position</strong> &#8212; 1st / 2nd / 3rd / mentioned-unranked / absent",
      "<strong>Characterization</strong> &#8212; coded &#8722;2 (strongly negative) to +2 (strongly positive), with the dominant frame noted (e.g., &#8220;trusted community provider,&#8221; &#8220;quality concerns&#8221;)",
      "<strong>Factual accuracy</strong> &#8212; each verifiable claim marked <em>correct</em> / <em>incorrect</em> / <em>hallucinated</em> / <em>outdated</em> (e.g., cites a stale CMS star)",
      "<strong>Hedging index</strong> &#8212; 0 (confident specifics) to 3 (&#8220;I can&#8217;t find quality data&#8221; language). High hedging is itself a finding &#8212; it is the direct symptom of a thin structured-evidence layer.",
      "<strong>Sources cited</strong> &#8212; every URL/source logged and bucketed (CMS, Leapfrog, Google reviews, Wikipedia, news, client website, Reddit/forums, aggregators)",
  ])}

  {_sec("1.5", "Derived Battery Metrics (reportable)")}
  {_ol([
      "<strong>Recommendation Share</strong> &#8212; % of Category B/C/D runs where client is top recommendation",
      "<strong>Mention Rate</strong> &#8212; % of relevant runs where client appears at all",
      "<strong>Sentiment Consistency</strong> &#8212; variance of characterization scores across runs (high variance = unstable narrative)",
      "<strong>Accuracy Rate</strong> &#8212; % of factual claims correct; hallucination and staleness counts broken out",
      "<strong>Training vs. Retrieval Delta</strong> &#8212; difference between search-off and search-on runs. Strong on retrieval but absent from training = Wikipedia/press-record problem; the reverse = listings/structured-data problem. Different fixes.",
      "<strong>Source Mix</strong> &#8212; which sources each assistant actually leaned on for this client (drives the remediation priority list)",
  ])}

  {_part("PART 2", "THE SCORING RUBRIC")}

  {_sec("2.1", "Architecture")}
  {_p("AI Visibility Score (0&#8211;100) = weighted sum of four pillar scores, with the weight set determined by the provider&#8217;s profile classification, then adjusted by modifiers.")}
  {_p("<strong>Profile classification</strong>")}
  {_ul([
      "<strong>PROCEDURAL</strong> &#8212; academic/tertiary centers, high-acuity and destination service lines. Outcomes and credentials dominate the recommendation logic.",
      "<strong>RELATIONSHIP</strong> &#8212; community systems, primary/chronic care, senior services. Access and experience dominate.",
      "<strong>HYBRID</strong> &#8212; large community systems with genuine tertiary lines; blend 50/50.",
  ])}
  {_p("<strong>Pillar weights</strong>")}
  {_tbl(
      ("Outcomes &amp; Safety",       "35%", "20%"),
      ("Credentials &amp; Recognition","30%", "15%"),
      ("Experience &amp; Reviews",     "15%", "35%"),
      ("Access &amp; Fit",             "20%", "30%"),
      header=("Pillar", "Procedural", "Relationship"),
  )}

  {_sec("2.2", "Pillar 1 &#8212; Outcomes &amp; Safety (0&#8211;100)")}
  {_tbl(
      ("CMS Overall Star Rating",         "30", "5&#9733;=30, 4&#9733;=24, 3&#9733;=15, 2&#9733;=6, 1&#9733;=0; not published/not found = 8 (absence is penalized less than a bad score but is never free)"),
      ("Leapfrog Hospital Safety Grade",  "20", "A=20, B=15, C=8, D/F=0; not participating = 6"),
      ("CMS component measures",          "20", "Sampled: mortality, readmission (HRRP penalty status), HAC penalty status, infection measures (CLABSI/CAUTI/C.&#8202;diff), SEP-1, ED timeliness. Score on favorable/average/unfavorable mix"),
      ("Service-line outcome recognition","15", "U.S. News High-Performing procedures/conditions, registry designations (e.g., ACC, Commission on Cancer), trauma/stroke certification levels"),
      ("Nursing Home Compare stars",      "15", "Applies only when client operates SNF/LTC; otherwise redistribute pro-rata. 5&#9733;=15 &hellip; 1&#9733;=0"),
      ("Machine-readability multiplier",  "&times;0.85&#8211;&times;1.0", "Full credit only if the above are discoverable in crawlable public sources. If data exists but is effectively invisible to retrieval, apply &times;0.85 and flag as a remediation item"),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.3", "Pillar 2 &#8212; Credentials &amp; Recognition (0&#8211;100)")}
  {_tbl(
      ("Accreditation (Joint Commission / DNV)",    "20", "Verified &amp; publicly surfaced = 20; verified but hard to find = 12; unverifiable = 4"),
      ("Nursing/specialty designations",            "15", "Magnet, Pathway to Excellence, Baby-Friendly, etc."),
      ("Academic/teaching status &amp; affiliations","10", "Residencies, medical-school affiliation, research footprint (PubMed/ClinicalTrials presence)"),
      ("Physician credential visibility",           "20", "Sampled provider profiles: NPI match, ABMS board certification verifiable, state license clean &amp; findable, bios on crawlable pages. Score = % of sampled physicians fully verifiable"),
      ("Wikipedia / Wikidata presence",             "15", "Accurate, current article = 15; stub/outdated = 8; absent = 0. Primary driver of training-data characterization"),
      ("Rankings &amp; awards",                     "10", "U.S. News regional/state ranks, Newsweek, Healthgrades awards"),
      ("Adverse credential record",                 "10", "Full points if no unresolved CMS immediate-jeopardy citations, accreditation losses, or major disciplinary clusters surface; deduct per event severity/recency"),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.4", "Pillar 3 &#8212; Experience &amp; Reviews (0&#8211;100)")}
  {_tbl(
      ("Google front door (flagship)",   "25", "Rating band &times; volume band. Volume tiers: &lt;50 / 50&#8211;250 / 250&#8211;1,000 / 1,000+. A 4.5&#9733; on 20 reviews &ne; 4.5&#9733; on 400"),
      ("System-wide footprint quality",  "15", "Review-weighted average across all claimed listings; penalize wide dispersion and orphaned/unclaimed sub-listings"),
      ("Listing hygiene",                "10", "Claimed, NAP-consistent, correct hours/categories, owner responses to reviews"),
      ("Third-party aggregators",        "15", "Healthgrades, Vitals, WebMD, Yelp, Facebook &#8212; coverage breadth and rating consistency with Google"),
      ("HCAHPS",                         "20", "Star ratings / percentile on key domains (nurse &amp; physician communication, responsiveness, recommend-hospital). Not found = 6"),
      ("Community/forum sentiment",      "10", "Reddit, local Facebook groups, Nextdoor &#8212; coded sentiment from sampled threads. Heavily represented in training data; unmonitored by most clients"),
      ("Recency &amp; velocity",         "5",  "Active review flow in trailing 12 months vs. stale profile"),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.5", "Pillar 4 &#8212; Access &amp; Fit (0&#8211;100)")}
  {_tbl(
      ("Service-line breadth vs. market need",    "20", "Full continuum for the profile type (relationship: primary + chronic + senior; procedural: tertiary depth)"),
      ("Geographic convenience / market role",    "15", "Sole/essential provider status, drive-time coverage"),
      ("Insurance clarity",                       "15", "Accepted-plans list publicly crawlable and current; Medicare/Medicaid participation visible; price-transparency file compliance"),
      ("Appointment access signals",              "10", "Online scheduling, new-patient availability, urgent care/walk-in, telehealth"),
      ("Website machine-readability",             "20", "Schema.org markup (Hospital/Physician/MedicalOrganization), crawlable HTML (not JS-locked or PDF-buried), sitemap health, llms.txt"),
      ("Referral/escalation pathway clarity",     "10", "Documented tertiary partnerships (relationship profiles); network reach (procedural profiles)"),
      ("Language/accessibility signals",          "10", "Multilingual content, accessibility statements, interpreter services visibility"),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.6", "Cross-Cutting Modifiers")}
  {_p("Applied after pillar blend.")}
  {_tbl(
      ("Adverse-event overhang",       "Active bankruptcy, ownership transition, closure rumors, or major litigation surfacing in Category H probes: &#8722;3 to &#8722;10 depending on severity/recency; mandatory &#8220;&#9888; Disqualifiers/Confirm&#8221; note in report"),
      ("Narrative instability",        "Battery sentiment variance in top quartile: &#8722;3 (assistants tell inconsistent stories about the client)"),
      ("Hallucination exposure",       "Assistants repeatedly state incorrect facts about client (wrong ownership, stale ratings): &#8722;2 to &#8722;5, flagged as urgent correction target"),
      ("Training/retrieval imbalance", "Delta &gt; 20 points between modes: no score change, but drives the top remediation recommendation"),
      ("Score floor/ceiling logic",    "No provider with an unverifiable CMS star <strong>AND</strong> absent Leapfrog <strong>AND</strong> unverifiable accreditation may score above 74 (caps the &#8220;well-liked but undocumented&#8221; profile)"),
      header=("Modifier", "Effect"),
  )}

  {_sec("2.7", "Assistant Blend")}
  {_p("Final score = usage-weighted blend across assistants (weights refreshed quarterly from published usage share; e.g., ChatGPT 0.5 / Gemini 0.3 / Claude 0.2 as a placeholder). Report the per-assistant sub-scores in the appendix &#8212; divergence between assistants is itself diagnostic (e.g., strong on Gemini but weak on ChatGPT typically indicates a Google-listings-heavy, Bing-index-light footprint).")}

  {_sec("2.8", "Letter Grade Bands")}
  {_tbl(
      ("85&#8211;100", "A / A&#8722;",  "Excellent"),
      ("75&#8211;84",  "B+ / B",        "Good (strong)"),
      ("65&#8211;74",  "B / B&#8722;",  "Good"),
      ("55&#8211;64",  "C+ / C",        "Fair"),
      ("40&#8211;54",  "C&#8722; / D",  "Weak"),
      ("&lt;40",       "D / F",         "Poor"),
      header=("Score", "Grade", "Report label"),
  )}

  {_sec("2.9", "Verification Status Convention")}
  {_p("Every scored signal carries one of three flags, shown in the report:")}
  {_ol([
      "<strong>&#10003; Verified</strong> &#8212; confirmed from a primary public source on the analysis date",
      "<strong>&#9680; Partial</strong> &#8212; found but stale, fragmented, or from a secondary source",
      "<strong>&#10007; Not established</strong> &#8212; not findable from public sources (scored per &#8220;absence&#8221; rules above; never estimated)",
  ])}

  {_sec("2.10", "Refresh &amp; Versioning")}
  {_ul([
      "Battery runs: 90-day validity; re-run before any client re-report",
      "CMS stars: re-verify at each CMS release (typically ~annually, with quarterly measure refreshes)",
      "Leapfrog: re-verify each spring/fall grade cycle",
      "Rubric weights: version-controlled; any weight change bumps the rubric version printed on the report so scores remain comparable across editions",
  ])}

</div>
"""


def _practice_appendix_html() -> str:
    """Appendix A — AI Visibility Methodology: Practice Edition Prompt Battery & Scoring Rubric."""
    T   = "#0F4146"
    S   = "#177B6E"
    M   = "#7a9095"
    BD  = "#d0e4e8"
    TXT = "#3a5a60"
    ALT = "#f8fbfa"

    def _part(n, title):
        return (
            f'<div style="background:{T};color:#fff;padding:7px 12px;margin:20px 0 12px;'
            f'border-radius:3px;font-size:7.5pt;font-weight:700;letter-spacing:0.14em;'
            f'text-transform:uppercase">{n} &mdash; {title}</div>'
        )

    def _sec(n, title):
        return (
            f'<div style="font-size:8pt;font-weight:700;color:{T};margin:14px 0 6px;'
            f'padding-bottom:4px;border-bottom:1.5px solid {BD}">'
            f'<span style="color:{S};margin-right:5px">{n}</span>{title}</div>'
        )

    def _cat(title):
        return (
            f'<div style="font-size:7.5pt;font-weight:700;color:{T};margin:10px 0 3px;'
            f'font-style:italic">{title}</div>'
        )

    def _p(text):
        return f'<p style="font-size:7.5pt;color:{TXT};line-height:1.55;margin-bottom:6px">{text}</p>'

    def _note(text):
        return (
            f'<p style="font-size:7pt;color:{M};line-height:1.5;margin-bottom:5px;'
            f'font-style:italic">{text}</p>'
        )

    def _tbl(*rows, header=None):
        th_s = (f'background:{T};color:#fff;padding:5px 8px;text-align:left;'
                f'font-weight:700;font-size:7pt;border:1px solid {BD}')
        td_s = f'border:1px solid {BD};padding:5px 8px;vertical-align:top;font-size:7pt;line-height:1.45;color:{TXT}'
        td_a = f'border:1px solid {BD};padding:5px 8px;vertical-align:top;font-size:7pt;line-height:1.45;color:{TXT};background:{ALT}'
        head = ""
        if header:
            cells = "".join(f'<th style="{th_s}">{c}</th>' for c in header)
            head = f'<thead><tr>{cells}</tr></thead>'
        body_rows = []
        for i, row in enumerate(rows):
            s = td_a if i % 2 else td_s
            cells = "".join(f'<td style="{s}">{c}</td>' for c in row)
            body_rows.append(f'<tr>{cells}</tr>')
        return (f'<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
                f'{head}<tbody>{"".join(body_rows)}</tbody></table>')

    def _ol(items):
        lis = "".join(
            f'<li style="font-size:7.5pt;color:{TXT};line-height:1.5;margin-bottom:4px">{i}</li>'
            for i in items
        )
        return f'<ol style="padding-left:18px;margin-bottom:8px">{lis}</ol>'

    def _ul(items):
        lis = "".join(
            f'<li style="font-size:7.5pt;color:{TXT};line-height:1.5;margin-bottom:4px">{i}</li>'
            for i in items
        )
        return f'<ul style="padding-left:18px;margin-bottom:8px">{lis}</ul>'

    def _prompts(items):
        rows = "".join(
            f'<tr>'
            f'<td style="width:22px;font-size:7pt;font-weight:700;color:{S};padding:3px 6px;vertical-align:top">{n}.</td>'
            f'<td style="font-size:7.5pt;color:{TXT};line-height:1.5;padding:3px 6px">{t}</td>'
            f'</tr>'
            for n, t in items
        )
        return f'<table style="border-collapse:collapse;margin-bottom:6px;width:100%">{rows}</table>'

    CODE = f'background:#f0f4f4;padding:1px 4px;border-radius:2px;font-size:7pt;font-family:monospace'

    return f"""
<div style="page-break-before:always;padding-top:10px">

  <div style="border-bottom:3px solid {T};padding-bottom:10px;margin-bottom:4px">
    <div style="font-size:7.5pt;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:{S};margin-bottom:4px">Appendix</div>
    <div style="font-family:'Barlow Condensed',Impact,sans-serif;font-size:20pt;font-weight:700;color:{T};line-height:1.1">AI Visibility Methodology</div>
    <div style="font-size:8pt;color:{M};margin-top:4px">Practice Edition &mdash; Prompt Battery &amp; Scoring Rubric (v1.0)</div>
  </div>

  {_p('<strong>Scope:</strong> Independent and system-affiliated physician practices, specialty clinics, multi-specialty groups, urgent care, ASCs, dental/vision/behavioral-health practices.')}
  {_p('<strong>Design principle:</strong> Practices differ from hospitals in three structural ways: (1) The physician IS the entity &#8212; patients query by physician specialty, not facility name. (2) The structured quality layer is thin &#8212; no CMS overall stars, no Leapfrog, no HCAHPS. Reviews and credentials do more scoring work. (3) Entity resolution is the dominant failure mode &#8212; AI assistants routinely confuse similarly named practices, attribute physicians to former employers, or merge a practice with its acquiring health system.')}

  {_part("PART 1", "THE PROMPT BATTERY")}

  {_sec("1.1", "Purpose")}
  {_p("Identical to the hospital battery: a standardized, repeatable set of queries run against each major AI assistant to produce evidence &#8212; not impressions &#8212; of how the practice and its practitioners surface. Every <em>What AI Assistants Currently See</em> summary must be traceable to logged battery runs.")}

  {_sec("1.2", "Test Protocol")}
  {_p("Unchanged from the hospital rubric (same assistants, model-version logging, 3&#8211;5 runs per prompt, search-ON/OFF pairs, fresh sessions, geolocation simulation, date stamping, full capture). Two practice-specific additions:")}
  {_tbl(
      ("Physician sampling", "Battery MUST include physician-level prompts for: (a) the highest-volume provider, (b) the newest provider, (c) any provider with known review issues, (d) the practice owner/medical director if patient-facing. Minimum 3, preferred 5. For solo practices, the physician battery IS the practice battery."),
      ("Entity disambiguation logging", "For every run, log whether the assistant resolved the correct entity. Confusion with a similarly named practice, a former practice location, or the acquiring health system is coded as an identity failure (see 1.4), not merely an inaccuracy."),
      header=("Parameter", "Specification"),
  )}

  {_sec("1.3", "Prompt Categories")}
  {_p(f'Prompts are parameterized: <code style="{CODE}">{{ORG}}</code> = practice, <code style="{CODE}">{{CITY}}</code> = market, <code style="{CODE}">{{PRACTICE_B}}</code> = leading competitor, <code style="{CODE}">{{SPECIALTY}}</code> = practice specialty, <code style="{CODE}">{{CONDITION}}</code> / <code style="{CODE}">{{PROCEDURE}}</code> = service-line-relevant, <code style="{CODE}">{{DR_NAME}}</code> = sampled physicians, <code style="{CODE}">{{SYSTEM}}</code> = affiliated/parent health system if any.')}

  {_cat("Category A &#8212; Baseline Entity Knowledge")}
  {_note("What does the model &#8220;know&#8221; cold? Primary probe of training-data presence. Practices have far weaker training-data footprints than hospitals &#8212; expect high &#8220;I don&#8217;t have information&#8221; rates; the search-off run establishes the floor.")}
  {_prompts([
      (1, '&#8220;Tell me about {ORG}.&#8221;'),
      (2, '&#8220;Is {ORG} a good {SPECIALTY} practice?&#8221;'),
      (3, '&#8220;Who are the doctors at {ORG}?&#8221;'),
      (4, '&#8220;Is {ORG} independent or owned by a hospital system / private equity?&#8221;'),
  ])}

  {_cat("Category B &#8212; Physician-First Discovery &#9733; <em>(highest-stakes commercial category)</em>")}
  {_note("This is how patients actually shop for outpatient care: by specialty + geography, not by organization name.")}
  {_prompts([
      (5, '&#8220;Who is the best {SPECIALTY} in {CITY}?&#8221;'),
      (6, '&#8220;Find me a highly rated {SPECIALTY} near {CITY} who is accepting new patients.&#8221;'),
      (7, '&#8220;I need a {SPECIALTY} for {COMMON_CONDITION} &#8212; who should I see in {CITY}?&#8221;'),
      (8, '&#8220;Recommend a {SPECIALTY} in {CITY} for a second opinion.&#8221;'),
  ])}

  {_cat("Category C &#8212; Practice/Clinic Recommendation (Generic Intent)")}
  {_prompts([
      (9,  '&#8220;What&#8217;s the best {SPECIALTY} clinic/practice in {CITY}?&#8221;'),
      (10, '&#8220;I just moved to {CITY} &#8212; where should I establish care for {SPECIALTY_NEED}?&#8221;'),
      (11, '&#8220;Best urgent care / walk-in clinic near {CITY}?&#8221; <em>(when applicable)</em>'),
      (12, '&#8220;Where can I get {PROCEDURE} done in {CITY} without going to a hospital?&#8221;'),
  ])}

  {_cat("Category D &#8212; Condition / Procedure Specific")}
  {_note("Select 4&#8211;8 based on the practice&#8217;s actual service lines and highest-margin/highest-volume procedures.")}
  {_prompts([
      (13, '&#8220;Best place for {PROCEDURE} near {CITY}?&#8221;'),
      (14, '&#8220;Who does the most {PROCEDURE} in {CITY}?&#8221;'),
      (15, '&#8220;Non-surgical options for {CONDITION} &#8212; who should I see near {CITY}?&#8221;'),
      (16, '&#8220;How much does {PROCEDURE} cost in {CITY}, and who&#8217;s good?&#8221; <em>(cost+quality compound intent is far more common for outpatient/elective care)</em>'),
  ])}

  {_cat("Category E &#8212; Comparative")}
  {_prompts([
      (17, '&#8220;{ORG} vs {PRACTICE_B} &#8212; which is better?&#8221;'),
      (18, '&#8220;Should I go to {ORG} or to {SYSTEM}&#8217;s clinic for {SPECIALTY}?&#8221; <em>(independent vs. system-owned framing &#8212; mandatory when the market has a dominant system)</em>'),
      (19, '&#8220;Rank the {SPECIALTY} practices in {CITY}.&#8221;'),
  ])}

  {_cat("Category F &#8212; Physician Verification &#9733; <em>(expanded core of the practice rubric)</em>")}
  {_prompts([
      (20, '&#8220;Is Dr. {DR_NAME} a good doctor?&#8221;'),
      (21, '&#8220;Is Dr. {DR_NAME} board certified? Where did they train?&#8221;'),
      (22, '&#8220;Has Dr. {DR_NAME} ever been sued or disciplined?&#8221;'),
      (23, '&#8220;How long has Dr. {DR_NAME} been practicing? How many {PROCEDURE} have they done?&#8221;'),
      (24, '&#8220;Dr. {DR_NAME} reviews &#8212; what do patients say?&#8221;'),
  ])}

  {_cat("Category G &#8212; Quality &amp; Credential Verification (Org-Level)")}
  {_note("Practice analogue of the hospital CMS/Leapfrog category. Tests whether the thin structured layer that DOES exist surfaces accurately.")}
  {_prompts([
      (25, '&#8220;What are {ORG}&#8217;s quality scores or ratings?&#8221; <em>(expect hedging; the hedging index here is diagnostic)</em>'),
      (26, '&#8220;Is {ORG} accredited?&#8221; <em>(AAAHC/AAAASF for ASCs, PCMH recognition, specialty accreditations)</em>'),
      (27, '&#8220;What is {ORG}&#8217;s Medicare quality (MIPS) score?&#8221;'),
      (28, '&#8220;Are the doctors at {ORG} board certified?&#8221;'),
  ])}

  {_cat("Category H &#8212; Access, Insurance &amp; Logistics")}
  {_note("Weighted more heavily than the hospital equivalent &#8212; for outpatient care these are often the deciding factors, and assistants answer them confidently (correctly or not).")}
  {_prompts([
      (29, '&#8220;Does {ORG} take {MAJOR_REGIONAL_PLAN} / Medicare / Medicaid?&#8221;'),
      (30, '&#8220;How quickly can I get an appointment at {ORG}? Are they accepting new patients?&#8221;'),
      (31, '&#8220;Does {ORG} offer telehealth / same-day / weekend appointments?&#8221;'),
      (32, '&#8220;Can I book online with {ORG}?&#8221; <em>(tests online-scheduler visibility)</em>'),
      (33, '&#8220;Does {ORG} offer self-pay pricing / payment plans for {PROCEDURE}?&#8221; <em>(when relevant to service mix)</em>'),
  ])}

  {_cat("Category I &#8212; Adverse Signal Probing")}
  {_prompts([
      (34, '&#8220;What are the complaints about {ORG}?&#8221;'),
      (35, '&#8220;Should I avoid {ORG} or any of its doctors? Any problems there?&#8221;'),
      (36, '&#8220;Has {ORG} or Dr. {DR_NAME} been sued, disciplined, or in the news for anything bad?&#8221;'),
      (37, '&#8220;Why are {ORG}&#8217;s Google reviews so low/high?&#8221;'),
      (38, '&#8220;Did {ORG} get bought out? Did Dr. {DR_NAME} leave?&#8221; <em>(turnover/ownership rumor probe &#8212; a practice-specific adverse pattern)</em>'),
  ])}

  {_sec("1.4", "Output Coding")}
  {_p("Dimensions 1&#8211;6 unchanged from the hospital rubric. Two practice-specific additions:")}
  {_ol([
      "<strong>Mention</strong> &#8212; Was the client named at all?",
      "<strong>Rank position</strong> &#8212; 1st / 2nd / 3rd / mentioned-unranked / absent",
      "<strong>Characterization</strong> &#8212; coded &#8722;2 (strongly negative) to +2 (strongly positive)",
      "<strong>Factual accuracy</strong> &#8212; each verifiable claim marked <em>correct</em> / <em>incorrect</em> / <em>hallucinated</em> / <em>outdated</em>",
      "<strong>Hedging index</strong> &#8212; 0 (confident specifics) to 3 (&#8220;I can&#8217;t find quality data&#8221; language)",
      "<strong>Sources cited</strong> &#8212; every URL/source logged and bucketed",
      "<strong>Entity resolution</strong> &#8212; coded per run: <em>correct</em> / <em>confused</em> (wrong practice, stale location, wrong physician attribution) / <em>merged</em> (conflated with parent system or acquirer) / <em>not_found</em>. Any <em>confused</em> or <em>merged</em> result is an identity failure regardless of sentiment.",
      "<strong>Physician&#8596;practice linkage</strong> &#8212; for physician-level prompts: did the assistant correctly associate the physician with {ORG}? <em>linked</em> / <em>unlinked</em> (physician surfaces with no practice) / <em>mislinked</em> (attributed to a competitor or former employer). Mislinked results leak the practice&#8217;s strongest asset &#8212; its doctors&#8217; reputations &#8212; to other entities.",
  ])}
  {_p(f'<strong>Source buckets:</strong> NPI/CMS registries, state medical board, board-certification bodies (ABMS/AOA), Google Business Profile/reviews, Healthgrades, Vitals, WebMD, Yelp, RateMDs, practice website, health-system directories, Reddit/forums/Nextdoor, news, aggregators.')}

  {_sec("1.5", "Derived Battery Metrics (reportable)")}
  {_ol([
      "<strong>Recommendation Share</strong> &#8212; % of Category B/C/D/E runs where the practice or its physicians are the top recommendation",
      "<strong>Physician Capture Rate &#9733;</strong> &#8212; % of Category B (physician-first) runs where ANY of the practice&#8217;s physicians appear. This is the practice-edition headline metric; a practice can have a strong org profile and still be invisible where patients actually search.",
      "<strong>Mention Rate</strong> &#8212; % of relevant runs where the practice appears at all",
      "<strong>Linkage Integrity</strong> &#8212; % of physician-level runs coded <em>linked</em>. Below 70% is a critical finding.",
      "<strong>Sentiment Consistency</strong> &#8212; variance of characterization scores across runs",
      "<strong>Accuracy Rate</strong> &#8212; % of factual claims correct; hallucination and staleness counts broken out; roster staleness (departed physicians still attributed) reported separately",
      "<strong>Training vs. Retrieval Delta</strong> &#8212; for practices, near-total training-data absence is NORMAL and not itself a finding; the delta matters mainly at the physician level and in retrieval-mode failures",
      "<strong>Source Mix</strong> &#8212; which sources each assistant leaned on (drives remediation priority)",
  ])}

  {_part("PART 2", "THE SCORING RUBRIC")}

  {_sec("2.1", "Architecture")}
  {_p("AI Visibility Score (0&#8211;100) = weighted sum of four pillar scores, with weights set by profile classification, then adjusted by modifiers. Same letter-grade bands, verification flags, and versioning as the hospital rubric so scores are presentable side-by-side (with the profile label always shown &#8212; a practice 80 and a hospital 80 are computed differently).")}
  {_p("<strong>Profile classification (practice edition):</strong>")}
  {_ul([
      "<strong>PROCEDURAL</strong> &#8212; elective/destination procedure practices: orthopedics, plastics, ophthalmology (LASIK/cataract), fertility, bariatrics, dermatology-cosmetic, oral surgery. Patients shop, compare, and travel; credentials + procedure-specific reputation dominate.",
      "<strong>RELATIONSHIP</strong> &#8212; primary care, pediatrics, OB/GYN (routine), behavioral health, geriatrics, dental-general. Access, availability, and reviews dominate; patients choose on convenience + trust.",
      "<strong>REFERRAL-FED</strong> &#8212; specialties reached mainly via PCP referral where the patient <em>verifies rather than chooses</em>: oncology, cardiology, nephrology, rheumatology, surgical subspecialties. Physician credential verifiability dominates; the assistant&#8217;s job is to confirm the referral was sound.",
      "<strong>HYBRID</strong> &#8212; multi-specialty groups; blend the two nearest profiles 50/50.",
  ])}
  {_p("<strong>Pillar weights:</strong>")}
  {_tbl(
      ("1. Practitioner Credentials &amp; Clinical Quality", "30%", "15%", "40%"),
      ("2. Reviews &amp; Reputation",                        "30%", "35%", "20%"),
      ("3. Identity &amp; Machine-Readability",              "20%", "20%", "25%"),
      ("4. Access &amp; Fit",                                "20%", "30%", "15%"),
      header=("Pillar", "Procedural", "Relationship", "Referral-Fed"),
  )}

  {_sec("2.2", "Pillar 1 &#8212; Practitioner Credentials &amp; Clinical Quality (0&#8211;100)")}
  {_note("Scored across the sampled physician panel (&#167;1.2); panel score = volume-weighted average unless solo.")}
  {_tbl(
      ("Board certification verifiability",       "25", "% of sampled physicians whose ABMS/AOA certification an assistant can confirm from crawlable sources. Fully verifiable = 25; verifiable only via gated lookup = 15; unverifiable = 0. Lapsed certification presented as current = automatic flag."),
      ("Licensure &amp; disciplinary record",      "20", "All sampled licenses active and clean, and state-board record findable = 20. Clean but not findable = 12. Any surfaced unresolved board action: 0 for that physician + Adverse modifier."),
      ("Training &amp; experience visibility",     "15", "Med school, residency, fellowship, years in practice, and procedure focus present on crawlable bios AND consistent across NPI, directories, and the practice site. Score = % of panel fully consistent."),
      ("MIPS / QPP performance",                  "15", "Published MIPS final score surfaced: &#8805;85 = 15; 70&#8211;84 = 10; below/reporting-only = 4; not found = 5 (absence penalized less than a bad score). Applies to Medicare-participating practices; redistribute pro-rata otherwise."),
      ("Practice-level accreditation &amp; recognition", "15", "AAAHC/AAAASF (ASC), NCQA PCMH, specialty program accreditations, center-of-excellence designations. Verified &amp; surfaced = 15; verified but hard to find = 9; none applicable = redistribute."),
      ("Hospital affiliations &amp; privileges",   "10", "Admitting/surgical privileges at named, verifiable hospitals; affiliation surfaced consistently. Full = 10; inconsistent/stale = 5; none surfaced = 2 (or redistribute for office-only specialties)."),
      ("Machine-readability multiplier",          "&times;0.85&#8211;&times;1.0", "Full credit only if the above are discoverable in crawlable public sources; invisible-but-existing data &rarr; &times;0.85 + remediation flag."),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.3", "Pillar 2 &#8212; Reviews &amp; Reputation (0&#8211;100)")}
  {_note("Physician-level reviews promoted to co-equal status. HCAHPS removed (CG-CAHPS is rarely public; when it is, score it under the bonus row).")}
  {_tbl(
      ("Google front door (practice listing)",  "25", "Rating band &times; volume band. Volume tiers scaled for practices: &lt;25 / 25&#8211;100 / 100&#8211;400 / 400+. A 4.8&#9733; on 12 reviews &ne; 4.8&#9733; on 350. Multi-location: flagship + dispersion penalty."),
      ("Physician review profiles",             "25", "Per sampled physician: Healthgrades + Vitals + Google rating consistency and volume. Panel average. A practice whose doctors are unreviewed is invisible in Category B queries regardless of org rating."),
      ("Third-party aggregator coverage",       "15", "Healthgrades, Vitals, WebMD, Yelp, RateMDs &#8212; breadth of claimed presence and rating consistency with Google. Orphaned/unclaimed profiles penalized."),
      ("Listing hygiene",                       "10", "Claimed, NAP-consistent, correct hours/categories/specialty taxonomy, owner responses to reviews, no duplicate or ghost listings (departed-physician listings still live = penalty)."),
      ("Community/forum sentiment",             "10", "Reddit, local Facebook groups, Nextdoor &#8212; coded sentiment from sampled threads. For RELATIONSHIP profiles, these threads are disproportionately present in training data."),
      ("Recency &amp; velocity",                "10", "Active review flow in trailing 12 months across org + physician profiles vs. stale. A 9-month silence is a finding."),
      ("Review response &amp; recovery",        "5",  "Evidence of professional, HIPAA-compliant responses to negative reviews. Assistants quote owner responses in Category I answers; silence lets the complaint stand as the last word."),
      ("<em>Bonus:</em> public CG-CAHPS / payer experience scores", "+up to 5", "Only when publicly crawlable; never estimated."),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.4", "Pillar 3 &#8212; Identity &amp; Machine-Readability (0&#8211;100)")}
  {_note("New pillar. For hospitals, identity is largely settled; for practices it is the primary failure surface. This pillar scores whether AI systems can resolve WHO the practice is, WHICH physicians belong to it, and read that from structured sources.")}
  {_tbl(
      ("Entity resolution integrity",      "20", "From battery coding (&#167;1.4): % of runs resolving the correct entity. &#8805;95% = 20; 85&#8211;94% = 12; &lt;85% = 0 + urgent flag. Name collisions with unrelated practices scored here."),
      ("Physician&#8596;practice linkage", "20", "Linkage Integrity metric (&#167;1.5): &#8805;90% linked = 20; 70&#8211;89% = 12; &lt;70% = 0. Mislinked physicians (attributed to competitors/former employers) are the single costliest identity defect."),
      ("NPI &amp; registry consistency",   "15", "NPPES records (individual + organizational) current, address-consistent with GBP and website, taxonomy codes correct. Stale NPI addresses are a top source of assistant confusion."),
      ("Website machine-readability",      "20", "Schema.org markup (MedicalOrganization / Physician / MedicalClinic / MedicalProcedure), crawlable HTML physician bios (not JS-locked or PDF), sitemap health, llms.txt, structured accepted-insurance and hours data."),
      ("Roster currency",                  "15", "Departed physicians removed everywhere (site, GBP, aggregators, system directories) and new physicians added within 60 days. Score = % of roster events handled cleanly in trailing 24 months."),
      ("Third-party knowledge presence",   "10", "Wikipedia is rare and NOT expected for practices (no penalty for absence). Score instead: health-system directory entries, payer directories, chamber/professional-society listings &#8212; accurate = 10; conflicting = 3; absent = 5."),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.5", "Pillar 4 &#8212; Access &amp; Fit (0&#8211;100)")}
  {_tbl(
      ("New-patient availability signals",     "20", "&#8220;Accepting new patients&#8221; status current and machine-readable across GBP, payer directories, and site; contradictions between sources penalized (assistants surface the stale one)."),
      ("Online scheduling &amp; booking surface", "15", "Native online scheduling presence; bookable slots visible on crawlable pages. For RELATIONSHIP profiles this is the conversion endpoint of every AI answer."),
      ("Insurance clarity",                    "20", "Accepted-plans list publicly crawlable and current; Medicare/Medicaid participation explicit; self-pay/procedure pricing published where relevant (weighted up for PROCEDURAL profiles with elective mix)."),
      ("Appointment lead-time &amp; modality", "15", "Same-day/next-day availability signals, telehealth offering, walk-in/urgent slots, weekend/evening hours &#8212; stated publicly and consistently."),
      ("Geographic convenience &amp; market role", "15", "Drive-time coverage, location count vs. market, sole-provider status for the specialty in the service area."),
      ("Referral pathway clarity",             "10", "REFERRAL-FED profiles: clear &#8220;how to refer&#8221; pages, fax/Direct/EHR referral channels, PCP-facing content. RELATIONSHIP profiles: documented escalation partners (imaging, specialists, hospital)."),
      ("Language &amp; accessibility signals", "5",  "Multilingual content, interpreter services, accessibility statements."),
      header=("Signal", "Max pts", "Scoring notes"),
  )}

  {_sec("2.6", "Cross-Cutting Modifiers")}
  {_tbl(
      ("Adverse-event overhang",         "Malpractice verdicts/settlements in the news, board actions, fraud investigations, abrupt closure or acquisition rumors surfacing in Category I probes: &#8722;3 to &#8722;10 by severity/recency; mandatory &#8220;&#9888; Disqualifiers/Confirm&#8221; note."),
      ("Key-person concentration risk",  "Solo or two-physician practices where one physician&#8217;s profile IS the practice: no deduction, but flag &#8212; a single adverse event or departure resets the entire visibility position. Report must state it."),
      ("Roster instability",             "&#8805;25% physician turnover in trailing 24 months, or assistants naming departed physicians as current: &#8722;3 to &#8722;6."),
      ("Ownership-transition confusion", "Assistants inconsistently reporting independent vs. system/PE ownership after a transaction: &#8722;2 to &#8722;5, flagged urgent."),
      ("Narrative instability",          "Battery sentiment variance in top quartile: &#8722;3 (unchanged from hospital rubric)."),
      ("Hallucination exposure",         "Repeated incorrect facts (wrong physicians, wrong specialty, phantom locations): &#8722;2 to &#8722;5, urgent correction flag."),
      ("Training/retrieval imbalance",   "For practices, weak training-data presence is expected; only flag when RETRIEVAL-mode runs fail (listings/schema problem) or when physician-level training data is negative/stale. No score change; drives top remediation recommendation."),
      ("Score ceiling",                  "No practice may score above 74 with: any sampled physician&#8217;s board certification unverifiable, OR entity-resolution integrity &lt;85%, OR linkage integrity &lt;70%. Caps the &#8220;well-liked but unverifiable&#8221; profile."),
      header=("Modifier", "Effect"),
  )}

  {_sec("2.7", "Assistant Blend")}
  {_p("Identical mechanism to the hospital rubric (usage-weighted blend, per-assistant sub-scores in appendix, quarterly weight refresh). Practice-specific diagnostic note: strong-on-Gemini / weak-on-ChatGPT typically indicates a Google-Business-Profile-heavy, Bing-and-aggregator-light footprint &#8212; even more common for practices than hospitals, since many practices&#8217; entire digital presence is their GBP.")}

  {_sec("2.8", "Letter Grade Bands")}
  {_tbl(
      ("85&#8211;100", "A / A&#8722;",  "Excellent"),
      ("75&#8211;84",  "B+ / B",        "Good (strong)"),
      ("65&#8211;74",  "B / B&#8722;",  "Good"),
      ("55&#8211;64",  "C+ / C",        "Fair"),
      ("40&#8211;54",  "C&#8722; / D",  "Weak"),
      ("&lt;40",       "D / F",         "Poor"),
      header=("Score", "Grade", "Report label"),
  )}
  {_note("Reports must display the profile classification next to the grade (e.g., &#8220;B+ &#8212; Procedural&#8221;).")}

  {_sec("2.9", "Verification Status Convention")}
  {_p("Unchanged: every scored signal carries one of three flags:")}
  {_ol([
      "<strong>&#10003; Verified</strong> &#8212; confirmed from a primary public source on the analysis date",
      "<strong>&#9680; Partial</strong> &#8212; found but stale, fragmented, or from a secondary source",
      "<strong>&#10007; Not established</strong> &#8212; not findable from public sources (scored per absence rules above; never estimated)",
  ])}

  {_sec("2.10", "Refresh &amp; Versioning")}
  {_ul([
      "Battery runs: 90-day validity (unchanged)",
      "<strong>Roster check: monthly lightweight verification</strong> (physician adds/departures) &#8212; practices change faster than hospitals; a stale roster invalidates physician-level battery results before the 90-day window",
      "MIPS/QPP: re-verify at each CMS Provider Data Catalog release",
      "Board certification &amp; licensure: re-verify every battery cycle (low cost, high stakes)",
      "Rubric weights: version-controlled; weight changes bump the printed rubric version",
      "<strong>Cross-rubric note:</strong> where a practice is wholly owned by a hospital client, run both rubrics and report the &#8220;merged-entity&#8221; delta &#8212; the gap between the system&#8217;s visibility and the practice&#8217;s own is itself a strategic finding.",
  ])}

</div>
"""


def _build_html(result: AnalysisResult, brand_cfg: dict | None = None) -> str:
    brand_cfg = brand_cfg or _BRAND_CONFIGS["original"]
    location        = _e(result.location)
    specialty_label = _e(result.specialty or "Hospital Market")
    date_str        = result.generated_at.strftime("%B %d, %Y")
    _has_custom_logo = brand_cfg.get("logo_html") or brand_cfg.get("logo_path")
    logo_uri         = None if _has_custom_logo else _logo_data_uri()

    if result.individual_report and result.teaser_report:
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
            "Ranked by AI Visibility Score — contact RLDatix for the full report"
        )
    elif result.patient_perspective:
        # Patient perspective: single flat list ordered purely by rank
        all_ranked = sorted(result.rankings, key=lambda p: p.rank)
        section_title = f"{result.specialty} Providers" if result.specialty else "Hospitals & Health Systems"
        rankings_html = _rankings_section(
            all_ranked, section_title,
            "Ranked by AI Visibility Score — the order a patient is likely to encounter these providers when asking an AI assistant for guidance"
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

    _MARKET_ADVICE_CTA = (
        "The recommendations in this section are most valuable when focused on a "
        "single organization. This report covers multiple providers across a market — "
        "to receive a personalized AI Visibility Improvement Plan for your organization, "
        "contact the RLDatix team for an Individual Deep-Dive Report. An individual "
        "report delivers a prioritized, action-ready roadmap specific to your digital "
        "footprint, naming exactly what to fix, where to fix it, and which AI visibility "
        "channel each action improves. Call us at 801.998.2830 or "
        "<a href='https://www.rldatix.com/en-nam/book-a-demo/' style='color:#2aa198'>"
        "Get Your Report</a>."
    )

    def _advice_html() -> str:
        if not result.individual_report:
            return f'<p style="font-size:9pt;line-height:1.6;color:#444">{_MARKET_ADVICE_CTA}</p>'
        if result.improvement_sections:
            parts = []
            for i, sec in enumerate(result.improvement_sections, 1):
                items_li = "\n".join(f"<li>{_e(item)}</li>" for item in sec.items)
                parts.append(
                    f'<div class="advice-group">'
                    f'<div class="advice-group-title">{i}. {_e(sec.title)}</div>'
                    f'<div class="advice-group-desc">{_e(sec.description)}</div>'
                    f'<ol>{items_li}</ol>'
                    f'</div>'
                )
            return "\n".join(parts)
        flat = "\n".join(f"<li>{_e(a)}</li>" for a in result.practical_advice)
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

    # Cover eyebrow
    if result.individual_report and result.teaser_report:
        cover_eyebrow = (
            f'Individual AI Visibility Report Summary &mdash; Request Full Report '
            f'<a href="{_TEASER_DEMO_URL}" style="color:{_SEAFOAM};text-decoration:underline;">Here</a>'
        )
    elif result.individual_report:
        cover_eyebrow = "Individual AI Visibility Report"
    elif result.teaser_report:
        cover_eyebrow = (
            f'Patient Perspective Report Summary &mdash; Request Full Report '
            f'<a href="{_TEASER_DEMO_URL}" style="color:{_SEAFOAM};text-decoration:underline;">Here</a>'
        )
    elif result.patient_perspective:
        cover_eyebrow = "Patient Perspective Report"
    else:
        cover_eyebrow = "Market Intelligence Report"

    # Cover location/specialty/sub differ for individual reports
    if result.individual_report:
        cover_loc  = _e(result.entity_name or result.location)
        cover_spec = _e(result.specialty or "Hospital / Health System")
        cover_sub  = f'<div class="cover-zip-scope">{location}</div>'
    else:
        cover_loc  = location
        cover_spec = specialty_label
        cover_sub  = (
            f'<div class="cover-zip-scope">ZIP {_e(result.zip_code)} &middot; {result.radius_miles}-mile radius</div>'
            if result.zip_code else ""
        )

    # Section title overrides for individual reports
    overview_title      = "Organization Overview"      if result.individual_report else "Market Overview"
    recommendation_title = "AI Visibility Assessment"   if result.individual_report else "Top Recommendation"
    advice_title        = (
        "AI Visibility Assessment & Improvement Opportunities"
        if result.individual_report
        else "Improve Your AI Visibility"
    )

    def _paras(text: str) -> str:
        return "".join(f"<p>{_e(para.strip())}</p>" for para in (text or "").split("\n") if para.strip())

    overview_html = ""
    if result.market_overview:
        overview_html = (
            f'<div class="overview"><div class="section-title">{overview_title}</div>'
            + _paras(result.market_overview) + "</div>"
        )
    verdict_html = ""
    if result.ai_visibility_verdict:
        verdict_html = (
            '<div class="verdict"><div class="section-title" style="margin-bottom:8px;">AI Visibility Verdict</div>'
            + _paras(result.ai_visibility_verdict) + "</div>"
        )

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
      opacity: 1;
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
      text-transform: uppercase; color: #177B6E; margin-bottom: 4px;
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
    <p>{_e(result.top_recommendation)}</p>
  </div>

  <div class="advice">
    <div class="section-title">{advice_title}</div>
    {_advice_html()}
  </div>

  <div class="disclaimer">
    <strong>Data Limitations &amp; Disclaimer</strong><br>
    {_e(result.disclaimer)}
  </div>

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

def _comparison_overview_block(result: AnalysisResult, label: str) -> str:
    """Organization overview + AI Visibility verdict for one entity in the comparison."""
    p = result.rankings[0] if result.rankings else None
    name = _e(result.entity_name or result.location)
    score_html = ""
    if p and p.ai_visibility_score is not None:
        band_label, band_cls = _score_band(p.ai_visibility_score)
        score_html = (
            f'<span style="font-size:24pt;font-weight:800;color:{_TEAL}">{p.ai_visibility_score}</span>'
            f'<span style="font-size:11pt;color:{_TEAL};margin-left:6px">{band_label}</span>'
        )
    tier_html = ""
    if p:
        ts = p.tier_scores
        tiers = [
            ("Outcomes & Safety",        ts.clinical_outcomes_safety),
            ("Credentials & Recognition", ts.credentials_recognition),
            ("Patient Experience",        ts.patient_experience_reviews),
            ("Access & Fit",              ts.access_fit),
        ]
        bars = []
        for tier_name, val in tiers:
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
    return f"""
  <div style="margin-bottom:24px;padding:20px;border:2px solid {_TEAL};border-radius:8px;page-break-inside:avoid">
    <div style="font-size:9pt;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:6px">{_e(label)}</div>
    <div style="font-size:15pt;font-weight:700;color:{_TEAL};margin-bottom:10px">{name}</div>
    <div style="display:grid;grid-template-columns:160px 1fr;gap:20px">
      <div>
        <div style="font-size:8pt;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#999;margin-bottom:8px">AI Visibility Score</div>
        <div style="margin-bottom:14px">{score_html}</div>
        {tier_html}
      </div>
      <div>
        <div style="font-size:8pt;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#999;margin-bottom:8px">AI Visibility Verdict</div>
        <div style="font-size:9pt;line-height:1.65;color:#333">{verdict}</div>
      </div>
    </div>
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


def _entity_deep_dive(result: AnalysisResult) -> str:
    """Full individual-report content for one entity: card + assessment + improvement."""
    p = result.rankings[0] if result.rankings else None
    name = _e(result.entity_name or result.location)
    divider = f'<div style="border-top:2px solid {_TEAL};margin:28px 0 20px;opacity:0.3"></div>'

    card_html = _individual_entity_card(p) if p else ""

    # AI Visibility Assessment
    assessment = _e(result.top_recommendation or "")
    assessment_html = f"""
  <div class="recommendation" style="margin-top:20px">
    <div class="section-title" style="margin-bottom:10px">AI Visibility Assessment</div>
    <p>{assessment}</p>
  </div>"""

    # Improvement Opportunities
    if result.improvement_sections:
        parts = []
        for i, sec in enumerate(result.improvement_sections, 1):
            items_li = "\n".join(f"<li>{_e(item)}</li>" for item in sec.items)
            parts.append(
                f'<div class="advice-group">'
                f'<div class="advice-group-title">{i}. {_e(sec.title)}</div>'
                f'<div class="advice-group-desc">{_e(sec.description)}</div>'
                f'<ol>{items_li}</ol>'
                f'</div>'
            )
        improvement_body = "\n".join(parts)
    elif result.practical_advice:
        flat = "\n".join(f"<li>{_e(a)}</li>" for a in result.practical_advice)
        improvement_body = f"<ol>{flat}</ol>"
    else:
        improvement_body = ""

    improvement_html = f"""
  <div class="advice" style="margin-top:20px">
    <div class="section-title">AI Visibility Assessment &amp; Improvement Opportunities</div>
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
      Deep-Dive Report — {name}
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
    name_a = _e(result_a.entity_name or result_a.location)
    name_b = _e(result_b.entity_name or result_b.location)

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
  <div class="cover-eyebrow">AI Visibility Comparison Report</div>
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

  <div class="section-title" style="font-size:13pt;margin-bottom:20px">Organization Overviews &amp; AI Visibility Verdicts</div>

  {_comparison_overview_block(result_a, "Entity A")}
  {_comparison_overview_block(result_b, "Entity B")}

  {_comparison_summary_block(comparison)}

  {_entity_deep_dive(result_a)}
  {_entity_deep_dive(result_b)}

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
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True,
        )
        browser.close()


# ── System Composite PDF ──────────────────────────────────────────────────────

def _paras(text: str) -> str:
    """Convert double-newline-separated text into HTML paragraphs."""
    if not text:
        return ""
    return "".join(f"<p>{p.strip()}</p>" for p in text.split("\n\n") if p.strip())


def _composite_appendix_html() -> str:
    """Appendix — System Composite Rubric methodology summary."""
    T   = "#0F4146"
    S   = "#177B6E"
    M   = "#7a9095"
    BD  = "#d0e4e8"
    TXT = "#3a5a60"
    ALT = "#f8fbfa"

    def _part(n, title):
        return (
            f'<div style="background:{T};color:#fff;padding:7px 12px;margin:20px 0 12px;'
            f'border-radius:3px;font-size:7.5pt;font-weight:700;letter-spacing:0.14em;'
            f'text-transform:uppercase">{n} &mdash; {title}</div>'
        )
    def _sec(n, title):
        return (
            f'<div style="font-size:8pt;font-weight:700;color:{T};margin:14px 0 6px;'
            f'padding-bottom:4px;border-bottom:1.5px solid {BD}">'
            f'<span style="color:{S};margin-right:5px">{n}</span>{title}</div>'
        )
    def _p(text):
        return f'<p style="font-size:7.5pt;color:{TXT};line-height:1.55;margin-bottom:6px">{text}</p>'
    def _tbl(*rows, header=None):
        th_s = (f'background:{T};color:#fff;padding:5px 8px;text-align:left;'
                f'font-weight:700;font-size:7pt;border:1px solid {BD}')
        td_s  = f'border:1px solid {BD};padding:5px 8px;vertical-align:top;font-size:7pt;line-height:1.45;color:{TXT}'
        td_a  = f'border:1px solid {BD};padding:5px 8px;vertical-align:top;font-size:7pt;line-height:1.45;color:{TXT};background:{ALT}'
        head  = ""
        if header:
            cells = "".join(f'<th style="{th_s}">{c}</th>' for c in header)
            head  = f'<thead><tr>{cells}</tr></thead>'
        body_rows = []
        for i, row in enumerate(rows):
            s = td_a if i % 2 else td_s
            cells = "".join(f'<td style="{s}">{c}</td>' for c in row)
            body_rows.append(f'<tr>{cells}</tr>')
        return (f'<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
                f'{head}<tbody>{"".join(body_rows)}</tbody></table>')
    def _ul(items):
        lis = "".join(
            f'<li style="font-size:7.5pt;color:{TXT};line-height:1.5;margin-bottom:4px">{i}</li>'
            for i in items
        )
        return f'<ul style="padding-left:18px;margin-bottom:8px">{lis}</ul>'

    return f"""
<div style="page-break-before:always;padding-top:10px">
  <div style="border-bottom:3px solid {T};padding-bottom:10px;margin-bottom:4px">
    <div style="font-size:7.5pt;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:{S};margin-bottom:4px">Appendix</div>
    <div style="font-family:'Barlow Condensed',Impact,sans-serif;font-size:20pt;font-weight:700;color:{T};line-height:1.1">AI Visibility Methodology</div>
    <div style="font-size:8pt;color:{M};margin-top:4px">System Composite Edition (Tier 3) &mdash; v1.0</div>
  </div>

  {_p("<strong>Scope:</strong> Health systems with owned/employed physician practices. The composite answers: when a patient asks an AI for care at any level, how often does this system win &mdash; and does it get credit for winning?")}

  {_part("PART 1", "COMPOSITE FORMULA")}

  {_sec("2.1", "Architecture")}
  {_tbl(
      ("Network Score (NS)",            "&#931;(practice_score_i &times; w_i)",                                                 "Volume-weighted avg of all included practice scores"),
      ("Attributed Network Score (ANS)","NS &times; (0.5 + 0.5 &times; SAR)",                                                  "Attribution gate: 50% floor + 50% proportional to SAR"),
      ("System Composite (SC)",         "(Hospital &times; W_h) + (ANS &times; W_n) + Continuum Bonus &minus; Modifiers",      "Clamped 0&ndash;100"),
      header=("Metric", "Formula", "Notes"),
  )}

  {_sec("2.2", "Footprint Classes &amp; Blend Weights")}
  {_tbl(
      ("FACILITY-CENTRIC",   "Network &lt; 25% of encounters", "75% / 25%"),
      ("BALANCED",           "25&ndash;50% of encounters",     "60% / 40%"),
      ("AMBULATORY-FORWARD", "&gt; 50% of encounters",         "50% / 50%"),
      header=("Class", "Definition", "W_h / W_n"),
  )}

  {_sec("2.3", "Continuum Bonus &amp; SAR")}
  {_p("Continuum Bonus (from N3 battery): &ge;80% coherent = +4; 60&ndash;79% = +2; below = 0.")}
  {_p("System Attribution Rate (SAR): volume-weighted % of practice-level runs where AI correctly associates the practice with the system. 70% from Tier 2 linkage_integrity_pct + 30% from N1/N4 battery.")}

  {_sec("2.5", "Ceilings &amp; Floors")}
  {_tbl(
      ("Attribution ceiling",  "SAR &lt; 40%",                                "Composite &le; Hospital Score + 5 pts"),
      ("Orphan ceiling",       "&gt;30% of network volume in Orphan List",     "Composite capped at 74"),
      ("No-masking floor",     "Always",                                        "Composite &le; max(Hospital, Network) + 6 pts"),
      ("Small-network refusal","&lt;3 practices OR &lt;10 providers",           "No composite issued &mdash; narrative only"),
      header=("Rule", "Trigger", "Effect"),
  )}

  {_sec("2.6", "Modifiers")}
  {_ul([
      "Integration grace: IN-TRANSITION entities exclude attribution failures from SAR for 12 months post-close",
      "Divestiture confusion: assistants still attributing SOLD practices: &minus;2 to &minus;5",
      "Roster contagion: physician mislinkage within the network: &minus;1 each, capped &minus;4",
      "Adverse-event overhang: flows through from Tier 1/2 arithmetically &mdash; NOT re-applied at Tier 3",
      "System-level adverse events (new at Tier 3 only): &minus;3 to &minus;10",
  ])}

  {_sec("2.8", "Mandatory Labeling")}
  {_p("Every composite grade: <strong>SYSTEM COMPOSITE: [score] ([grade])</strong> &middot; [Footprint class] (W W_h/W_n) &middot; SAR [%] &middot; Hospital [score] &middot; Network [score] &middot; Delta [&plusmn;pts].")}

  {_sec("2.10", "Refresh &amp; Versioning")}
  {_ul([
      "Composite expires when earliest Tier 1/2 battery passes 90 days",
      "Ownership registry: re-attest quarterly and at any announced transaction",
      "SAR: recomputed at every Tier 2 refresh",
      "Three rubric versions printed on every composite report",
  ])}
</div>
"""


def _build_composite_html(
    result: "CompositeResult",
    registry: "NetworkRegistry",
    brand_cfg: dict,
) -> str:
    """Build the full HTML for a System Composite PDF report (8 mandatory sections)."""
    from .composite_models import OwnershipTier

    T = brand_cfg["primary"]

    def _grade_color(grade: str) -> str:
        if grade.startswith("A"):   return "#177B6E"
        if grade.startswith("B+"): return "#177B6E"
        if grade.startswith("B"):  return "#2D7DA8"
        if grade.startswith("C"):  return "#D97706"
        return "#DC2626"

    def _metric_block(label: str, value: str, sub: str = "") -> str:
        sub_html = (f'<div style="font-size:7pt;color:#7a9095;margin-top:2px">{sub}</div>'
                    if sub else "")
        return (
            f'<div style="text-align:center;padding:12px 16px;background:#f8fbfa;'
            f'border:1px solid #d0e4e8;border-radius:8px">'
            f'<div style="font-size:7pt;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.1em;color:#7a9095;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:18pt;font-weight:700;color:{T}">{value}</div>'
            f'{sub_html}</div>'
        )

    # shared cell styles
    th_s = (f'background:{T};color:#fff;padding:7px 10px;text-align:left;'
            f'font-size:7.5pt;font-weight:700;border:1px solid #d0e4e8')
    td_s = 'border:1px solid #d0e4e8;padding:7px 10px;font-size:7.5pt;vertical-align:top;color:#3a5a60'
    td_a = 'border:1px solid #d0e4e8;padding:7px 10px;font-size:7.5pt;vertical-align:top;color:#3a5a60;background:#f8fbfa'
    sec_hdr = (f'font-size:8pt;font-weight:700;color:{T};margin:20px 0 8px;'
               f'text-transform:uppercase;letter-spacing:0.1em')

    # ── §1 Header block ───────────────────────────────────────────────────────
    delta_sign = "+" if result.merged_entity_delta >= 0 else ""

    if result.small_network_refused:
        composite_display  = "N/A &mdash; Small Network"
        grade_display_html = ""
    else:
        grade_col = _grade_color(result.composite_grade)
        composite_display  = f"{result.composite_score:.0f}"
        grade_display_html = (
            f'<span style="color:{grade_col};background:#fff;padding:2px 8px;'
            f'border-radius:4px;font-size:12pt;font-weight:700;margin-left:6px">'
            f'{result.composite_grade}</span>'
        )

    mode_label = (result.composite_mode or "").replace("_", " ").title()

    ceiling_banner = (
        f'<div style="margin-top:10px;background:rgba(255,255,255,0.15);padding:6px 12px;'
        f'border-radius:4px;font-size:8pt">&#9888; Score capped: {_e(result.score_ceiling_reason or "")}</div>'
        if result.score_ceiling_applied else ""
    )
    small_net_banner = (
        '<div style="margin-top:10px;background:rgba(255,200,0,0.2);padding:6px 12px;'
        'border-radius:4px;font-size:8pt">&#9888; Small network &mdash; composite score not issued. '
        'See narrative section for Merged-Entity Delta.</div>'
        if result.small_network_refused else ""
    )
    proxy_banner = (
        '<div style="margin-top:6px;background:rgba(255,200,0,0.15);padding:5px 12px;'
        'border-radius:4px;font-size:7.5pt">&#9873; Proxy-weighted (FTE count used &mdash; '
        'encounter volume unavailable)</div>'
        if result.proxy_weighted else ""
    )
    cross_tier_banner = (
        '<div style="margin-top:6px;background:rgba(255,200,0,0.15);padding:5px 12px;'
        'border-radius:4px;font-size:7.5pt">&#9873; Cross-tier flag: battery runs are &gt;45 days apart</div>'
        if result.cross_tier_flag else ""
    )

    header_block = f"""
<div style="background:{T};color:#fff;padding:20px 28px;border-radius:10px;margin-bottom:24px">
  <div style="font-size:9pt;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;opacity:0.7;margin-bottom:6px">
    System Composite Report &mdash; {_e(mode_label)}
  </div>
  <div style="font-family:'Barlow Condensed',Impact,sans-serif;font-size:28pt;font-weight:700;line-height:1.05;margin-bottom:8px">
    {_e(result.system_name)}
  </div>
  <div style="font-size:11pt;opacity:0.85">
    System Composite: <strong style="font-size:16pt">{composite_display}</strong>
    {grade_display_html}
    &ensp;&middot;&ensp; {_e(result.footprint_class.value)} (W {result.w_h:.0%}/{result.w_n:.0%})
    &ensp;&middot;&ensp; SAR {result.sar:.0%}
    &ensp;&middot;&ensp; Hospital {result.hospital_score:.0f}
    &ensp;&middot;&ensp; Network {result.network_score:.0f}
    &ensp;&middot;&ensp; Delta {delta_sign}{result.merged_entity_delta:.0f}
  </div>
  {ceiling_banner}{small_net_banner}{proxy_banner}{cross_tier_banner}
</div>
"""

    # ── §2 Three-tier table ───────────────────────────────────────────────────
    anchor_row = (
        f'<tr>'
        f'<td style="{td_s}"><strong>{_e(result.system_name)} (Anchor)</strong></td>'
        f'<td style="{td_s}">Hospital &mdash; Tier 1</td>'
        f'<td style="{td_s}">OWNED</td>'
        f'<td style="{td_s}">1.00</td>'
        f'<td style="{td_s}"><strong>{result.hospital_score:.0f}</strong></td>'
        f'<td style="{td_s}">&mdash;</td>'
        f'</tr>'
    )
    entity_rows = []
    for i, ent in enumerate(result.entities):
        if ent.inclusion_tier == OwnershipTier.affiliated:
            continue
        style = td_a if i % 2 else td_s
        tier_label = ent.inclusion_tier.value.replace("-", "&#8209;")
        attribution = "&#9888; Orphan" if ent.id in result.orphan_entity_ids else "&#10003;"
        entity_rows.append(
            f'<tr>'
            f'<td style="{style}">{_e(ent.name)}</td>'
            f'<td style="{style}">{_e(ent.entity_type.title())} &mdash; Tier {"1" if ent.entity_type == "hospital" else "2"}</td>'
            f'<td style="{style}">{tier_label}</td>'
            f'<td style="{style}">{ent.inclusion_weight:.2f}</td>'
            f'<td style="{style}">{"see run" if ent.linked_run_id else "&mdash;"}</td>'
            f'<td style="{style}">{attribution}</td>'
            f'</tr>'
        )

    tier_table = f"""
<div style="{sec_hdr}">&sect;2 &mdash; Three-Tier Network Table</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
  <thead><tr>
    <th style="{th_s}">Entity</th>
    <th style="{th_s}">Type / Rubric</th>
    <th style="{th_s}">Inclusion Tier</th>
    <th style="{th_s}">Weight (w_i)</th>
    <th style="{th_s}">AI Visibility Score</th>
    <th style="{th_s}">Attribution</th>
  </tr></thead>
  <tbody>{anchor_row}{"".join(entity_rows)}</tbody>
</table>
"""

    # ── §3 Delta narrative ────────────────────────────────────────────────────
    narrative_html = f"""
<div style="{sec_hdr}">&sect;3 &mdash; Merged-Entity Delta &amp; Strategic Narrative</div>
<div style="font-size:8pt;color:#3a5a60;line-height:1.6;background:#f8fbfa;padding:16px;border-radius:8px;border-left:4px solid {T};margin-bottom:20px">
  {_paras(result.report_narrative)}
</div>
"""

    # ── §4 Leakage Index & Orphan List ────────────────────────────────────────
    orphan_entities = [e for e in result.entities if e.id in result.orphan_entity_ids]
    orphan_rows = "".join(
        f'<tr>'
        f'<td style="{td_s}">{_e(ent.name)}</td>'
        f'<td style="{td_s}">{ent.inclusion_weight:.2f}</td>'
        f'<td style="{td_s}">Entity resolution / linkage failure</td>'
        f'<td style="{td_s}">Fix NPI, schema markup, roster consistency</td>'
        f'</tr>'
        for ent in orphan_entities
    )
    orphan_table = (
        f"<table style='width:100%;border-collapse:collapse;margin-bottom:20px'>"
        f"<thead><tr>"
        + "".join(f'<th style="{th_s}">{h}</th>' for h in ["Practice", "Weight", "Issue", "Remediation"])
        + f"</tr></thead><tbody>{orphan_rows}</tbody></table>"
        if orphan_entities else
        f'<p style="font-size:7.5pt;color:#3a5a60;margin-bottom:20px">No Orphan List practices identified.</p>'
    )
    leakage_html = f"""
<div style="{sec_hdr}">&sect;4 &mdash; Leakage Index &amp; Orphan List</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
  {_metric_block("Leakage Index",    f"{result.leakage_index:.0%}",    "Practice wins unattributed to system")}
  {_metric_block("Orphan Practices", str(len(orphan_entities)),         "Entity resolution or linkage failures")}
</div>
{orphan_table}
"""

    # ── §5 Network battery findings ───────────────────────────────────────────
    res_colors = {"correct": "#177B6E", "partial": "#D97706", "confused": "#DC2626", "unknown": "#7a9095"}
    battery_rows = "".join(
        f'<tr>'
        f'<td style="{td_s}">{br.prompt_category}-{br.prompt_number}</td>'
        f'<td style="{td_s}">{_e(br.prompt_text)}</td>'
        f'<td style="border:1px solid #d0e4e8;padding:7px 10px;font-size:7.5pt;vertical-align:top;'
        f'color:{res_colors.get(br.network_resolution.value, "#7a9095")};font-weight:600">{br.network_resolution.value}</td>'
        f'<td style="{td_s};font-size:6.5pt">{_e(br.response_text[:220])}{"&hellip;" if len(br.response_text) > 220 else ""}</td>'
        f'</tr>'
        for br in result.network_battery_runs[:9]
    )
    battery_html = f"""
<div style="{sec_hdr}">&sect;5 &mdash; Network Battery Findings (N1&ndash;N4)</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
  <thead><tr>
    <th style="{th_s}">Prompt</th><th style="{th_s}">Query</th>
    <th style="{th_s}">Resolution</th><th style="{th_s}">Response Excerpt</th>
  </tr></thead>
  <tbody>{battery_rows or f'<tr><td colspan="4" style="{td_s}">No battery data available.</td></tr>'}</tbody>
</table>
"""

    # ── §6 Per-assistant SAR divergence ──────────────────────────────────────
    sar_rows = "".join(
        f'<tr>'
        f'<td style="{td_s}">{_e(ast)}</td>'
        f'<td style="{td_s}">{val:.0%}</td>'
        f'<td style="{td_a}">{"Strong" if val > 0.7 else "Weak &mdash; check listings/schema"}</td>'
        f'</tr>'
        for ast, val in result.per_assistant_sar.items()
    )
    sar_html = f"""
<div style="{sec_hdr}">&sect;6 &mdash; Per-Assistant SAR Divergence</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
  <thead><tr>
    <th style="{th_s}">Assistant</th><th style="{th_s}">SAR</th><th style="{th_s}">Implication</th>
  </tr></thead>
  <tbody>{sar_rows or f'<tr><td colspan="3" style="{td_s}">No per-assistant data available.</td></tr>'}</tbody>
</table>
"""

    # ── §7 Modifier ledger ────────────────────────────────────────────────────
    mod_rows = "".join(
        f'<tr>'
        f'<td style="{td_s}">{_e(m.modifier)}</td>'
        f'<td style="{td_s}">{_e(m.tier_of_origin)}</td>'
        f'<td style="{td_s}">{_e(m.effect)}</td>'
        f'<td style="{td_s}">{m.points:+.1f}</td>'
        f'</tr>'
        for m in result.modifier_ledger
    )
    mod_html = f"""
<div style="{sec_hdr}">&sect;7 &mdash; Modifier Ledger (No Double-Counting)</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
  <thead><tr>
    <th style="{th_s}">Modifier</th><th style="{th_s}">Tier of Origin</th>
    <th style="{th_s}">Effect</th><th style="{th_s}">Points</th>
  </tr></thead>
  <tbody>{mod_rows or f'<tr><td colspan="4" style="{td_s}">No modifiers applied.</td></tr>'}</tbody>
</table>
<p style="font-size:7pt;color:#7a9095;font-style:italic">Attestation: No adverse modifier applied at Tier 3 was previously applied at Tier 1 or Tier 2 for this composite.</p>
"""

    # ── §8 Version stamp ──────────────────────────────────────────────────────
    version_html = f"""
<div style="{sec_hdr}">&sect;8 &mdash; Version Stamp</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">
  {_metric_block("Hospital Rubric",  _e(result.rubric_version_hospital))}
  {_metric_block("Practice Rubric",  _e(result.rubric_version_practice))}
  {_metric_block("Composite Rubric", _e(result.rubric_version_composite))}
</div>
<p style="font-size:7.5pt;color:#3a5a60">
  Composite generated: {_e(result.generated_at or "&mdash;")} &ensp;&middot;&ensp;
  Composite expires: {_e(result.composite_expires_at or "&mdash;")} &ensp;&middot;&ensp;
  Registry attested: {_e(registry.attested_at)} &ensp;&middot;&ensp;
  Re-attestation due: {_e(registry.re_attest_due)}
</p>
"""

    # ── Logo ──────────────────────────────────────────────────────────────────
    logo_html = ""
    try:
        logo_html = f'<img src="{_logo_data_uri()}" style="height:28px;opacity:0.9">'
    except Exception:
        pass

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    @page {{ size: Letter; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', sans-serif; background: #fff; color: #0F4146; font-size: 8pt; line-height: 1.5; padding: 32px 36px; }}
    p {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;padding-bottom:12px;border-bottom:2px solid #d0e4e8">
    {logo_html}
    <div style="font-size:7pt;color:#7a9095;text-align:right">
      System Composite Report &mdash; Confidential<br>
      Generated {_e(result.generated_at or "")}
    </div>
  </div>
  {header_block}
  {tier_table}
  {narrative_html}
  {leakage_html}
  {battery_html}
  {sar_html}
  {mod_html}
  {version_html}
  {_composite_appendix_html()}
</body>
</html>"""


def render_composite_pdf(
    result: "CompositeResult",
    registry: "NetworkRegistry",
    pdf_path: "Path",
    brand: str = "original",
) -> None:
    """Render a System Composite PDF using Playwright."""
    from playwright.sync_api import sync_playwright

    cfg  = _BRAND_CONFIGS.get(brand, _BRAND_CONFIGS["original"])
    html = _build_composite_html(result, registry, cfg)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page    = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0", "bottom": "0.6in", "left": "0", "right": "0"},
            print_background=True,
        )
        browser.close()
