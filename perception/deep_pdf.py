"""Deep Diagnostic PDF — the Hospital Network report's layout applied to one entity
(hospital, specialty practice or hospital service line).

Reading order, one job per page: cover with the score → Executive Summary (headline +
bullets) → AI Reputation (the four pillar bars of whichever rubric scored the entity,
Score Evidence, "What AI assistants currently see", the website-access alert) → What to
Do First → Locations (one merged table: confirmed locations + Google profile checks +
composite ratings) → Physicians (one merged table: NPI/linkage/certification + composite
ratings) → What AI assistants actually said → Evidence behind the score → the roadmap or
the embedded Content Report → sources + methodology.

The pillars are NOT changed here: hospitals keep the hospital rubric, practices and
service lines keep the practice rubric; this module only lays the result out.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import AnalysisResult, RankedProvider
from .pdf import (
    _e, _strip_md, _vflag, _tier_labels, _quartile_label, _score_bar_color,
    _ai_access_alert_html, _assessment_body_html, _first_moves_title, _spotcheck_section,
    _sources_box_html, _methodology_box_html, _content_keys_section, _outcomes_safety_weaknesses,
    _outcomes_safety_block, _quality_signals_block, _logo_data_uri,
    _TEASER_PHONE, _TEASER_DEMO_URL, _GREEN_OK, _RED_BAD, _TEAL, _PALE_GREEN, _SEAFOAM,
)
from .network_pdf import _network_css, _structured_html
from .scoring import grade_from_score, PRACTICE_PROFILE_DISPLAY
from .strings import BLUR_CTA_INDIVIDUAL, SECTION_ASSESSMENT


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _target(result: AnalysisResult) -> Optional[RankedProvider]:
    rk = list(result.rankings or [])
    if not rk:
        return None
    return next((p for p in rk if getattr(p, "is_target", False)), rk[0])


def _display_name(result: AnalysisResult) -> tuple[str, str]:
    """(name, address-part) — practice names that embed a street address are split."""
    raw = result.report_title or result.entity_name or result.location or ""
    m = re.search(r"\s+\d+\s", raw)
    if result.entity_type == "practice" and m:
        return raw[:m.start()].strip(), raw[m.start():].strip()
    return raw, ""


def _edition(result: AnalysisResult) -> dict:
    et = (result.entity_type or "hospital")
    if et == "service_line" or (et == "practice" and result.service_line):
        return {"kind": "service_line", "label": "Hospital Service Line", "rubric": "practice",
                "rubric_label": "Specialty Practice rubric"}
    if et == "practice":
        return {"kind": "practice", "label": "Specialty Practice", "rubric": "practice",
                "rubric_label": "Specialty Practice rubric"}
    return {"kind": "hospital", "label": "Hospital / Health System", "rubric": "hospital",
            "rubric_label": "Hospital rubric"}


def _pillars(p: Optional[RankedProvider], result: AnalysisResult) -> list[tuple[str, Optional[int]]]:
    profile = (p.weighting_profile if p else None) or result.weighting_profile or "procedural"
    labels = _tier_labels(profile)
    ts = p.tier_scores if p else None
    get = lambda k: getattr(ts, k, None) if ts is not None else None
    return [
        (labels["clinical_outcomes_safety"], get("clinical_outcomes_safety")),
        (labels["credentials_recognition"], get("credentials_recognition")),
        (labels["patient_experience_reviews"], get("patient_experience_reviews")),
        (labels["access_fit"], get("access_fit")),
    ]


def _profile_label(p: Optional[RankedProvider], result: AnalysisResult) -> str:
    profile = (p.weighting_profile if p else None) or result.weighting_profile or "procedural"
    if profile.startswith("practice_"):
        return PRACTICE_PROFILE_DISPLAY.get(profile, "Procedural")
    return "Relationship" if profile == "relationship" else "Procedural"


def _confidence(result: AnalysisResult) -> Optional[dict]:
    try:
        from .confidence import score_confidence
        return score_confidence(result)
    except Exception:
        return None


def _norm_name(s: str) -> str:
    s = re.sub(r"\((anchor listing|analyzed|anchor)\)", " ", (s or "").lower())
    s = re.sub(r"^(dr\.?|md|do|pa-c|np)\s+", "", s)
    s = re.sub(r",?\s*(md|do|pa-c|np|phd)\b\.?", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", _strip_md(text or "").strip()) if s.strip()]


def _bars_html(pillars: list[tuple[str, Optional[int]]]) -> str:
    out = ""
    for label, score in pillars:
        pct = score if score is not None else 0
        num = str(score) if score is not None else "—"
        color = _score_bar_color(score)
        out += (f'<div class="score-bar-row"><div class="score-bar-label">{_e(label)}</div>'
                f'<div class="score-bar-track"><div class="score-bar-fill" style="width:{pct}%;background:{color}"></div></div>'
                f'<div class="score-bar-num" style="color:{color}">{num}</div></div>')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def _cover(result: AnalysisResult, p: Optional[RankedProvider], cfg: dict, logo_html: str, ed: dict) -> str:
    name, addr = _display_name(result)
    accent = cfg["accent"]
    score = p.ai_visibility_score if p else None
    score_str = str(score) if score is not None else "—"
    quartile, q_band = grade_from_score(score)
    q_color = {"Q1": "#6fcf97", "Q2": "#56b8d9", "Q3": "#f2994a", "Q4": "#eb5757"}.get(quartile, "rgba(255,255,255,0.7)")
    generated = result.generated_at.strftime("%Y-%m-%d")
    sub_bits = [x for x in [result.specialty or "", addr, result.location or ""] if x]
    subtitle = " &middot; ".join(_e(x) for x in sub_bits) or _e(ed["label"])
    pillars = _pillars(p, result)
    pillar_names = " &mdash; " + ", ".join(_e(l) for l, _ in pillars[:3]) + f", and {_e(pillars[3][0])} &mdash;"

    # Stat tiles
    tiles: list[tuple[str, str]] = []
    locs = _location_rows(result, p)
    if ed["rubric"] == "practice":
        tiles.append((str(max(1, len(locs))), "Locations<br>Assessed"))
        pf = result.physician_facts or {}
        n_phys = pf.get("checked") or len(_physician_rows(result))
        if n_phys:
            tiles.append((str(n_phys), "Physicians<br>Checked"))
        else:
            tiles.append((_front_door_rating(p) or "—", "Google<br>Rating"))
    else:
        tiles.append((_front_door_rating(p) or "—", "Google<br>Rating"))
        if p and p.cms_star_rating:
            tiles.append((f"{p.cms_star_rating}/5", "CMS<br>Stars"))
        elif p and (p.leapfrog_grade or "").strip()[:1].upper() in tuple("ABCDF"):
            tiles.append(((p.leapfrog_grade or "").strip()[:1].upper(), "Leapfrog<br>Grade"))
        elif locs:
            tiles.append((str(len(locs)), "Locations<br>Assessed"))
        else:
            tiles.append((_profile_label(p, result), "Weighting<br>Profile"))
    tiles.append((generated, "Report<br>Date"))
    tiles_html = ""
    for v, l in tiles:
        small = ' style="font-size:12pt"' if len(v) > 5 else ""
        tiles_html += (f'<div class="cover-stat-box"><div class="cover-stat-val"{small}>{_e(v)}</div>'
                       f'<div class="cover-stat-lbl">{l}</div></div>')

    ceiling = ""
    if p and p.score_ceiling_applied and p.score_ceiling_reason:
        ceiling = (f'<div style="font-size:8pt;color:rgba(255,255,255,0.6);text-align:center;margin:-24px 0 24px">'
                   f'Score capped at 74 &mdash; {_e(p.score_ceiling_reason)}. The cap lifts once these are verifiable.</div>')

    return f"""
