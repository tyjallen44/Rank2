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


def _tier_row(label: str, value: int | None) -> str:
    width = value if isinstance(value, int) else 0
    val_txt = str(value) if isinstance(value, int) else "—"
    return (
        f'<div class="tier-row"><span class="tier-name">{_e(label)}</span>'
        f'<span class="tier-track"><span class="tier-fill" style="width:{width}%"></span></span>'
        f'<span class="tier-val">{val_txt}</span></div>'
    )


def _aivs_block(p: RankedProvider) -> str:
    """AI Visibility score + the four tier bars (the headline of each card)."""
    labels = TIER_LABELS.get(p.weighting_profile or "procedural", TIER_LABELS["procedural"])
    ts = p.tier_scores
    score_txt = str(p.ai_visibility_score) if p.ai_visibility_score is not None else "—"
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
        <div class="aivs-score">{score_txt}<span class="out">/100</span></div>
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
    if tpa.rating is not None:
        agg = f"{tpa.rating:.1f} avg"
    else:
        agg = _e(tpa.note) or "limited data"
    gap = f' <span class="google-gap">{_e(fp.gap_note)}</span>' if fp.gap_note else ""

    return f"""
    <div class="google-stat">
      {front}<br>
      {system_line}Footprint: {footprint}{consistency}<br>
      Third-Party Aggregate <span style="font-size:6.5pt;color:#7a9095">(Healthgrades, Vitals, WebMD)</span>: <strong>{agg}</strong>{gap}
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

    locations_html = ""
    if p.consolidated_locations:
        loc_parts = []
        for loc in p.consolidated_locations:
            rating_span = (
                f'&thinsp;—&thinsp;<span class="loc-rating">{_e(loc.overall_rating)}</span>'
                if loc.overall_rating else ""
            )
            loc_parts.append(f'<li><span class="loc-name">{_e(loc.name)}</span>{rating_span}</li>')
        loc_items = "".join(loc_parts)
        locations_html = f'<div class="locations-block"><div class="locations-label">Includes locations:</div><ul class="locations-list">{loc_items}</ul></div>'

    return f"""
    <div class="card">
      <div class="card-rank" style="background:{bg}; color:{text_color}">
        <span class="rank-num">{display_rank}</span>
      </div>
      <div class="card-body">
        <div class="card-top">
          <h3 class="provider-name">{_e(p.name)}</h3>
          {f'<span class="surgeon-pill">{_e(_physician_label(p.physician_count))}</span>' if p.physician_count and p.physician_count.lower() not in ("unknown", "") else ""}
          <span class="rating-pill">{_e(p.overall_rating)}</span>
        </div>
        {f'<div class="provider-url"><a href="{_e(p.website_url)}">{_e(p.website_url)}</a></div>' if p.website_url else ""}
        {_aivs_block(p)}
        {_google_stat(p)}
        {disq_html}
        {locations_html}
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
    physician_html = (
        f'<span class="surgeon-pill">{_e(_physician_label(p.physician_count))}</span>'
        if p.physician_count and p.physician_count.lower() not in ("unknown", "") else ""
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
          <span class="rating-pill">{_e(p.overall_rating)}</span>
        </div>
        {_aivs_block(p)}
        <div class="teaser-blur-wrapper">
          <div class="teaser-blur-content">
            {_google_stat(p)}
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

    if result.teaser_report:
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
    if result.teaser_report:
        cover_eyebrow = (
            f'Patient Perspective Report Summary &mdash; Request Full Report '
            f'<a href="{_TEASER_DEMO_URL}" style="color:{_SEAFOAM};text-decoration:underline;">Here</a>'
        )
    elif result.patient_perspective:
        cover_eyebrow = "Patient Perspective Report"
    else:
        cover_eyebrow = "Market Intelligence Report"
    zip_scope_html = (
        f'<div class="cover-zip-scope">ZIP {_e(result.zip_code)} &middot; {result.radius_miles}-mile radius</div>'
        if result.zip_code else ""
    )

    def _paras(text: str) -> str:
        return "".join(f"<p>{_e(para.strip())}</p>" for para in (text or "").split("\n") if para.strip())

    overview_html = ""
    if result.market_overview:
        overview_html = (
            '<div class="overview"><div class="section-title">Market Overview</div>'
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
  <div class="cover-location">{location}</div>
  <div class="cover-specialty">{specialty_label}</div>
  {zip_scope_html}
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
    <div class="section-title" style="margin-bottom:10px;">Top Recommendation</div>
    <p>{_e(result.top_recommendation)}</p>
  </div>

  <div class="advice">
    <div class="section-title">Practical Advice for Patients</div>
    <ol>{advice_items}</ol>
  </div>

  <div class="disclaimer">
    <strong>Data Limitations &amp; Disclaimer</strong><br>
    {_e(result.disclaimer)}
  </div>

</div>
</body>
</html>"""
