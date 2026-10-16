"""Compare Two — the head-to-head pages (2026-10-15 rebuild).

The opening of a comparison report is a real side-by-side: one scoreboard (both Pulse Scores,
quartiles, evidence lines, the four pillars as paired bars on one scale with the gap on every
row, and the finding that would close each gap), a facts table of the measured figures the
Deep Diagnostic pages carry, and "What AI assistants currently see" for both in two columns.
Everything here is drawn from the two stored results — the same objects the detail pages
render — so the numbers agree page to page. Nothing is estimated: a figure we did not measure
reads "Not established"."""
from __future__ import annotations

import re
from typing import Optional

from .models import AnalysisResult, RankedProvider

_GREY = "#7a9095"
_INK = "#333"


def _e(s) -> str:
    from .pdf import _e as _esc
    return _esc(s)


def _target(result: AnalysisResult) -> Optional[RankedProvider]:
    rk = list(result.rankings or [])
    if not rk:
        return None
    return next((p for p in rk if getattr(p, "is_target", False)), rk[0])


def _name(result: AnalysisResult) -> str:
    return result.report_title or result.entity_name or result.location or ""


def _is_practice(result: AnalysisResult) -> bool:
    return (result.entity_type or "hospital") in ("practice", "service_line") or bool(result.service_line)


def _pillars(result: AnalysisResult) -> list[tuple[str, str, Optional[int]]]:
    """[(tier_key, label, score)] in rubric order."""
    from .pdf import _tier_labels
    from .scoring import TIER_KEYS
    p = _target(result)
    profile = (p.weighting_profile if p else None) or result.weighting_profile or "procedural"
    labels = _tier_labels(profile)
    ts = p.tier_scores if p else None
    return [(k, labels.get(k, k), getattr(ts, k, None) if ts is not None else None) for k in TIER_KEYS]


def _confidence(result: AnalysisResult) -> Optional[dict]:
    try:
        from .confidence import score_confidence
        return score_confidence(result)
    except Exception:
        return None


def _findings_for(result: AnalysisResult):
    """Stored content findings for this run (ContentFindings-like object) or None. Fail-soft."""
    try:
        from .db import get_content_findings
        from .models import ContentFindings
        d = get_content_findings(result.run_id) if result.run_id else None
        return ContentFindings.model_validate(d) if d else None
    except Exception:
        return None


def _checklist(result: AnalysisResult, findings) -> list[dict]:
    try:
        from .checklist import build_checklist
        return build_checklist(result, findings)
    except Exception:
        return []


# ── scoreboard ───────────────────────────────────────────────────────────────

def _score_col(result: AnalysisResult, tag: str, primary: str) -> str:
    from .pdf import _quartile_label, _QUARTILE_COLORS
    from .scoring import grade_from_score
    p = _target(result)
    score = p.ai_visibility_score if p else None
    q, band = grade_from_score(score) if score is not None else ("", "")
    qc = _QUARTILE_COLORS.get(q, primary) if score is not None else _GREY
    conf = _confidence(result)
    ev = ""
    if conf:
        color = {"high": "#1a7a4a", "medium": "#b8860b", "low": "#b42318"}.get(conf.get("level"), _GREY)
        ev = (f'<div style="font-size:8pt;color:#5a7880;margin-top:6px;line-height:1.5">Evidence: <strong style="color:{color}">{_e(conf.get("label", ""))}</strong>'
              f' &middot; {_e(conf.get("note", ""))}</div>')
    site = f'<div style="font-size:8pt;color:#2e7d9a;margin-top:4px">{_e(p.website_url)}</div>' if p and p.website_url else ""
    loc = f'<div style="font-size:8.5pt;color:{_GREY};margin-top:2px">{_e(result.location or "")}</div>'
    score_html = (f'<span style="font-size:30pt;font-weight:800;color:{primary};line-height:1">{score}</span>'
                  f'<span style="font-size:10pt;font-weight:700;color:#aabcc0;vertical-align:top;display:inline-block;padding-top:6px;margin-left:2px">/100</span>'
                  if score is not None else f'<span style="font-size:16pt;font-weight:700;color:{_GREY}">Not established</span>')
    chip = (f'<span style="display:inline-block;margin-top:8px;border-left:3px solid {qc};background:#f5f8fa;border-radius:0 5px 5px 0;padding:4px 10px 4px 8px;'
            f'font-size:9pt;font-weight:800;color:{qc}">{_e(_quartile_label(q))} <span style="font-size:7.5pt;font-weight:500;color:#5a7880">&middot; {_e(band)}</span></span>'
            if score is not None else "")
    return (f'<div style="padding:14px 16px;border:1.5px solid {primary};border-radius:8px;min-height:120px">'
            f'<div style="font-size:7.5pt;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#999">{tag}</div>'
            f'<div style="font-size:13.5pt;font-weight:700;color:{primary};margin:2px 0 2px;line-height:1.2">{_e(_name(result))}</div>'
            f'{loc}{site}<div style="margin-top:10px">{score_html}</div><div>{chip}</div>{ev}</div>')