<div class="cover">
  <div class="cover-logo-header">
    {logo_html}
    <div class="cover-report-type-label">Deep Diagnostic<br>AI Reputation Report</div>
  </div>
  <div class="cover-edition">{_e(ed["label"])} &middot; AI Reputation</div>
  <div class="cover-network-name">{_e(name)}</div>
  <div class="cover-subtitle">{subtitle}</div>
  <div class="cover-confidential">Confidential &nbsp;·&nbsp; Prepared exclusively for {_e(name)}</div>
  <div class="cover-score-center">
    <div style="line-height:1"><span class="cover-score-num" style="color:{accent}">{score_str}</span><span class="cover-score-out-of">/100</span></div>
    <div class="cover-score-lbl">AI Reputation Score</div>
    <div class="cover-quartile-badge" style="border-left:4px solid {q_color}">
      <div class="cover-quartile-badge-lbl">National Quartile</div>
      <div class="cover-quartile-badge-val" style="color:{q_color}">{_e(_quartile_label(quartile))} <span style="font-size:11pt;font-weight:500;color:rgba(255,255,255,0.7)">&middot;&nbsp;{_e(q_band)}</span></div>
    </div>
  </div>
  {ceiling}
  <div class="cover-stat-boxes">{tiles_html}</div>
  <div class="cover-benchmark">
    The Pulse Score reflects how visible and favorable this organization is across four pillars{pillar_names}
    when patients and referring physicians ask AI assistants where to get care. It is scored on the {_e(ed["rubric_label"])},
    the same rubric used in the Competitors Rankings for its market. Data is gathered through live queries run directly
    against ChatGPT, Claude, and Gemini.
  </div>
  <div class="cover-meta">
    <span>Pulse | RLDatix &nbsp;·&nbsp; Deep Diagnostic &nbsp;·&nbsp; {_e(name)}</span>
    <span>Generated {generated}</span>
  </div>
</div>"""


def _front_door_rating(p: Optional[RankedProvider]) -> str:
    if not p:
        return ""
    fd = p.google_footprint.front_door
    if fd.verified and fd.rating is not None:
        return f"{fd.rating:.1f}★"
    return ""


def _exec_summary(result: AnalysisResult) -> str:
    """Headline + bullets. The plain-language pass stores a model-written version on
    executive_summary_structured; older results fall back to the verdict's sentences."""
    st = getattr(result, "executive_summary_structured", None)
    if not (st and st.get("headline")):
        verdict = _sentences(result.ai_visibility_verdict)
        overview = _sentences(result.market_overview)
        if not verdict and not overview:
            return ""
        head = verdict[0] if verdict else overview[0]
        rest = (verdict[1:] if verdict else overview[1:]) + (overview if verdict else [])
        st = {"headline": head, "bullets": [s for s in rest if s != head][:4]}
    st = {"headline": st.get("headline", ""), "bullets": list(st.get("bullets") or [])[:4]}
    return f'<h2>Executive Summary</h2><div class="exec-summary">{_structured_html(st, st.get("headline", ""))}</div>'


def _reputation_section(result: AnalysisResult, p: Optional[RankedProvider], ed: dict, cfg: dict) -> str:
    primary, accent, pale = cfg["primary"], cfg["accent"], cfg["pale"]
    name, _ = _display_name(result)
    score = p.ai_visibility_score if p else None
    composite = str(score) if score is not None else "—"
    quartile, q_band = grade_from_score(score)
    conf = _confidence(result)
    ev = ""
    if conf:
        color = {"high": "#1a7a4a", "medium": "#b8860b", "low": "#b42318"}.get(conf.get("level"), "#7a9095")
        ev = (f' &nbsp;·&nbsp; Evidence: <strong style="color:{color}">{_e(conf.get("label", ""))}</strong>'
              f' &middot; {_e(conf.get("note", ""))}')
    site = ""
    if p and p.website_url:
        site = f'<p style="font-size:8.5pt;color:#8a9aaa;margin:-2px 0 0"><a href="{_e(p.website_url)}" style="color:#2e7d9a;text-decoration:none">{_e(p.website_url)}</a></p>'
    ai_says_html = ""
    if p and p.ai_says:
        ai_says_html = (
            f'<div style="background:{pale};border-left:3px solid {accent};padding:12px 16px;margin-top:12px;border-radius:4px;break-inside:avoid">'
            f'<div style="font-size:8.5pt;font-weight:700;letter-spacing:0.05em;color:{primary};text-transform:uppercase;margin-bottom:4px">What AI Assistants Currently See'
            f' <span style="font-weight:400;letter-spacing:0;text-transform:none;color:#7a8a9a">&mdash; from training memory &amp; live retrieval &middot; Claude, ChatGPT, Gemini</span></div>'
            f'<div style="font-size:9.5pt;color:#3a4a5a">{_structured_html(getattr(p, "ai_says_structured", None), _strip_md(p.ai_says), primary=primary)}</div></div>')
    return f"""
<h2>AI Reputation</h2>
<p style="font-size:9pt;color:#4a5a6a;margin-top:-4px">How AI assistants view {_e(name)} &mdash; the four-pillar Pulse Score on the {_e(ed["rubric_label"])} ({_e(_profile_label(p, result))} weighting).</p>
{site}
<div class="score-breakdown">
{_bars_html(_pillars(p, result))}
<p style="font-size:8.5pt;color:#8a9aaa;margin-top:8px">Pulse Score: <strong style="color:{primary}">{composite}/100</strong> &nbsp;·&nbsp; {_e(_quartile_label(quartile))} ({_e(q_band)}){ev}</p>
{_identity_cap_note(result)}
{_pillar_notes_html(result)}
</div>
{ai_says_html}
{_ai_access_alert_html(result)}"""


