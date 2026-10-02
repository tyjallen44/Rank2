"""AI Readiness Checklist — the same ten measured checks on every Deep Diagnostic.

Each check reads a verified fact the run already collected (website crawl, AI-crawler probe,
Google profile audit, review counts, the content analysis snapshot) and reports pass / fix /
partial / not checked, with what was found and which pillar it feeds. Nothing here is modelled
or estimated, and nothing changes the score: it makes every report list the same rows so
practices and programs can be compared.
"""
from __future__ import annotations

from typing import Optional

from .pdf import _tier_labels

PASS, FAIL, PARTIAL, NA = "pass", "fail", "partial", "na"

# (id, label, feed key) — feed key is the rubric pillar key, or a literal for unscored checks.
CHECKS: list[tuple[str, str, str]] = [
    ("ai_access",        "AI assistants can read your website",            "web"),
    ("robots",           "robots.txt allows AI crawlers",                  "web"),
    ("org_schema",       "Organization schema.org markup",                 "web"),
    ("physician_schema", "Physician schema.org markup",                    "web"),
    ("bio_pages",        "Crawlable physician bio pages",                  "web"),
    ("sitemap",          "XML sitemap",                                    "web"),
    ("llms_txt",         "llms.txt guide for AI assistants",               "web"),
    ("google_profiles",  "Google Business Profiles complete and linked",   "reviews"),
    ("review_volume",    "Review volume behind the rating",                "reviews"),
    ("public_record",    "Wikidata item and Wikipedia article",            "record"),
]

# Which pillar each feed lands in, by rubric.
_FEEDS = {
    "practice": {"web": "patient_experience_reviews", "reviews": "credentials_recognition", "record": "patient_experience_reviews"},
    "hospital": {"web": None, "reviews": "patient_experience_reviews", "record": "credentials_recognition"},
}

# One plain sentence per pillar, shown under the score bars.
PILLAR_BLURBS: dict[str, str] = {
    "Practitioner Credentials & Clinical Quality": "Board certification, training and quality signals assistants can verify for your physicians.",
    "Reviews & Reputation": "Google and third-party ratings, review volume and what patients say about you.",
    "Identity & Machine-Readability": "Whether assistants can read your site, tell your locations apart and tie each physician to your practice.",
    "Access & Fit": "Locations, hours, insurance, new-patient availability and the services patients ask about.",
    "Outcomes & Safety": "Leapfrog grade, CMS stars and the safety record assistants cite.",
    "Quality & Coordination": "Care coordination, continuity and the quality signals assistants cite.",
    "Credentials & Recognition": "Rankings, accreditations, designations and academic affiliation.",
    "Experience & Reviews": "Google ratings, review volume and patient voice across your locations.",
    "Services & Access": "The services students look for and how easily they can get an appointment.",
    "Findability & Identity": "Whether assistants find the clinic and name it correctly for its campus.",
    "Machine-Readability & Digital Presence": "Whether the clinic's pages, hours and services can be read by AI crawlers.",
}


def pillar_blurbs(result) -> list[tuple[str, str]]:
    p = next(iter(getattr(result, "rankings", None) or []), None)
    profile = (getattr(p, "weighting_profile", None) if p else None) or getattr(result, "weighting_profile", None) or "procedural"
    labels = _tier_labels(profile)
    return [(labels[k], PILLAR_BLURBS.get(labels[k], "")) for k in
            ("clinical_outcomes_safety", "credentials_recognition", "patient_experience_reviews", "access_fit")]


def _rubric(result) -> str:
    et = getattr(result, "entity_type", None) or "hospital"
    return "practice" if et in ("practice", "service_line") else "hospital"


def _finding_for(findings, platform: str, *words: str) -> Optional[str]:
    """Finding id on the given platform (optionally whose summary mentions one of `words`)."""
    for f in (getattr(findings, "findings", None) or []):
        d = f.model_dump() if hasattr(f, "model_dump") else f
        if d.get("platform") != platform:
            continue
        text = (d.get("teaser_summary") or "").lower()
        if not words or any(w in text for w in words):
            return d.get("finding_id")
    return None


def _reviews(result) -> tuple[Optional[int], Optional[int]]:
    """(total reviews, locations) from the best source the run has."""
    p = next(iter(getattr(result, "rankings", None) or []), None)
    for cr in getattr(result, "practice_composite_rows", None) or []:
        if cr.get("is_anchor") and cr.get("total_reviews"):
            n_loc = 1 + sum(1 for r in result.practice_composite_rows if not r.get("is_anchor") and not r.get("not_established"))
            return int(cr["total_reviews"]), n_loc
    if p is not None:
        sa = p.google_footprint.system_aggregate
        if sa.available and sa.total_reviews:
            return int(sa.total_reviews), int(sa.location_count or 1)
        fd = p.google_footprint.front_door
        if fd.verified and fd.count:
            return int(fd.count), 1
    return None, None