_GAP_WORDS = {
    # pillars the verified checklist does not feed: the trailing side's own 'areas for improvement' is searched by keyword
    "clinical_outcomes_safety": ("certif", "board", "training", "fellowship", "outcome", "leapfrog", "cms", "safety", "quality", "accredit"),
    "credentials_recognition": ("review", "rating", "reputation", "accredit", "ranking", "recogni", "u.s. news", "designation"),
    "patient_experience_reviews": ("schema", "crawl", "machine-readable", "llms", "listing", "duplicate", "identity", "fragment", "wikidata", "wikipedia", "review", "rating"),
    "access_fit": ("insurance", "hours", "schedul", "telehealth", "new-patient", "new patient", "appointment", "location", "access", "service"),
}


def _gap_to_close(result: AnalysisResult, tier_key: str, label: str, rows: list[dict]) -> str:
    """The one verified check (or stated weakness) that would move this pillar for the trailing side."""
    for r in rows:
        if r.get("feeds") == label and r.get("status") in ("fail", "partial"):
            fid = f' <span style="color:{_GREY}">({_e(r["finding"])})</span>' if r.get("finding") else ""
            return f'<strong>{_e(r["label"])}</strong> &mdash; {_e(r.get("detail") or "")}{fid}'
    p = _target(result)
    words = _GAP_WORDS.get(tier_key, ())
    for w in (p.notable_weaknesses if p else []) or []:
        if any(k in (w or "").lower() for k in words):
            return _e(w)
    return ""


def scoreboard_html(result_a: AnalysisResult, result_b: AnalysisResult, cfg: dict, findings_a=None, findings_b=None) -> str:
    from .pdf import _score_bar_color
    primary = cfg.get("primary", "#0F4146")
    pa, pb = _pillars(result_a), _pillars(result_b)
    na, nb = _name(result_a), _name(result_b)
    rows_a = _checklist(result_a, findings_a)
    rows_b = _checklist(result_b, findings_b)
    mixed = (result_a.entity_type != result_b.entity_type)

    def bar(score, align_right=False):
        pct = max(0, min(100, score or 0))
        color = _score_bar_color(score)
        num = str(score) if score is not None else "&mdash;"
        track = (f'<div style="flex:1;height:12px;background:#e4e8ec;border-radius:6px;overflow:hidden;display:flex;justify-content:{"flex-end" if align_right else "flex-start"}">'
                 f'<div style="width:{pct}%;height:100%;background:{color};border-radius:6px"></div></div>')
        numd = f'<div style="width:28px;text-align:{"right" if not align_right else "left"};font-size:9.5pt;font-weight:700;color:{color}">{num}</div>'
        return (numd + track) if align_right else (track + numd)

    pillar_rows, gaps = [], []
    for (ka, la, sa), (kb, lb, sb) in zip(pa, pb):
        label = la if (la == lb or not mixed) else f"{la} / {lb}"
        if sa is None or sb is None or sa == sb:
            gap = f'<span style="color:{_GREY}">tie</span>' if (sa is not None and sa == sb) else f'<span style="color:{_GREY}">&mdash;</span>'
        else:
            lead = "A" if sa > sb else "B"
            gap = f'<span style="font-weight:800;color:{primary}">{lead} +{abs(sa - sb)}</span>'
            if abs(sa - sb) >= 5 and not mixed:
                trail = result_a if sa < sb else result_b
                g = _gap_to_close(trail, ka, la, rows_a if sa < sb else rows_b)
                if g:
                    gaps.append(f'<li><span style="color:{_GREY}">{_e(la)} &middot; {_e(_name(trail))} trails by {abs(sa - sb)}:</span> {g}</li>')
        pillar_rows.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:9px;page-break-inside:avoid">'
            f'<div style="width:170px;font-size:8.5pt;color:{_INK};flex-shrink:0;line-height:1.25">{_e(label)}</div>'
            f'<div style="flex:1;display:flex;align-items:center;gap:6px">{bar(sa, align_right=True)}</div>'
            f'<div style="width:46px;text-align:center;font-size:8.5pt">{gap}</div>'
            f'<div style="flex:1;display:flex;align-items:center;gap:6px">{bar(sb)}</div></div>')
    head = (f'<div style="display:flex;align-items:center;gap:10px;margin:14px 0 8px">'
            f'<div style="width:170px;flex-shrink:0"></div>'
            f'<div style="flex:1;text-align:right;font-size:8pt;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:{primary}">A &middot; {_e(na)}</div>'
            f'<div style="width:46px;text-align:center;font-size:7.5pt;color:{_GREY};text-transform:uppercase;letter-spacing:.05em">gap</div>'
            f'<div style="flex:1;font-size:8pt;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:{primary}">B &middot; {_e(nb)}</div></div>')
    mixed_note = (f'<div style="font-size:7.5pt;color:{_GREY};font-style:italic;margin-top:6px">The two organizations are scored on different rubrics '
                  f'(hospital and practice editions): pillar names and weights differ, so pillar scores are shown but not compared.</div>') if mixed else ""
    gaps_html = ""
    if gaps:
        gaps_html = (f'<div style="margin-top:12px;background:{cfg.get("pale", "#f0faf7")};border-left:3px solid {cfg.get("accent", "#80F8E4")};border-radius:4px;padding:10px 14px;page-break-inside:avoid">'
                     f'<div style="font-size:8pt;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:{primary};margin-bottom:4px">Gap to close</div>'
                     f'<div style="font-size:7.5pt;color:{_GREY};margin-bottom:6px">Where one side trails a pillar by 5 or more: the verified check (or stated weakness) on the trailing side that would move it. '
                     f'Finding ids point to the fix in that organization\'s detail pages.</div>'
                     f'<ul style="margin:0;padding-left:16px;font-size:8.5pt;line-height:1.55;color:{_INK}">{"".join(gaps)}</ul></div>')
    return (f'<div class="section-title" style="font-size:13pt;margin-bottom:14px">Head-to-Head</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">{_score_col(result_a, "Entity A", primary)}{_score_col(result_b, "Entity B", primary)}</div>'
            f'<div style="margin-top:4px">{head}{"".join(pillar_rows)}</div>{mixed_note}{gaps_html}')