def _identity_cap_note(result: AnalysisResult) -> str:
    c = (getattr(result, "website_facts", None) or {}).get("identity_cap")
    if not c:
        return ""
    return (f'<p style="font-size:8.5pt;color:{_RED_BAD};margin:4px 0 0"><strong>Identity &amp; Machine-Readability capped at {c["cap"]}</strong> &mdash; '
            f'{_e(c["reason"])} (was {c["pillar_before"]}; Pulse Score {c["score_before"]} &rarr; {c["score_after"]}). The cap lifts once the site is readable.</p>')


def _pillar_notes_html(result: AnalysisResult) -> str:
    """One plain sentence per pillar, right under the bars."""
    from .checklist import pillar_blurbs
    items = [(l, b) for l, b in pillar_blurbs(result) if b]
    if not items:
        return ""
    return ('<div class="pillar-notes">' + "".join(f'<div><strong>{_e(l)}.</strong> {_e(b)}</div>' for l, b in items) + "</div>")


_CHECK_STYLE = {"pass": ("✓ Pass", _GREEN_OK), "fail": ("✗ Fix", _RED_BAD), "partial": ("◐ Partial", "#b8860b"), "na": ("— Not checked", "#8a9aaa")}


def _checklist_section(result: AnalysisResult, findings=None) -> str:
    """AI Readiness Checklist — the same ten measured checks on every report."""
    from .checklist import build_checklist, summarize
    rows = build_checklist(result, findings)
    sm = summarize(rows)
    trs = ""
    for r in rows:
        lbl, col = _CHECK_STYLE[r["status"]]
        ref = f' <span style="font-family:monospace;font-size:7.5pt;color:#5a6a7a">{_e(r["finding"])}</span>' if r.get("finding") else ""
        trs += (f'<tr><td style="font-weight:600">{_e(r["label"])}</td>'
                f'<td style="white-space:nowrap;font-weight:700;color:{col}">{lbl}</td>'
                f'<td style="font-size:8.5pt;color:#3a4a5a">{_e(r["detail"])}{ref}</td>'
                f'<td style="font-size:8pt;color:#5a6a7a">{_e(r["feeds"])}</td></tr>')
    intro = (f"The same ten checks on every report, each read from the source (website crawl, AI-crawler probe, Google profiles, "
             f"review counts, Wikidata and Wikipedia). This report: <strong>{sm['pass']} pass</strong>, <strong style=\"color:{_RED_BAD}\">{sm['fail']} to fix</strong>"
             + (f", {sm['partial']} partial" if sm['partial'] else "") + (f", {sm['na']} not checked" if sm['na'] else "")
             + ". A finding id points to the fix in the content analysis.")
    return (f'<div style="break-inside:avoid;page-break-inside:avoid"><h2>AI Readiness Checklist</h2>'
            f'<p style="font-size:9pt;color:#4a5a6a">{intro}</p>'
            f'<table class="facility-table deep-table"><thead><tr><th>Check</th><th>Result</th><th>What we found</th><th>Feeds</th></tr></thead>'
            f'<tbody>{trs}</tbody></table></div>')


def _first_moves_section(result: AnalysisResult) -> str:
    if not (result.top_recommendation or "").strip():
        return ""
    return (f'<h2>{_first_moves_title(result, SECTION_ASSESSMENT)}</h2>'
            f'<div class="first-moves-box">{_assessment_body_html(result.top_recommendation, footnote=False, result=result)}</div>')


# ── Locations: one merged table ───────────────────────────────────────────────

def _location_rows(result: AnalysisResult, p: Optional[RankedProvider]) -> list[dict]:
    """Union of confirmed locations, Google profile checks and composite practice rows, keyed by name."""
    rows: dict[str, dict] = {}
    order: list[str] = []

    def row(name: str) -> dict:
        k = _norm_name(name)
        if k not in rows:
            rows[k] = {"name": re.sub(r"\s*\((anchor listing|anchor)\)", "", name, flags=re.I).strip()}
            order.append(k)
        return rows[k]

    anchor_name = (result.entity_name or "")
    if p is not None:
        for loc in p.consolidated_locations or []:
            r = row(loc.name)
            r.setdefault("address", loc.address or "")
            if loc.google_rating is not None:
                r.setdefault("google_rating", loc.google_rating)
                r.setdefault("google_count", loc.google_review_count)
    au = result.profile_audit or {}
    for prof in (au.get("profiles") or []):
        if not prof.get("found"):
            continue
        r = row(prof.get("name") or "")
        r["audit"] = prof
        if prof.get("rating") is not None:
            r["google_rating"] = prof.get("rating")
            r["google_count"] = prof.get("review_count")
        if not r.get("address") and prof.get("city"):
            r["address"] = prof.get("city")
    for cr in result.practice_composite_rows or []:
        r = row(cr.get("practice_name") or "")
        r["composite"] = cr
        if cr.get("is_anchor"):
            r["anchor"] = True
            if cr.get("google_rating") is not None and r.get("google_rating") is None:
                r["google_rating"] = cr.get("google_rating")
        elif r.get("google_rating") is None and _google_only(cr):
            # The composite found Google alone — show it in the Google column rather than "—".
            r["google_rating"] = cr.get("avg_rating")
            r["google_count"] = cr.get("total_reviews")
    # The analyzed entity is the anchor row even without a composite
    if p is not None:
        k = _norm_name(p.name)
        if k in rows:
            rows[k]["anchor"] = True
        elif anchor_name and _norm_name(anchor_name) in rows:
            rows[_norm_name(anchor_name)]["anchor"] = True
    out = [rows[k] for k in order]
    out.sort(key=lambda r: (0 if r.get("anchor") else 1))
    return out