def build_checklist(result, findings=None) -> list[dict]:
    """[{id, label, status, detail, feeds, finding}] — one row per CHECKS entry, always all ten."""
    rubric = _rubric(result)
    labels = _tier_labels((getattr(result, "weighting_profile", None)
                           or getattr(next(iter(result.rankings or []), None), "weighting_profile", None) or "procedural"))
    wf = getattr(result, "website_facts", None) or {}
    st = wf.get("status")
    measured = st == "measured"
    snap = getattr(findings, "source_snapshot", None) or {}
    has_content = findings is not None and bool(getattr(findings, "findings", None) is not None)
    rows: list[dict] = []

    def add(cid, status, detail, finding=None):
        label = next(l for i, l, _ in CHECKS if i == cid)
        feed = next(f for i, _, f in CHECKS if i == cid)
        key = _FEEDS[rubric].get(feed)
        feeds = labels[key] if key else "Content visibility (not scored)"
        rows.append({"id": cid, "label": label, "status": status, "detail": detail, "feeds": feeds, "finding": finding})

    # 1 AI-crawler access
    if st == "blocked":
        pr = wf.get("crawler_probe") or {}
        add("ai_access", FAIL, "Turned away: " + ", ".join(pr.get("blocked") or ["AI crawlers"]) + ".", _finding_for(findings, "website", "crawler", "firewall", "bot"))
    elif measured:
        add("ai_access", PASS, f"Homepage served to AI crawlers; {wf.get('pages', 0)} page(s) read.")
    elif st == "refused":
        af = wf.get("assistant_fetch") or {}
        if af.get("ok"):
            add("ai_access", PARTIAL, "Our server was refused by the site's firewall, so the crawl could not run; Claude's live fetch read the homepage.")
        else:
            add("ai_access", NA, "Our server was refused by the site's firewall (a browser request too); AI-crawler access could not be verified.")
    elif st == "unreachable":
        add("ai_access", NA, "Website unreachable at analysis time.")
    else:
        add("ai_access", NA, "Website not crawled.")

    # 2 robots.txt
    if measured:
        ok = wf.get("robots_allows_ai")
        add("robots", PASS if ok else FAIL, "No AI-crawler disallow rules." if ok else "robots.txt disallows AI crawlers.",
            None if ok else _finding_for(findings, "website", "robots"))
    elif st in ("blocked", "refused"):
        add("robots", NA, "Not readable while the firewall blocks crawlers." if st == "blocked" else "Not readable: the site refused our server.")
    else:
        add("robots", NA, "Not checked.")

    # 3–7 website facts
    def wf_check(cid, key, ok_text, bad_text, platform=None, *words):
        if measured:
            ok = bool(wf.get(key))
            add(cid, PASS if ok else FAIL, ok_text if ok else bad_text,
                None if ok or not platform else _finding_for(findings, platform, *words))
        else:
            add(cid, NA, "Not checked.")
    wf_check("org_schema", "org_schema", "MedicalOrganization / LocalBusiness schema found.", "No organization schema on the pages read.",
             "structured_data", "organization", "localbusiness", "no healthcare schema", "no schema")
    if rubric == "practice":
        wf_check("physician_schema", "physician_schema", "Physician schema found on provider pages.", "No Physician schema on provider pages.",
                 "structured_data", "physician")
        if measured:
            n = int(wf.get("physician_pages") or 0)
            add("bio_pages", PASS if n else FAIL, f"{n} crawlable provider page(s) found." if n else "No crawlable physician bio pages found.",
                None if n else _finding_for(findings, "website", "bio", "physician"))
        else:
            add("bio_pages", NA, "Not checked.")
    else:
        add("physician_schema", NA, "Practice reports only.")
        add("bio_pages", NA, "Practice reports only.")
    wf_check("sitemap", "sitemap", "sitemap.xml present.", "No XML sitemap found.", "website", "sitemap")
    wf_check("llms_txt", "llms_txt", "llms.txt published.", "No llms.txt.", "llms_txt")

    # 8 Google profiles
    au = getattr(result, "profile_audit", None) or {}
    sm = au.get("summary") or {}
    n = int(sm.get("checked") or 0)
    if n:
        parts = [("link to the site", sm.get("linked", 0)), ("list hours", sm.get("with_hours", 0)),
                 ("list a phone", sm.get("with_phone", 0)), ("have 3+ photos", sm.get("with_photos", 0))]
        full = all(int(v or 0) >= n for _, v in parts)
        none_linked = int(sm.get("linked") or 0) == 0
        status = PASS if full else (FAIL if none_linked else PARTIAL)
        add("google_profiles", status, f"{n} profile(s): " + "; ".join(f"{int(v or 0)} {t}" for t, v in parts) + ".",
            None if full else _finding_for(findings, "reputation", "profile", "listing"))
    else:
        add("google_profiles", NA, "Profiles not checked.")

    # 9 review volume
    total, n_loc = _reviews(result)
    if total is None:
        add("review_volume", NA, "No verified review count.")
    else:
        status = PASS if total >= 200 else (PARTIAL if total >= 50 else FAIL)
        add("review_volume", status, f"{total:,} Google reviews" + (f" across {n_loc} location(s)" if n_loc and n_loc > 1 else "") +
            (" (200+ reads as high evidence)." if status != PASS else "."),
            None if status == PASS else _finding_for(findings, "reputation", "review", "rating"))

    # 10 public record
    if has_content:
        qid, art = snap.get("wikidata_qid"), snap.get("wikipedia_article")
        if qid and art:
            add("public_record", PASS, f"Wikidata {qid}; Wikipedia article “{art}”.")
        elif qid or art:
            add("public_record", PARTIAL, (f"Wikidata {qid}; no Wikipedia article." if qid else f"Wikipedia “{art}”; no Wikidata item."),
                _finding_for(findings, "wikipedia") if qid else _finding_for(findings, "wikidata"))
        else:
            add("public_record", FAIL, "No Wikidata item and no Wikipedia article.", _finding_for(findings, "wikidata") or _finding_for(findings, "wikipedia"))
    else:
        add("public_record", NA, "Not checked.")
    return rows


def summarize(rows: list[dict]) -> dict:
    c = {PASS: 0, FAIL: 0, PARTIAL: 0, NA: 0}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return {"pass": c[PASS], "fail": c[FAIL], "partial": c[PARTIAL], "na": c[NA], "checked": len(rows) - c[NA]}