# ── facts table ──────────────────────────────────────────────────────────────

_PASS, _FAIL, _PART, _NA = "pass", "fail", "partial", "na"
_STATUS_TXT = {_PASS: ("✓", "#1a7a4a"), _FAIL: ("✗", "#b42318"), _PART: ("◐", "#b8860b"), _NA: ("—", "#8a9aaa")}
_STATUS_RANK = {_PASS: 2, _PART: 1, _FAIL: 0}


def _fact_rows(result: AnalysisResult, rows: list[dict]) -> dict:
    """Measured facts for one side: {key: (display_html, comparable_value_or_None)}."""
    from .pdf import _vflag
    from .plain import measured_counts
    p = _target(result)
    out: dict = {}
    conf = _confidence(result)
    if conf:
        out["evidence"] = (f'<span style="color:{ {"high": "#1a7a4a", "medium": "#b8860b", "low": "#b42318"}.get(conf.get("level"), _GREY)};font-weight:700">{_e(conf.get("label", ""))}</span>',
                           {"high": 3, "medium": 2, "low": 1}.get(conf.get("level")))
    if p is not None:
        fd = p.google_footprint.front_door
        sa = p.google_footprint.system_aggregate
        if fd.verified and fd.rating is not None:
            out["listing"] = (f'{fd.rating:.1f}★{_vflag("verified")}' + (f' <span style="color:{_GREY}">({fd.count:,} reviews)</span>' if fd.count else ""), float(fd.rating))
            out["listing_reviews"] = (f'{fd.count:,}' if fd.count else "Not established", int(fd.count or 0) or None)
        if _is_practice(result):
            profs = [x for x in ((getattr(result, "profile_audit", None) or {}).get("profiles") or []) if x.get("found") and x.get("rating") is not None and x.get("review_count")]
            if len(profs) > 1:
                tot = sum(int(x["review_count"]) for x in profs)
                avg = sum(float(x["rating"]) * int(x["review_count"]) for x in profs) / tot if tot else None
                if avg is not None:
                    out["all_locations"] = (f'{avg:.1f}★ across {tot:,} reviews at {len(profs)} profiles', float(avg))
                    out["all_reviews"] = (f'{tot:,}', tot)
        elif sa.available:
            out["all_locations"] = (f'{sa.rating:.1f}★ across {sa.total_reviews:,} reviews at {sa.location_count}{"+" if sa.capped else ""} location{"s" if sa.location_count != 1 else ""}', float(sa.rating) if sa.rating else None)
            out["all_reviews"] = (f'{sa.total_reviews:,}', int(sa.total_reviews or 0) or None)
    mc = measured_counts(result) if _is_practice(result) else {}
    if mc.get("locations_verified"):
        out["locations"] = (str(mc["locations_verified"]), mc["locations_verified"])
    if mc.get("physicians_on_roster"):
        out["physicians"] = (str(mc["physicians_on_roster"]), mc["physicians_on_roster"])
    pf = getattr(result, "physician_facts", None) or {}
    if pf.get("status") == "measured":
        if pf.get("linkage_pct") is not None:
            out["linkage"] = (f'{pf["linkage_pct"]}% <span style="color:{_GREY}">({pf.get("linked", 0)} of {pf.get("checked", 0)})</span>', int(pf["linkage_pct"]))
        elif pf.get("linkage_note"):
            out["linkage"] = (f'<span style="color:{_GREY}">not measurable</span>', None)
        if pf.get("cert_checked"):
            out["cert"] = (f'{pf.get("cert_stated", 0)} of {pf["cert_checked"]} pages read', round(100 * (pf.get("cert_stated") or 0) / pf["cert_checked"]))
    sc = getattr(result, "spotcheck", None) or {}
    if sc and sc.get("unprompted_asked"):
        out["spot_named"] = (f'{sc.get("unprompted_mentioned", 0)} of {sc["unprompted_asked"]} answers', round(100 * int(sc.get("unprompted_mentioned") or 0) / int(sc["unprompted_asked"])))
        out["spot_cited"] = (("✓ cited" if sc.get("our_domain_cited") else "✗ not cited"), 1 if sc.get("our_domain_cited") else 0)
    for r in rows:
        sym, col = _STATUS_TXT.get(r["status"], ("—", _GREY))
        detail = (r.get("detail") or "").strip()
        detail = re.sub(r"\s+", " ", detail)
        short = detail if len(detail) <= 70 else detail[:67].rstrip() + "…"
        out[f'chk:{r["id"]}'] = (f'<span style="color:{col};font-weight:700">{sym}</span> <span style="color:#5a7880">{_e(short)}</span>', _STATUS_RANK.get(r["status"]))
    if rows:
        from .checklist import summarize
        s = summarize(rows)
        out["checks"] = (f'{s["pass"]} of {s["checked"]} pass', s["pass"])
    if p is not None and not _is_practice(result):
        if p.leapfrog_grade:
            out["leapfrog"] = (_e(p.leapfrog_grade), {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}.get(str(p.leapfrog_grade).strip().upper()[:1]))
        if p.cms_star_rating is not None:
            out["cms"] = (f'{p.cms_star_rating}★', float(p.cms_star_rating))
        if p.accreditations:
            out["accred"] = (f'{len(p.accreditations)} verified', len(p.accreditations))
        if p.us_news_rankings:
            out["usnews"] = (_e("; ".join(str(x) for x in p.us_news_rankings)[:80]), len(p.us_news_rankings))
    return out