def _physician_rows(result: AnalysisResult) -> list[dict]:
    """Union of NPI/linkage checks and composite physician ratings, keyed by name."""
    rows: dict[str, dict] = {}
    order: list[str] = []

    def row(name: str) -> dict:
        k = _norm_name(name)
        if k not in rows:
            rows[k] = {"name": name}
            order.append(k)
        return rows[k]

    pf = result.physician_facts or {}
    if pf.get("status") == "measured":
        for r0 in pf.get("rows") or []:
            r = row(r0.get("name") or "")
            r["facts"] = r0
    comp: list[tuple[str, dict]] = []
    for cr in result.practice_composite_rows or []:
        for ph in cr.get("physicians") or []:
            comp.append((cr.get("practice_name") or "", ph))
    if not comp:
        for ph in result.physician_composite_rows or []:
            comp.append((ph.get("practice_name") or "", ph))
    comp.sort(key=lambda t: (t[1].get("not_established", False), -(t[1].get("total_reviews") or 0)))
    for pname, ph in comp:
        r = row(ph.get("physician_name") or "")
        if "composite" not in r:
            r["composite"] = ph
            r["practice"] = pname
    return [rows[k] for k in order]


def _google_only(cr: dict) -> bool:
    if cr.get("not_established") or cr.get("avg_rating") is None:
        return False
    entries = cr.get("platform_entries")
    if entries:
        return len(entries) == 1 and str(entries[0][0]).lower() == "google"
    return (cr.get("platforms_found") == 1) and "google" in str(cr.get("platforms_list", "")).lower()


_PC_LABELS = {"google": "Google", "healthgrades": "Healthgrades", "vitals": "Vitals", "webmd": "WebMD", "yelp": "Yelp", "ratemds": "RateMDs"}


def _platforms_cell(cr: dict) -> str:
    n = cr.get("platforms_found") or 0
    if not n:
        return "—"
    entries = cr.get("platform_entries")
    if entries:
        parts = []
        for pk, _pc, pu in entries:
            label = _PC_LABELS.get(pk, str(pk).capitalize())
            parts.append(f'<a href="{_e(pu)}" style="color:#1a6e9e;text-decoration:underline">{_e(label)}</a>' if pu else _e(label))
        return f"{n}: {', '.join(parts)}"
    return f"{n}: {_e(cr.get('platforms_list', ''))}"


def _rating_cell(cr: dict) -> str:
    if cr.get("not_established"):
        return '<span style="color:#7a9095;font-style:italic">Not established</span>'
    avg = cr.get("avg_rating")
    if avg is None:
        return "—"
    unv = "" if cr.get("affiliation_verified", True) else ' <span style="font-size:7.5pt;color:#7a9095">(unverified affiliation)</span>'
    return f"<strong>{avg:.1f}</strong> / 5{unv}"


_WHOLE_TABLE_ROWS = 15


def _table_section(title: str, intro: str, th: list, trs: str, n_rows: int) -> str:
    """Heading + intro + table. Tables of up to 15 rows never split across pages (the whole
    section moves together); longer ones flow and repeat their header row on each page."""
    table = f'<table class="facility-table deep-table"><thead><tr>{"".join(th)}</tr></thead><tbody>{trs}</tbody></table>'
    head = f'<h2>{title}</h2><p style="font-size:9pt;color:#4a5a6a">{intro}</p>'
    if n_rows <= _WHOLE_TABLE_ROWS:
        return f'<div style="break-inside:avoid;page-break-inside:avoid">{head}{table}</div>'
    return f'<div style="break-inside:avoid;page-break-inside:avoid">{head}</div>{table}'


def _tick(v) -> str:
    if v is True:
        return f'<span style="color:{_GREEN_OK};font-weight:700">✓</span>'
    if v is False:
        return f'<span style="color:{_RED_BAD};font-weight:700">—</span>'
    return '<span style="color:#b0b8c0">—</span>'


