"""Delivery check — flags on a finished Deep Diagnostic before the coordinator sends it.

A post-render linter over the stored result: it reads what the report actually contains and
names anything an association expects that is missing — no spot check, no physician rows, a
summary that did not take the fixed shape, a website the assistants could not read, a
specialty label that did not resolve, a benchmark that should have printed. Nothing here
changes a score; it tells the coordinator which reports to re-run or review.

Each flag: {"id", "label", "detail", "level"} with level "fix" (the report is incomplete or
the organization needs a fix before AI assistants can read it) or "warn" (worth a look).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from .specialties import CANONICAL

FIX, WARN = "fix", "warn"
STALE_DAYS = 90

# Rendered order; "fix" rows first within a report.
LABELS: dict[str, str] = {
    "no_score":     "No Pulse Score",
    "website":      "Website unreadable by AI",
    "rubric":       "Hospital rubric",
    "spotcheck":    "No spot check",
    "physicians":   "No physician rows",
    "summary":      "Summary not in fixed shape",
    "first_moves":  "No What to Do First",
    "specialty":    "Specialty label unresolved",
    "benchmark":    "Benchmark not printed",
    "roster_drift": "Roster drifted since confirmation",
    "listing_quality": "Listing details disagree",
    "stale":        f"Over {STALE_DAYS} days old",
}

# Findings about the organization (for them to fix) vs problems with the report (for the coordinator).
KIND: dict[str, str] = {"website": "finding", "listing_quality": "finding"}


def _is_practice(d: dict) -> bool:
    return (d.get("entity_type") or "hospital") in ("practice", "service_line")


def _score(d: dict) -> Optional[int]:
    p0 = (d.get("rankings") or [{}])[0] or {}
    return p0.get("ai_visibility_score")


def _physician_count(d: dict) -> int:
    pf = d.get("physician_facts") or {}
    n = len(pf.get("rows") or []) if pf.get("status") == "measured" else 0
    for cr in d.get("practice_composite_rows") or []:
        n += len(cr.get("physicians") or [])
    n += len(d.get("physician_composite_rows") or [])
    return n


def _age_days(d: dict) -> Optional[int]:
    g = d.get("generated_at")
    if not g:
        return None
    try:
        gd = date.fromisoformat(str(g)[:10])
    except Exception:
        return None
    return (date.today() - gd).days


def check_result(d: dict, *, group: Optional[dict] = None, benchmark_ready: bool = False,
                 require_spotcheck: bool = True) -> list[dict]:
    """Flags for one stored result (the parsed result_json dict).

    `group` is the group the report sits in (type_hint decides the rubric check);
    `benchmark_ready` says the group prints a benchmark, so a report without one is flagged;
    `require_spotcheck` is off for groups whose preset hides the spot check.
    """
    flags: list[dict] = []
    practice = _is_practice(d)

    def add(fid: str, level: str, detail: str) -> None:
        # kind: "report" = the coordinator can act (re-run / fix roster); "finding" = the organization's
        # problem, already written up in the PDF — informational on the group page, never "needs attention".
        flags.append({"id": fid, "label": LABELS[fid], "detail": detail, "level": level, "kind": KIND.get(fid, "report")})

    if _score(d) is None:
        add("no_score", FIX, "The run finished without a Pulse Score; re-run it.")

    wf = d.get("website_facts") or {}
    st = wf.get("status")
    if st == "blocked":
        pr = wf.get("crawler_probe") or {}
        add("website", FIX, "The site's firewall turns AI crawlers away" + (": " + ", ".join(pr["blocked"]) if pr.get("blocked") else "") + ". Nothing it publishes reaches AI answers until this is fixed.")
    elif st == "refused":
        af = wf.get("assistant_fetch") or {}
        add("website", WARN, "The site refused our server, so the crawl could not run" + ("; Claude's live fetch did read the homepage." if af.get("ok") else " and AI access could not be verified."))
    elif st == "unreachable":
        add("website", WARN, "The website was unreachable at analysis time; confirm the URL and re-run.")
    elif practice and st in (None, "skipped"):
        add("website", WARN, "The website was not crawled for this run; re-run with a website to measure machine-readability.")

    if group and group.get("type_hint") == "practice" and not practice:
        add("rubric", FIX, "Run on the hospital rubric inside a practice group; Re-run produces it as a Specialty Practice.")

    sc = d.get("spotcheck") or {}
    if require_spotcheck and not sc.get("asked"):
        add("spotcheck", WARN, "The AI assistants were not asked what they say about this organization; re-run with the spot check on.")

    if practice and _physician_count(d) == 0:
        add("physicians", WARN, "No physicians were found for this organization, so the Physicians table is empty; check the listing and website.")

    es = d.get("executive_summary_structured") or {}
    bullets = [b for b in (es.get("bullets") or []) if str(b).strip()]
    if not (es.get("headline") and 1 <= len(bullets) <= 4):
        if (d.get("ai_visibility_verdict") or "").strip():
            add("summary", WARN, "The Executive Summary fell back to a sentence split instead of the headline + bullets shape.")
        else:
            add("summary", FIX, "The report has no Executive Summary.")

    if not (d.get("top_recommendation") or "").strip():
        add("first_moves", WARN, "The report has no What to Do First section.")

    if practice:
        spec = (d.get("specialty") or "").strip()
        if not spec:
            add("specialty", WARN, "No specialty on the report; the cover and the spot-check question bank default to a generic practice.")
        elif spec not in CANONICAL:
            add("specialty", WARN, f"“{spec}” is not one of the controlled specialty labels; the cover prints it as typed.")

    lr, pr = d.get("location_resolution") or {}, d.get("physician_resolution") or {}
    bits = []
    for label, res in (("office", lr), ("physician", pr)):
        dr = res.get("drift") or {}
        if dr.get("new"):
            bits.append(f"{len(dr['new'])} {label}{'s' if len(dr['new']) != 1 else ''} found that are not on the confirmed roster ({', '.join(dr['new'][:5])}{'…' if len(dr['new']) > 5 else ''})")
        if dr.get("missing"):
            bits.append(f"{len(dr['missing'])} confirmed {label}{'s' if len(dr['missing']) != 1 else ''} not found this run ({', '.join(dr['missing'][:5])}{'…' if len(dr['missing']) > 5 else ''})")
    if bits:
        add("roster_drift", WARN, "; ".join(bits) + ". Open Fix roster to confirm or drop them, then re-run.")

    lq = d.get("listing_quality") or {}
    if lq:
        from .listing_quality import issues as _lq_issues
        probs = _lq_issues(lq)
        if probs:
            closed = (lq.get("status") or {}).get("closed") or []
            add("listing_quality", FIX if closed else WARN, " ".join(probs[:3]) + (" …" if len(probs) > 3 else ""))

    if benchmark_ready and not ((d.get("group_context") or {}).get("ready")):
        add("benchmark", WARN, "The group now prints a rank and median, but this report predates that; re-run to include the benchmark.")

    age = _age_days(d)
    if age is not None and age > STALE_DAYS:
        add("stale", WARN, f"Report generated {age} days ago; worth a re-run before sending.")

    flags.sort(key=lambda f: (0 if f["level"] == FIX else 1, list(LABELS).index(f["id"])))
    return flags


def spotcheck_required(group: Optional[dict]) -> bool:
    """A group whose preset hides the spot check does not expect one on its reports."""
    if not group or not group.get("preset"):
        return True
    try:
        from .presets import PRESETS
        return (PRESETS.get(group["preset"]) or {}).get("options", {}).get("spotcheck", "on") != "hidden"
    except Exception:
        return True


def summarize(members: list[dict]) -> dict:
    """Counts for the group page strip: reports with a report-level flag (coordinator action), with a 'fix'
    flag, organizations with findings, and per flag id."""
    per: dict[str, int] = {}
    any_n = fix_n = findings_n = 0
    for m in members:
        fl = m.get("flags") or []
        rep = [f for f in fl if f.get("kind", "report") == "report"]
        if rep:
            any_n += 1
        if any(f["level"] == FIX for f in rep):
            fix_n += 1
        if any(f.get("kind") == "finding" for f in fl):
            findings_n += 1
        for f in fl:
            per[f["id"]] = per.get(f["id"], 0) + 1
    return {"flagged": any_n, "fix": fix_n, "findings": findings_n, "by_flag": per}