_PRACTICE_ONLY = {"locations", "physicians", "linkage", "cert"}
_HOSPITAL_ONLY = {"leapfrog", "cms", "accred", "usnews"}

_FACT_LABELS = [
    ("evidence", "Evidence behind the score"),
    ("listing", "Analyzed Google listing"),
    ("all_locations", "All locations (review-weighted)"),
    ("locations", "Locations confirmed"),
    ("physicians", "Physicians on roster"),
    ("linkage", "Physicians linked to a practice location (NPI registry)"),
    ("cert", "Board certification stated on bio pages read"),
    ("spot_named", "Named by AI assistants unprompted (spot check)"),
    ("spot_cited", "Own website cited by assistants"),
    ("chk:ai_access", "AI assistants can read the website"),
    ("chk:robots", "robots.txt allows AI crawlers"),
    ("chk:org_schema", "Organization schema.org markup"),
    ("chk:physician_schema", "Physician schema.org markup"),
    ("chk:bio_pages", "Crawlable physician bio pages"),
    ("chk:sitemap", "XML sitemap"),
    ("chk:llms_txt", "llms.txt guide for AI assistants"),
    ("chk:google_profiles", "Google Business Profiles complete and linked"),
    ("chk:review_volume", "Review volume behind the rating"),
    ("chk:public_record", "Wikidata item and Wikipedia article"),
    ("checks", "Content checks passed"),
    ("leapfrog", "Leapfrog safety grade"),
    ("cms", "CMS star rating"),
    ("accred", "Accreditations verified"),
    ("usnews", "U.S. News rankings"),
]