def _locations_section(result: AnalysisResult, p: Optional[RankedProvider], ed: dict) -> str:
    rows = _location_rows(result, p)
    if not rows:
        return ""
    has_audit = any(r.get("audit") for r in rows)
    has_comp = any(r.get("composite") and r["composite"].get("avg_rating") is not None
                   and not _google_only(r["composite"]) and not r["composite"].get("is_anchor") for r in rows)
    au = result.profile_audit or {}
    sm = au.get("summary") or {}
    intro = f"{len(rows)} location{'s' if len(rows) != 1 else ''} confirmed for this {'practice' if ed['rubric'] == 'practice' else 'organization'}; the highlighted row is the analyzed entity."
    if has_audit and sm.get("checked"):
        dom = au.get("domain") or "the practice site"
        intro += (f" {sm['checked']} Google Business Profile{'s' if sm['checked'] != 1 else ''} read directly from Google: "
                  f"{sm.get('linked', 0)} link to <strong>{_e(dom)}</strong>, {sm.get('with_hours', 0)} list hours, {sm.get('with_phone', 0)} list a phone, "
                  f"{sm.get('with_photos', 0)} have 3+ photos, {sm.get('thin_reviews', 0)} have under 5 reviews — the signals AI assistants read as \"managed\".")
    if has_comp:
        intro += " All-platform rating = review-count-weighted average across Google, Healthgrades, Vitals, WebMD, Yelp and RateMDs (Zocdoc excluded)."
    owner = ""
    if au.get("owner_attested"):
        since = (result.owner_facts or {}).get("reviews_since")
        owner = (f'<p style="font-size:8.5pt;color:{_GREEN_OK};margin-top:6px">Owner-attested: the practice reports these profiles are claimed and managed'
                 + (f"; review invitations active since {_e(since)}" if since else "") + ".</p>")

    has_addr = any((r.get("address") or "").strip() for r in rows)
    th = ['<th>Location</th>'] + (['<th>Address</th>'] if has_addr else []) + ['<th style="text-align:center">Google</th>']
    if has_audit:
        th += ['<th style="text-align:center">Links to site</th>', '<th style="text-align:center">Hours</th>',
               '<th style="text-align:center">Phone</th>', '<th style="text-align:center">Photos</th>']
    if has_comp:
        th += ['<th style="text-align:center">All platforms</th>', '<th style="text-align:right">Reviews</th>', '<th>Platforms found</th>']
    trs = ""
    for r in rows:
        anchor = r.get("anchor")
        style = ' style="background:#edf6f7;font-weight:700"' if anchor else ""
        name = _e(r["name"]) + (' <span style="font-size:7.5pt;color:#7a9095;font-weight:400">(analyzed)</span>' if anchor else "")
        g = r.get("google_rating")
        gc = r.get("google_count")
        google = (f'{g:.1f}★ <span style="font-size:7.5pt;color:#7a8a9a">({gc:,})</span>' if (g is not None and gc) else
                  (f"{g:.1f}★" if g is not None else
                   ('<span style="color:#7a9095;font-style:italic;font-size:8pt">Not established</span>'
                    if (r.get("composite") or {}).get("not_established") else '<span style="color:#b0b8c0">—</span>')))
        tds = [f"<td>{name}</td>"] + ([f'<td style="font-size:8.5pt;color:#4a5a6a">{_e(r.get("address") or "")}</td>'] if has_addr else []) + \
              [f'<td style="text-align:center">{google}</td>']
        if has_audit:
            a = r.get("audit")
            if a:
                tds += [f'<td style="text-align:center">{_tick(bool(a.get("domain_matches")))}</td>',
                        f'<td style="text-align:center">{_tick(bool(a.get("has_hours")))}</td>',
                        f'<td style="text-align:center">{_tick(bool(a.get("has_phone")))}</td>',
                        f'<td style="text-align:center">{_tick((a.get("photos") or 0) >= 3)}</td>']
            else:
                tds += ['<td style="text-align:center;color:#b0b8c0">—</td>'] * 4
        if has_comp:
            c = r.get("composite")
            if c and not (_google_only(c) and not c.get("is_anchor")):
                tds += [f'<td style="text-align:center">{_rating_cell(c)}</td>',
                        f'<td style="text-align:right">{c.get("total_reviews") or "—"}</td>',
                        f'<td style="font-size:8.5pt">{_platforms_cell(c)}</td>']
            elif c:
                tds += ['<td style="text-align:center;color:#b0b8c0">—</td>', f'<td style="text-align:right">{c.get("total_reviews") or "—"}</td>',
                        f'<td style="font-size:8.5pt">{_platforms_cell(c)}</td>']
            else:
                tds += ['<td style="text-align:center;color:#b0b8c0">—</td>', '<td style="text-align:right;color:#b0b8c0">—</td>', '<td></td>']
        trs += f"<tr{style}>{''.join(tds)}</tr>"
    return _table_section("Locations", intro, th, trs, len(rows)) + owner


def _physicians_section(result: AnalysisResult) -> str:
    rows = _physician_rows(result)
    if not rows:
        return ""
    pf = result.physician_facts or {}
    has_facts = pf.get("status") == "measured" and any(r.get("facts") for r in rows)
    has_comp = any(r.get("composite") for r in rows)
    intro = f"{len(rows)} physician{'s' if len(rows) != 1 else ''} associated with this organization."
    if has_facts:
        lp = (f"{pf['linkage_pct']}% ({pf['linked']} of {pf['checked']})" if pf.get("linkage_pct") is not None else "not measurable")
        cs = f"{pf['cert_stated']} of {pf['cert_checked']}" if pf.get("cert_checked") else "no pages read"
        intro += (f" {pf.get('checked', len(rows))} were checked against the NPI registry"
                  + (f" and {pf.get('bio_pages_read')} of the organization's own bio pages" if pf.get("bio_pages_read") else "")
                  + f". Linked to a confirmed location: <strong>{_e(lp)}</strong> — this measured value sets physician↔practice linkage in "
                  f"Identity &amp; Machine-Readability. Certification stated on the pages read: <strong>{_e(cs)}</strong> "
                  f"(AI assistants can only cite a certification the site states).")
    if has_comp:
        intro += " Rating = review-count-weighted average across the platforms found."
    th = ["<th>Physician</th>"]
    if has_facts:
        th += ['<th style="text-align:center">NPI registry</th>', '<th style="text-align:center">Linked to a confirmed location</th>',
               '<th style="text-align:center">Certification stated on site</th>']
    if has_comp:
        th += ['<th style="text-align:center">Rating</th>', '<th style="text-align:right">Reviews</th>', '<th>Platforms found</th>']
    try:
        from .holds import is_held as _is_held
    except Exception:
        _is_held = None
    trs = ""
    for r in rows:
        raw = r["name"]
        if not any(raw.lower().startswith(t) for t in ("dr.", "pa-", "np ", "rn ", "do ", "md ")):
            raw = "Dr. " + raw
        tds = [f"<td>{_e(raw)}</td>"]
        if has_facts:
            f = r.get("facts")
            if f:
                st = f.get("registry")
                reg = (f'<span style="color:{_GREEN_OK};font-weight:700">found</span>' if st == "found"
                       else f'<span style="color:#7a9095">{_e(st or "—")}</span>')
                tds += [f'<td style="text-align:center">{reg}</td>',
                        f'<td style="text-align:center">{_tick(f.get("linked"))}</td>',
                        f'<td style="text-align:center">{_tick(f.get("cert_stated"))}</td>']
            else:
                tds += ['<td style="text-align:center;color:#b0b8c0">—</td>'] * 3
        if has_comp:
            c = r.get("composite")
            held = False
            if c and _is_held is not None:
                try:
                    held = _is_held(c.get("physician_name", ""), entity=r.get("practice", ""))
                except Exception:
                    held = False
            if c and held:
                tds += ['<td colspan="3" style="color:#7a9095;font-style:italic">◐ Partial — identity verification pending</td>']
            elif c:
                tds += [f'<td style="text-align:center">{_rating_cell(c)}</td>',
                        f'<td style="text-align:right">{c.get("total_reviews") or "—"}</td>',
                        f'<td style="font-size:8.5pt">{_platforms_cell(c)}</td>']
            else:
                tds += ['<td style="text-align:center;color:#b0b8c0">—</td>', '<td style="text-align:right;color:#b0b8c0">—</td>', '<td></td>']
        trs += f"<tr>{''.join(tds)}</tr>"
    return _table_section("Physicians", intro, th, trs, len(rows))


