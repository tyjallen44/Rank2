"""Citation gaps: turn the observed spot-check's cited pages into prescription evidence.

For every patient question the assistants answered we know (a) whether the organization was
named and (b) which pages the answer drew on. This module folds that into:

  • by_question — one row per question: named by whom, the domains cited, and whether the
                  organization's own site was among them
  • sourcing    — the customer takeaway ("Your site was cited for 3 of 8 questions, all when
                  you were asked about by name; when patients are choosing, assistants relied
                  on healthgrades.com, health.usnews.com and atriumhealth.org")
  • gaps        — third-party domains assistants relied on for the CHOOSING questions (not the
                  direct one) where the organization was not cited, with the assistants and
                  question counts — each mapped to the roadmap item / content finding it
                  justifies ("Why this matters: …")

Nothing here changes a score. It only attaches observed evidence to fixes already prescribed.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

QUESTION_LABELS = {
    "best_in_market": "Best in the market", "condition": "A condition or procedure", "near_me": "Near me",
    "insurance": "Insurance / coverage", "urgency": "Urgent or same-day", "direct": "About you, by name",
    "compare": "Comparison", "spanish": "In Spanish",
}

# Third-party domains → words that identify the roadmap item / finding that addresses them.
DOMAIN_KEYWORDS = {
    "healthgrades.com": ["healthgrades"], "vitals.com": ["vitals"], "doctor.webmd.com": ["webmd"], "webmd.com": ["webmd"],
    "yelp.com": ["yelp"], "health.usnews.com": ["u.s. news", "us news", "usnews"], "usnews.com": ["u.s. news", "us news", "usnews"],
    "en.wikipedia.org": ["wikipedia"], "wikipedia.org": ["wikipedia"], "wikidata.org": ["wikidata"],
    "google.com": ["google business", "google profile", "business profile", "google listing", "gbp"],
    "maps.google.com": ["google business", "business profile"], "medicare.gov": ["care compare", "cms"],
    "leapfroggroup.org": ["leapfrog"], "hospitalsafetygrade.org": ["leapfrog"], "zocdoc.com": ["zocdoc"],
    "facebook.com": ["facebook"], "threebestrated.com": ["three best rated", "directory"], "castleconnolly.com": ["castle connolly"],
    "npiregistry.cms.hhs.gov": ["nppes", "npi"], "reddit.com": ["reddit", "forum"],
}


def _dom(u: str) -> str:
    try:
        return (urlparse(u if "://" in u else "https://" + u).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _ours(d: str, our_domain: str | None) -> bool:
    return bool(our_domain) and (d == our_domain or d.endswith("." + our_domain))


def analyze(sc: dict) -> dict:
    """Add by_question / sourcing / gaps to a spot-check result (in place) and return it."""
    results = [r for r in (sc.get("results") or []) if "error" not in r]
    our = sc.get("our_domain")
    order: list[str] = []
    rows: dict[str, dict] = {}
    for r in results:
        k = r.get("key") or ""
        if k not in rows:
            order.append(k)
            rows[k] = {"key": k, "label": QUESTION_LABELS.get(k, k.replace("_", " ").title()), "query": r.get("query") or "",
                       "asked": 0, "named_by": [], "domains": {}, "ours_cited": False, "ours_by": []}
        row = rows[k]
        row["asked"] += 1
        if r.get("mentioned"):
            row["named_by"].append(r.get("assistant"))
        for u in r.get("citations") or []:
            d = _dom(u)
            if not d:
                continue
            row["domains"][d] = row["domains"].get(d, 0) + 1
            if _ours(d, our):
                row["ours_cited"] = True
                if r.get("assistant") not in row["ours_by"]:
                    row["ours_by"].append(r.get("assistant"))
    by_q = []
    for k in order:
        row = rows[k]
        top = sorted(row["domains"].items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        by_q.append({**row, "named": bool(row["named_by"]),
                     "domains": [{"domain": d, "count": c, "ours": _ours(d, our)} for d, c in top]})
    sc["by_question"] = by_q
    # Per-category roll-up (the PDF table): questions, answers, answers naming us, our site cited.
    from .spotcheck_bank import CATEGORIES
    cats: dict[str, dict] = {}
    for r in results:
        c = r.get("category") or ("direct" if r.get("key") == "direct" else "other")
        g = cats.setdefault(c, {"key": c, "label": CATEGORIES.get(c, c.title()), "questions": set(), "answers": 0, "named": 0,
                                "ours_q": set(), "domains": {}, "named_by": {}})
        g["questions"].add(r.get("key")); g["answers"] += 1
        if r.get("mentioned"):
            g["named"] += 1; g["named_by"][r.get("assistant")] = g["named_by"].get(r.get("assistant"), 0) + 1
        for u in r.get("citations") or []:
            d = _dom(u)
            if d:
                g["domains"][d] = g["domains"].get(d, 0) + 1
                if _ours(d, our):
                    g["ours_q"].add(r.get("key"))
    order_c = list(CATEGORIES.keys()) + [c for c in cats if c not in CATEGORIES]
    sc["by_category"] = [{"key": c, "label": g["label"], "questions": len(g["questions"]), "answers": g["answers"], "named": g["named"],
                          "named_pct": round(100 * g["named"] / g["answers"]) if g["answers"] else 0,
                          "ours_questions": len(g["ours_q"]), "named_by": g["named_by"],
                          "domains": [{"domain": d, "count": k, "ours": _ours(d, our)} for d, k in sorted(g["domains"].items(), key=lambda kv: (-kv[1], kv[0]))[:4]]}
                         for c in order_c if (g := cats.get(c))]

    n = len(by_q)
    cited_rows = [q for q in by_q if q["ours_cited"]]
    choosing = [q for q in by_q if q["key"] != "direct"]
    choosing_uncited = [q for q in choosing if not q["ours_cited"]]
    relied: dict[str, dict] = {}
    for q in choosing_uncited:
        for d in q["domains"]:
            if d["ours"]:
                continue
            g = relied.setdefault(d["domain"], {"domain": d["domain"], "questions": 0, "citations": 0, "assistants": set(), "question_keys": []})
            g["questions"] += 1; g["citations"] += d["count"]; g["question_keys"].append(q["key"])
    for q in choosing_uncited:
        for r in results:
            if r.get("key") == q["key"]:
                for u in r.get("citations") or []:
                    d = _dom(u)
                    if d in relied:
                        relied[d]["assistants"].add(r.get("assistant"))
    gaps = sorted(relied.values(), key=lambda g: (-g["questions"], -g["citations"], g["domain"]))[:8]
    for g in gaps:
        g["assistants"] = sorted(a for a in g["assistants"] if a)
    sc["gaps"] = gaps

    only_direct = bool(cited_rows) and all(q["key"] == "direct" for q in cited_rows)
    top3 = [g["domain"] for g in gaps[:3]]
    if not our:
        sentence = ""
    elif not cited_rows:
        sentence = (f"The AI assistants did not use your website {our} in their answers to any of the {n} questions"
                    + (f"; they relied on {_join(top3)} instead." if top3 else "."))
    else:
        sentence = f"The AI assistants used your website {our} in their answers for {len(cited_rows)} of {n} questions"
        sentence += ", all of them when they were asked about you by name." if only_direct else "."
        if choosing_uncited and top3:
            sentence += (f" For the {len(choosing_uncited)} question{'s' if len(choosing_uncited) != 1 else ''} where patients are choosing "
                         f"and your site was not used, they relied on {_join(top3)}.")
    sc["sourcing"] = {"our_domain": our, "cited_questions": len(cited_rows), "questions": n,
                      "only_direct": only_direct, "choosing_uncited": len(choosing_uncited),
                      "relied_on": top3, "sentence": sentence}
    return sc


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _reason_for(gap: dict, n_questions: int) -> str:
    who = _join(gap.get("assistants") or [])
    return (f"Why this matters: {who or 'assistants'} cited {gap['domain']} for {gap['questions']} of {n_questions} "
            f"patient questions where you were not cited.")


def _match_gap(text: str, gaps: list[dict]) -> dict | None:
    t = (text or "").lower()
    for g in gaps:
        for kw in DOMAIN_KEYWORDS.get(g["domain"], []):
            if kw in t:
                return g
    return None


def attach_roadmap_reasons(result) -> int:
    """Append 'Why this matters: …' to roadmap items that address a domain assistants relied on.
    Returns the number of items annotated. Idempotent."""
    sc = getattr(result, "spotcheck", None) or {}
    gaps, n = sc.get("gaps") or [], (sc.get("sourcing") or {}).get("questions") or 0
    if not gaps or not n:
        return 0
    count = 0
    for sec in getattr(result, "improvement_sections", None) or []:
        new_items = []
        for item in sec.items or []:
            if "Why this matters:" in item:
                new_items.append(item); continue
            g = _match_gap(item, gaps)
            if g:
                item = item.rstrip() + " " + _reason_for(g, n); count += 1
            new_items.append(item)
        sec.items = new_items
    return count


def attach_finding_reasons(findings: list, sc: dict | None) -> int:
    """Set why_it_matters on content findings (models or dicts) that a citation gap justifies.
    Website / structured-data findings get the sourcing sentence when the site was under-cited;
    third-party platform findings get the matching gap. Returns the number annotated."""
    sc = sc or {}
    gaps, src = sc.get("gaps") or [], sc.get("sourcing") or {}
    n = src.get("questions") or 0
    if not n:
        return 0
    count = 0
    for f in findings or []:
        get = (lambda k: f.get(k)) if isinstance(f, dict) else (lambda k: getattr(f, k, None))
        if get("why_it_matters"):
            continue
        text = " ".join(str(get(k) or "") for k in ("teaser_summary", "current_state", "expected_state", "platform"))
        g = _match_gap(text, gaps)
        why = ""
        if g:
            why = _reason_for(g, n)
        elif (get("platform") in ("website", "structured_data", "llms_txt")) and src.get("choosing_uncited"):
            why = ("Why this matters: " + src["sentence"]) if src.get("sentence") else ""
        elif get("platform") in ("wikipedia", "wikidata") and any(d in ("en.wikipedia.org", "wikipedia.org", "wikidata.org") for d in [g2["domain"] for g2 in gaps]):
            why = _reason_for(next(g2 for g2 in gaps if "wiki" in g2["domain"]), n)
        if why:
            if isinstance(f, dict):
                f["why_it_matters"] = why
            else:
                f.why_it_matters = why
            count += 1
    return count
