from __future__ import annotations

import base64
import html as _html_lib
from pathlib import Path

from .models import AffiliationType, AnalysisResult, RankedProvider, SizeCategory
from .scoring import TIER_LABELS

# RLDatix brand palette
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


def render_pdf(result: AnalysisResult, pdf_path: Path) -> None:
    """Render a structured AnalysisResult to a branded PDF using Playwright."""
    from playwright.sync_api import sync_playwright

    html = _build_html(result)
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
    """'What AI assistants currently see' callout block."""
    if not p.ai_says:
        return ""
    return f"""
    <div class="ai-says">
      <div class="ai-says-label">What AI Assistants Currently See</div>
      <div class="ai-says-source">AI Summary &mdash; Claude &middot; ChatGPT &middot; Gemini</div>
      <div class="ai-says-text">{_e(p.ai_says)}</div>
      <div class="ai-says-footnote">This report analyzes the public signals AI assistants use \
to understand, evaluate, and recommend healthcare providers to patients. The summary above \
reflects what those systems currently surface when asked about this organization.</div>
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
    labels = TIER_LABELS.get(p.weighting_profile or "procedural", TIER_LABELS["procedural"])
    ts = p.tier_scores
    score = p.ai_visibility_score
    score_txt = str(score) if score is not None else "—"
    band, band_cls = _score_band(score)
    band_html = f'<span class="score-band {band_cls}">{band}</span>' if band else ""
    profile = p.weighting_profile or "procedural"
    profile_label = "Procedural" if profile == "procedural" else "Relationship"
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
      </div>
      <div class="tier-bars">{rows}</div>
    </div>"""


def _google_stat(p: RankedProvider) -> str:
    """Front door (verified) + footprint + third-party aggregate — the wedge."""
    fd = p.google_footprint.front_door
    if fd.verified and fd.rating is not None:
        recency = f" · {_e(fd.recency)}" if fd.recency else ""
        front = f"Google front door: <strong>{fd.rating:.1f}&#9733; · {fd.count or 0} reviews</strong>{recency}"
    else:
        front = f'Google front door: <strong>not verified</strong> <span class="google-gap">— {_e(fd.reason or "no rated listing")}</span>'

    fp = p.google_footprint
    footprint = _e(fp.rating_range or fp.listings_estimate or fp.consistency) or "single listing"
    consistency = f" · {_e(fp.consistency)}" if fp.consistency and (fp.rating_range or fp.listings_estimate) else ""

    sa = fp.system_aggregate
    system_line = ""
    if sa.available:
        conf = "registry-enumerated" if sa.confidence == "registry" else "sampled"
        loc = f"{sa.location_count}{'+' if sa.capped else ''}"
        system_line = (
            f"System-wide: <strong>{sa.rating:.1f}&#9733; · {sa.total_reviews:,} reviews "
            f"across {loc} locations</strong> (review-count-weighted, {conf})<br>"
        )

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
        {front}<br>
        {system_line}Footprint: {footprint}{consistency}<br>
        Third-Party Aggregate <span style="font-size:6.5pt;color:#7a9095">(Healthgrades, Vitals, WebMD)</span>: <strong>{agg}</strong>{gap}
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


def _outcomes_safety_block(p: RankedProvider) -> str:
    """Leapfrog + CMS quality row — always renders, making non-participation explicit."""
    grade = (p.leapfrog_grade or "").strip()
    first = grade[0].upper() if grade else ""

    if first in "ABCDF":
        css_cls = f"qs-badge qs-leapfrog-{first}"
        leapfrog_cell = f'<span class="{css_cls}">{first}</span>'
    elif grade.lower() in ("not rated", "not_rated"):
        leapfrog_cell = '<span class="os-absent">Not rated in current survey cycle</span>'
    else:
        leapfrog_cell = '<span class="os-absent">Not currently participating in Leapfrog survey</span>'

    if p.cms_star_rating and 1 <= p.cms_star_rating <= 5:
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
    weaknesses_html = "".join(f"<li>{_e(w)}</li>" for w in p.notable_weaknesses)
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
            <div class="trait-label weaknesses-label">Areas to Note</div>
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
    weaknesses_html = "".join(f"<li>{_e(w)}</li>" for w in p.notable_weaknesses)
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
            <div class="trait-label weaknesses-label">Areas to Note</div>
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
    weaknesses_html = "".join(f"<li>{_e(w)}</li>" for w in p.notable_weaknesses)
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
        <div class="teaser-blur-wrapper">
          <div class="teaser-blur-content">
            {_ai_says_block(p)}
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
                <div class="trait-label weaknesses-label">Areas to Note</div>
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
    weaknesses_html = "".join(f"<li>{_e(w)}</li>" for w in p.notable_weaknesses)
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
        <div class="teaser-blur-wrapper">
          <div class="teaser-blur-content">
            {_ai_says_block(p)}
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
                <div class="trait-label weaknesses-label">Areas to Note</div>
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


def _build_html(result: AnalysisResult) -> str:
    location        = _e(result.location)
    specialty_label = _e(result.specialty or "Hospital Market")
    date_str        = result.generated_at.strftime("%B %d, %Y")
    logo_uri        = _logo_data_uri()

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

    advice_items   = "\n".join(f"<li>{_e(a)}</li>" for a in result.practical_advice)
    logo_tag       = f'<img class="cover-logo" src="{logo_uri}" alt="RLDatix">' if logo_uri else ""

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
    advice_title        = "Key Takeaways"              if result.individual_report else "Practical Advice for Patients"

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

    return f"""<!DOCTYPE html>
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
    .advice ol {{ padding-left: 18px; }}
    .advice li {{
      font-size: 8.5pt;
      margin-bottom: 6px;
      color: {_TEAL};
      line-height: 1.5;
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
    <ol>{advice_items}</ol>
  </div>

  <div class="disclaimer">
    <strong>Data Limitations &amp; Disclaimer</strong><br>
    {_e(result.disclaimer)}
  </div>

</div>
</body>
</html>"""