# ── Evidence behind the score ─────────────────────────────────────────────────

def evidence_section_html(p: Optional[RankedProvider], heading: bool = True) -> str:
    """Public & social ratings, patient voice, quality & accreditation, strengths / areas
    for improvement and 'best for' — as short labelled bullets. Hospital-only signals
    (Leapfrog, CMS stars) only appear for hospital providers. Used by the Deep Diagnostic
    and by Compare Two's per-entity section."""
    if p is None:
        return ""
    is_practice = getattr(p, "report_type", "hospital") == "practice"
    items: list[str] = []
    fd = p.google_footprint.front_door
    fp = p.google_footprint
    sa = fp.system_aggregate
    if fd.verified and fd.rating is not None:
        label = "Brand / parent Google listing" if sa.available else "Google front door"
        items.append(f"<strong>{label}:</strong> {fd.rating:.1f}★{_vflag('verified')}")
    if sa.available:
        diff = ""
        if fd.verified and fd.rating and sa.rating:
            d = round(sa.rating - fd.rating, 1)
            if abs(d) >= 0.3:
                diff = f' <span style="color:#B45309">({abs(d):.1f}★ {"higher" if d > 0 else "lower"} than the brand listing)</span>'
        conf = "registry-enumerated" if sa.confidence == "registry" else "sampled"
        items.append(f"<strong>System-wide (all locations):</strong> {sa.rating:.1f}★ across {sa.total_reviews:,} reviews at "
                     f"{sa.location_count}{'+' if sa.capped else ''} location{'s' if sa.location_count != 1 else ''}{diff} "
                     f'<span style="color:#7a8a9a">(review-count-weighted, {conf})</span>')
    footprint = fp.rating_range or fp.listings_estimate or fp.consistency
    if footprint:
        cons = f"; {_e(fp.consistency)}" if fp.consistency and (fp.rating_range or fp.listings_estimate) else ""
        items.append(f"<strong>Footprint:</strong> {_e(footprint)}{cons}")
    tpa = p.third_party_aggregate
    if tpa.rating is not None or tpa.note:
        agg = (f"{tpa.rating:.1f} avg" if tpa.rating is not None else "") + (f" — {_e(tpa.note)}" if tpa.note else "")
        items.append(f"<strong>Third-party (Healthgrades, Vitals, WebMD, Yelp):</strong> {agg.strip(' —')}")
    if fp.gap_note:
        items.append(f'<strong>Gap:</strong> <span style="color:#B45309">{_e(fp.gap_note)}</span>')
    if p.patient_voice_summary:
        items.append(f"<strong>Patient voice:</strong> {_e(' '.join(_sentences(p.patient_voice_summary)[:2]))}")
    if p.disqualifiers:
        items.append(f'<strong style="color:{_RED_BAD}">Disqualifiers:</strong> {_e("; ".join(p.disqualifiers))}')
    ratings_html = "".join(f"<li>{x}</li>" for x in items)

    quality = ("" if is_practice else _outcomes_safety_block(p)) + _quality_signals_block(p)

    strengths = "".join(f"<li>{_e(_strip_md(s))}</li>" for s in list(p.key_strengths)[:5])
    weaknesses = "".join(f"<li>{_e(_strip_md(w))}</li>" for w in (list(p.notable_weaknesses) + _outcomes_safety_weaknesses(p))[:5])
    traits = ""
    if strengths or weaknesses:
        traits = (f'<div class="traits"><div class="trait-col"><div class="trait-label strengths-label">Strengths</div><ul>{strengths}</ul></div>'
                  f'<div class="trait-col"><div class="trait-label weaknesses-label">Areas for Improvement</div><ul>{weaknesses}</ul></div></div>')
    best = f'<p class="best-for"><strong>Best for:</strong> {_e(_strip_md(p.best_suited_for))}</p>' if p.best_suited_for else ""
    if not (ratings_html or quality or traits or best):
        return ""
    head = "<h2>Evidence Behind the Score</h2>" if heading else ""
    first = (f'<div class="ev-label">Public &amp; Social Ratings</div><ul class="ev-list">{ratings_html}</ul>' if ratings_html
             else (f'<div class="ev-label">Quality &amp; Accreditation</div>{quality}' if quality else ""))
    rest = (f'<div class="ev-label">Quality &amp; Accreditation</div>{quality}' if (quality and ratings_html) else "")
    return (f'<div class="evidence"><div style="break-inside:avoid;page-break-inside:avoid">{head}{first}</div>'
            + rest + (f'<div style="break-inside:avoid;page-break-inside:avoid">{traits}{best}</div>' if (traits or best) else "")
            + "</div>")


def _roadmap_section(result: AnalysisResult) -> str:
    from .pdf import _roadmap_item_html
    if result.improvement_sections:
        parts = []
        for sec in result.improvement_sections:
            items_li = "\n".join(_roadmap_item_html(item) for item in sec.items)
            parts.append(f'<div class="advice-group"><div class="advice-group-title">{_e(_strip_md(sec.title))}</div>'
                         f'<div class="advice-group-desc">{_e(_strip_md(sec.description))}</div><ol>{items_li}</ol></div>')
        body = "\n".join(parts)
    elif result.practical_advice:
        body = "<ol>" + "\n".join(f"<li>{_e(_strip_md(a))}</li>" for a in result.practical_advice) + "</ol>"
    else:
        return ""
    return f'<h2>Improvement Roadmap</h2><p style="font-size:9pt;color:#4a5a6a;margin-top:-4px">The detailed roadmap for your web and marketing teams.</p><div class="advice">{body}</div>'


