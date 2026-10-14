"""Run summary — the few fields the group page needs from a stored report, computed once.

A Deep Diagnostic's result_json is hundreds of KB; a group of 160 members used to parse all of
them (twice) on every page view. `build_summary` keeps only what `groups.members()` and the
delivery check read, in the same shape `delivery.check_result` expects, so the page reads 160
small rows. Stored in analysis_runs.summary_json at save time; older runs are backfilled the
first time a group reads them.
"""
from __future__ import annotations

import json
from typing import Any, Optional

SUMMARY_VERSION = 1


def _n(v) -> int:
    try:
        return len(v or [])
    except Exception:
        return 0


def build_summary(d: dict) -> dict:
    """Compact dict from a parsed result_json. Safe on partial / old results."""
    p0 = (d.get("rankings") or [{}])[0] or {}
    wf = d.get("website_facts") or {}
    pf = d.get("physician_facts") or {}
    es = d.get("executive_summary_structured") or {}
    lr = d.get("location_resolution") or {}
    pr = d.get("physician_resolution") or {}
    lq = d.get("listing_quality") or {}
    phys_count = (_n(pf.get("rows")) if pf.get("status") == "measured" else 0) \
        + sum(_n(cr.get("physicians")) for cr in (d.get("practice_composite_rows") or [])) \
        + _n(d.get("physician_composite_rows"))
    issues: list[str] = []
    if lq:
        try:
            from .listing_quality import issues as _issues
            issues = _issues(lq)
        except Exception:
            issues = []
    return {
        "v": SUMMARY_VERSION,
        "entity_type": d.get("entity_type"),
        "specialty": d.get("specialty"),
        "generated_at": str(d.get("generated_at") or "")[:10],
        "rankings": ([{"ai_visibility_score": p0.get("ai_visibility_score"), "tier_scores": p0.get("tier_scores") or {}}]
                     if p0.get("ai_visibility_score") is not None else []),
        "website_facts": ({"status": wf.get("status"),
                           "crawler_probe": {"blocked": list((wf.get("crawler_probe") or {}).get("blocked") or [])},
                           "assistant_fetch": {"ok": (wf.get("assistant_fetch") or {}).get("ok")}} if wf else None),
        "spotcheck": {"asked": (d.get("spotcheck") or {}).get("asked")} if d.get("spotcheck") else None,
        "physician_count": phys_count,
        "executive_summary_structured": ({"headline": (es.get("headline") or "")[:120],
                                          "bullets": ["·"] * _n([b for b in (es.get("bullets") or []) if str(b).strip()])} if es else None),
        "ai_visibility_verdict": "·" if (d.get("ai_visibility_verdict") or "").strip() else "",
        "top_recommendation": "·" if (d.get("top_recommendation") or "").strip() else "",
        "group_context": {"ready": bool((d.get("group_context") or {}).get("ready"))} if d.get("group_context") else None,
        "location_resolution": ({"drift": lr.get("drift"), "confirmed": lr.get("confirmed"), "total": lr.get("total")} if lr else None),
        "physician_resolution": ({"drift": pr.get("drift"), "confirmed": pr.get("confirmed"), "total": pr.get("total")} if pr else None),
        "listing_quality": ({"status": {"closed": list((lq.get("status") or {}).get("closed") or [])}, "issues": issues} if lq else None),
    }


def summary_from_json(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    try:
        return build_summary(json.loads(text))
    except Exception:
        return None