def facts_html(result_a: AnalysisResult, result_b: AnalysisResult, cfg: dict, findings_a=None, findings_b=None) -> str:
    primary = cfg.get("primary", "#0F4146")
    fa = _fact_rows(result_a, _checklist(result_a, findings_a))
    fb = _fact_rows(result_b, _checklist(result_b, findings_b))
    trs = []
    def _blank(key, result):
        # A row the other report type does not measure is 'not applicable', never 'not established'.
        if key in _PRACTICE_ONLY and not _is_practice(result):
            return "<span style='color:#8a9aaa'>&mdash; Practice reports only.</span>", None
        if key in _HOSPITAL_ONLY and _is_practice(result):
            return "<span style='color:#8a9aaa'>&mdash; Hospital reports only.</span>", None
        return "<span style='color:#8a9aaa'>Not established</span>", None
    for key, label in _FACT_LABELS:
        if key not in fa and key not in fb:
            continue
        da, va = fa.get(key) or _blank(key, result_a)
        db_, vb = fb.get(key) or _blank(key, result_b)
        if va is None or vb is None or va == vb:
            edge = '<span style="color:#8a9aaa">—</span>'
        else:
            edge = f'<span style="font-weight:800;color:{primary}">{"A" if va > vb else "B"}</span>'
        trs.append(f'<tr style="page-break-inside:avoid"><td style="padding:5px 8px;border-bottom:1px solid #dde3ea;color:{_INK}">{_e(label)}</td>'
                   f'<td style="padding:5px 8px;border-bottom:1px solid #dde3ea">{da}</td>'
                   f'<td style="padding:5px 8px;border-bottom:1px solid #dde3ea">{db_}</td>'
                   f'<td style="padding:5px 8px;border-bottom:1px solid #dde3ea;text-align:center">{edge}</td></tr>')
    if not trs:
        return ""
    th = (f'<tr><th style="background:{primary};color:#fff;padding:6px 8px;text-align:left;font-size:7.5pt">Measured</th>'
          f'<th style="background:{primary};color:#fff;padding:6px 8px;text-align:left;font-size:7.5pt">A &middot; {_e(_name(result_a))}</th>'
          f'<th style="background:{primary};color:#fff;padding:6px 8px;text-align:left;font-size:7.5pt">B &middot; {_e(_name(result_b))}</th>'
          f'<th style="background:{primary};color:#fff;padding:6px 8px;text-align:center;font-size:7.5pt;width:40px">Leads</th></tr>')
    return (f'<div style="margin-top:18px">'
            f'<div class="section-title" style="margin-bottom:8px;page-break-after:avoid">Head-to-Head Facts</div>'
            f'<p style="font-size:8pt;color:{_GREY};margin:0 0 6px;page-break-after:avoid">The same measured figures the detail pages carry — Google listings, the NPI registry, our crawl of each website, the live spot check and the Wikidata / Wikipedia checks. '
            f'"Leads" marks the better measured value; nothing here is estimated.</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:8.5pt"><thead style="display:table-header-group">{th}</thead><tbody>{"".join(trs)}</tbody></table></div>')


# ── what AI assistants see, side by side ─────────────────────────────────────

def ai_says_html(result_a: AnalysisResult, result_b: AnalysisResult, cfg: dict) -> str:
    from .pdf import _strip_md
    from .network_pdf import _structured_html
    primary, accent, pale = cfg.get("primary", "#0F4146"), cfg.get("accent", "#80F8E4"), cfg.get("pale", "#f0faf7")
    cols = []
    for tag, r in (("A", result_a), ("B", result_b)):
        p = _target(r)
        body = _structured_html(getattr(p, "ai_says_structured", None), _strip_md(p.ai_says), primary=primary) if p and p.ai_says else f'<span style="color:#8a9aaa">Not established</span>'
        cols.append(f'<div style="background:{pale};border-left:3px solid {accent};padding:12px 14px;border-radius:4px">'
                    f'<div style="font-size:8pt;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:{primary};margin-bottom:6px">{tag} &middot; {_e(_name(r))}</div>'
                    f'<div style="font-size:9pt;color:#3a4a5a">{body}</div></div>')
    return (f'<div style="margin-top:18px">'
            f'<div class="section-title" style="margin-bottom:4px;page-break-after:avoid">What AI Assistants Currently See</div>'
            f'<p style="font-size:8pt;color:{_GREY};margin:0 0 8px">From training memory and live retrieval &middot; Claude, ChatGPT, Gemini.</p>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">{"".join(cols)}</div></div>')