def _teaser_gate(primary: str) -> str:
    return (f'<div class="net-teaser-gate"><div class="blur-lock">&#128274;</div>'
            f'<div class="blur-cta-heading">The full Deep Diagnostic continues below</div>'
            f'<div class="blur-cta-sub">{BLUR_CTA_INDIVIDUAL}</div>'
            f'<div class="blur-cta-actions"><span class="blur-phone">{_TEASER_PHONE}</span>&nbsp;&nbsp;&middot;&nbsp;&nbsp;'
            f'<a href="{_TEASER_DEMO_URL}" class="blur-demo-link">Book a Demo &rarr;</a></div></div>')


def entity_summary_html(result: AnalysisResult) -> str:
    """Compare Two's per-entity block: score + pillar bars, 'What AI assistants currently
    see', and the evidence bullets — inline-styled so it renders inside the comparison
    document's own stylesheet."""
    p = _target(result)
    if p is None:
        return ""
    ed = _edition(result)
    cfg = {"primary": _TEAL, "accent": _SEAFOAM, "pale": _PALE_GREEN}
    css = _deep_extra_css(_TEAL, _SEAFOAM, _PALE_GREEN) + """
.cmp-entity .score-bar-row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.cmp-entity .score-bar-label{width:200px;font-size:8.5pt;flex-shrink:0}
.cmp-entity .score-bar-track{flex:1;height:12px;background:#e4e8ec;border-radius:6px;overflow:hidden}
.cmp-entity .score-bar-fill{height:100%;border-radius:6px}
.cmp-entity .score-bar-num{width:36px;text-align:right;font-size:9.5pt;font-weight:700}
.cmp-entity h2{font-size:9pt;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#0F4146;margin:14px 0 6px;padding-bottom:4px;border-bottom:2px solid #80F8E4}
"""
    css += """
.cmp-entity .facility-table{width:100%;border-collapse:collapse;margin:8px 0;font-size:8.5pt}
.cmp-entity .facility-table th{background:#0F4146;color:#fff;padding:6px 8px;text-align:left;font-size:7.5pt}
.cmp-entity .facility-table td{padding:5px 8px;border-bottom:1px solid #dde3ea;vertical-align:middle}
"""
    body = (_reputation_section(result, p, ed, cfg).replace("<h2>AI Reputation</h2>", "") + evidence_section_html(p)
            + _locations_section(result, p, ed) + _physicians_section(result))
    return f'<style>{css}</style><div class="cmp-entity">{body}</div>'


# ─────────────────────────────────────────────────────────────────────────────
# CSS additions on top of the network stylesheet
# ─────────────────────────────────────────────────────────────────────────────

def _deep_extra_css(primary: str, accent: str, pale: str) -> str:
    return f"""
.section-title {{ font-size: 8pt; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: {primary}; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 2px solid {accent}; }}
.first-moves-box {{ background: {pale}; border-left: 5px solid {accent}; padding: 14px 20px; margin: 12px 0 8px; border-radius: 0 8px 8px 0; break-inside: avoid; }}
.first-moves-box p {{ font-size: 10pt; margin: 0 0 6px; }}
.first-moves {{ margin: 0; padding-left: 18px; }}
.first-moves li {{ font-size: 10pt; line-height: 1.5; margin-bottom: 6px; }}
.first-moves-note {{ font-size: 8pt; color: #7a8a9a; font-style: italic; margin-top: 6px; }}
.deep-table {{ font-size: 9pt; }}
.deep-table th {{ font-size: 8pt; }}
.deep-table td {{ padding: 6px 8px; }}
.evidence {{ margin: 8px 0 16px; }}
.pillar-notes {{ margin-top: 10px; font-size: 8.5pt; color: #4a5a6a; line-height: 1.5; columns: 2; column-gap: 24px; }}
.pillar-notes div {{ break-inside: avoid; margin-bottom: 3px; }}
.pillar-notes strong {{ color: {primary}; }}
.ev-label {{ font-size: 8pt; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #5a6a7a; margin: 12px 0 4px; }}
.ev-list {{ margin: 0 0 4px 18px; padding: 0; }}
.ev-list li {{ font-size: 9pt; line-height: 1.5; margin-bottom: 3px; }}
.traits {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 12px 0 8px; }}
.trait-label {{ font-size: 8pt; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 4px; }}
.strengths-label {{ color: #1a7a3c; }}
.weaknesses-label {{ color: #8b1c1c; }}
.trait-col ul {{ margin: 0 0 0 16px; padding: 0; }}
.trait-col li {{ font-size: 9pt; line-height: 1.45; margin-bottom: 3px; }}
.best-for {{ font-size: 9pt; color: #3a4a5a; margin: 6px 0 0; }}
.quality-signals {{ margin: 4px 0 8px; }}
.qs-label {{ display: none; }}
.qs-badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; align-items: center; }}
.qs-badge {{ font-size: 7.5pt; font-weight: 700; padding: 2px 8px; border-radius: 3px; border: 1.5px solid; white-space: nowrap; }}
.qs-leapfrog-A {{ color: #1d6b4a; border-color: #1d6b4a; background: #f0faf4; }}
.qs-leapfrog-B {{ color: #2a7a5e; border-color: #2a7a5e; background: #f0faf4; }}
.qs-leapfrog-C {{ color: #7a5e00; border-color: #b38b00; background: #fffbeb; }}
.qs-leapfrog-D {{ color: #8b4000; border-color: #c05c1a; background: #fff5ee; }}
.qs-leapfrog-F {{ color: #8b0000; border-color: #c00000; background: #fff0f0; }}
.qs-leapfrog-N {{ color: #7a9095; border-color: #c0d4d8; background: {pale}; }}
.qs-accred {{ color: #0a5c70; border-color: #73D2E1; background: #DCF4F8; }}
.qs-cms-5, .qs-cms-4 {{ color: #1d6b4a; border-color: #1d6b4a; background: #f0faf4; }}
.qs-cms-3 {{ color: #7a5e00; border-color: #b38b00; background: #fffbeb; }}
.qs-cms-2 {{ color: #8b4000; border-color: #c05c1a; background: #fff5ee; }}
.qs-cms-1 {{ color: #8b0000; border-color: #c00000; background: #fff0f0; }}
.qs-usnews-ranked {{ color: #1a3a6e; border-color: #2a5ab0; background: #eef3fc; }}
.qs-usnews-hp {{ color: #2a4a80; border-color: #6a8ac0; background: #f4f6fc; }}
.qs-quality {{ font-size: 9pt; color: #3a4a5a; line-height: 1.45; }}
.outcomes-safety {{ margin: 4px 0 8px; padding: 8px 10px; background: #f5f8fa; border: 1px solid #c8dde2; border-radius: 4px; }}
.os-label {{ display: none; }}
.os-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 9pt; }}
.os-key {{ font-weight: 600; color: #3a5a60; min-width: 190px; }}
.os-absent {{ color: #8aacb2; font-style: italic; }}
.os-verify {{ font-size: 7.5pt; color: #9ab0b5; margin-top: 3px; }}
.advice ol {{ padding-left: 18px; margin: 0; }}
.advice li {{ font-size: 9pt; margin-bottom: 6px; line-height: 1.5; }}
.advice-group {{ margin-bottom: 16px; }}
.advice-group-title {{ font-size: 10pt; font-weight: 700; color: {primary}; margin: 12px 0 2px; }}
.advice-group-desc {{ font-size: 8.5pt; color: #5a6a7a; font-style: italic; margin-bottom: 5px; }}
.disclaimer {{ border-top: 1px solid #dde3ea; padding-top: 10px; margin-top: 24px; font-size: 7.5pt; color: #7a8a9a; line-height: 1.5; }}
.pxcontent {{ font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif; margin: 24px -48px 0; }}
.pxcontent .band {{ background: {primary}; }}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Document
# ─────────────────────────────────────────────────────────────────────────────

def build_deep_html(result: AnalysisResult, brand_cfg: dict, content_findings=None, page_map=None,
                    content_teaser: bool = False, content_keys=None) -> str:
    """The Deep Diagnostic document. `content_findings` embeds the Content Report (practice
    combined report; `content_teaser` blurs it); `content_keys` appends the Content
    Improvement Keys box (hospital Deep Diagnostic). `result.teaser_report` blurs everything
    below the AI Reputation section."""
    cfg = dict(brand_cfg or {})
    primary, accent, pale = cfg.get("primary", _TEAL), cfg.get("accent", _SEAFOAM), cfg.get("pale", _PALE_GREEN)
    p = _target(result)
    ed = _edition(result)
    teaser = bool(result.teaser_report)

    if cfg.get("logo_html"):
        logo_html = cfg["logo_html"]
    elif cfg.get("logo_path") and cfg["logo_path"].exists():
        import base64
        logo_html = f'<img src="data:image/svg+xml;base64,{base64.b64encode(cfg["logo_path"].read_bytes()).decode()}" class="cover-logo-img" alt="Logo">'
    else:
        uri = _logo_data_uri()
        logo_html = f'<img src="{uri}" class="cover-logo-img" alt="RLDatix">' if uri else '<div class="cover-logo-text">Pulse</div>'

    cover = _cover(result, p, {"primary": primary, "accent": accent, "pale": pale}, logo_html, ed)
    exec_sum = _exec_summary(result)
    reputation = _reputation_section(result, p, ed, {"primary": primary, "accent": accent, "pale": pale})
    first_moves = _first_moves_section(result)
    locations = _locations_section(result, p, ed)
    physicians = _physicians_section(result)
    spot = _spotcheck_section(result)
    evidence = evidence_section_html(p)

    # Roadmap, or the embedded Content Report (practice combined), or the keys box (hospital).
    extra_css = ""
    if content_findings is not None:
        from .content_report_pdf import _content_body_html, _content_css
        body = _content_body_html(result.entity_name or result.report_title or result.location, result.location,
                                  content_findings, report_title=result.report_title or result.entity_name or "",
                                  service_line=None, page_map=page_map)
        extra_css = _content_css(include_reset=False)
        if content_teaser:
            gate = ('<div class="net-teaser-gate"><div class="blur-lock">&#128274;</div>'
                    '<div class="blur-cta-heading">The full content analysis &amp; prescription continue below</div>'
                    '<div class="blur-cta-sub">Request the complete report to see every verified content finding and its publication-ready fix.</div></div>')
            tail = f'<div class="pxcontent">{gate}<div class="net-teaser-blur-content">{body}</div></div>'
        else:
            tail = f'<div class="pxcontent">{body}</div>'
    else:
        tail = _roadmap_section(result)
        if content_keys is not None:
            tail += _content_keys_section(content_keys)

    disclaimer = f'<div class="disclaimer"><strong>Data Limitations &amp; Disclaimer</strong><br>{_e(result.disclaimer)}</div>' if result.disclaimer else ""
    appendix = _sources_box_html(result) + _methodology_box_html(
        [_e(l) for l, _ in _pillars(p, result)], edition="practice" if ed["rubric"] == "practice" else "hospital")

    checklist = _checklist_section(result, content_findings if content_findings is not None else content_keys)
    gated = f"{first_moves}{locations}{physicians}{spot}{evidence}{checklist}{tail}"
    if teaser:
        detail = f'{_teaser_gate(primary)}<div class="net-teaser-blur-content">{gated}</div>'
    else:
        detail = gated

    name, _ = _display_name(result)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<title>Deep Diagnostic — {_e(name)}</title>
<style>
    h1, h2, h3, h4, .section-title {{ break-after: avoid; page-break-after: avoid; }}
    p, li {{ orphans: 3; widows: 3; }}
    tr {{ break-inside: avoid; page-break-inside: avoid; }}
    thead {{ display: table-header-group; }}
    img, svg {{ break-inside: avoid; page-break-inside: avoid; }}
    @page {{ size: Letter; }}
{_network_css(primary, pale, accent)}
{_deep_extra_css(primary, accent, pale)}
{extra_css}
{cfg.get("css_overrides", "")}
</style>
</head>
<body>
{cover}
<div class="report-body">
{exec_sum}
{reputation}
{detail}
{disclaimer}
{appendix}
</div>
</body>
</html>"""
