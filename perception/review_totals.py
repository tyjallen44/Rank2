"""One canonical set of review totals per report (report-quality cleanup 1.8).

Every section that prints a review count reads these, so the checklist, the Evidence line and the
cover can never disagree. Google totals come from the verified roster profiles (Place Details);
the analyzed listing is reported separately and labelled as such."""
from __future__ import annotations


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def compute(result) -> dict:
    """{google_total, google_locations, anchor_google, anchor_all_platforms, all_platform_total,
    all_platform_locations, source}. Zeros when nothing is verified."""
    au = getattr(result, "profile_audit", None) or {}
    profiles = [p for p in (au.get("profiles") or []) if p.get("found")]
    rows = [r for r in (getattr(result, "practice_composite_rows", None) or []) if not r.get("not_established")]
    anchor_row = next((r for r in rows if r.get("is_anchor")), None)
    out = {"google_total": 0, "google_locations": 0, "anchor_google": None, "anchor_all_platforms": None,
           "all_platform_total": sum(_int(r.get("total_reviews")) for r in rows), "all_platform_locations": len(rows), "source": "none"}
    if anchor_row:
        out["anchor_all_platforms"] = _int(anchor_row.get("total_reviews")) or None
        out["anchor_google"] = _int(anchor_row.get("google_count")) or None
    if profiles:
        out["google_total"] = sum(_int(p.get("review_count")) for p in profiles)
        out["google_locations"] = sum(1 for p in profiles if p.get("review_count") is not None)
        out["source"] = "profile_audit"
        a = next((p for p in profiles if p.get("is_anchor")), None)
        if a and a.get("review_count") is not None:
            out["anchor_google"] = _int(a.get("review_count"))
        return out
    g_rows = [r for r in rows if r.get("google_count")]
    if g_rows:
        out["google_total"] = sum(_int(r.get("google_count")) for r in g_rows)
        out["google_locations"] = len(g_rows)
        out["source"] = "composite"
        return out
    p = next(iter(getattr(result, "rankings", None) or []), None)
    if p is not None:
        sa = p.google_footprint.system_aggregate
        if sa.available and _int(sa.total_reviews):
            out.update(google_total=_int(sa.total_reviews), google_locations=max(1, _int(sa.location_count)), source="system_aggregate")
            return out
        fd = p.google_footprint.front_door
        if fd.verified and _int(fd.count):
            out.update(google_total=_int(fd.count), google_locations=1, anchor_google=_int(fd.count), source="front_door")
    return out


def google_phrase(t: dict) -> str:
    """'7,875 Google reviews across 10 profiles (analyzed listing: 758)'."""
    if not t.get("google_total"):
        return ""
    s = f"{t['google_total']:,} Google reviews"
    if t.get("google_locations", 0) > 1:
        s += f" across {t['google_locations']} profiles"
    if t.get("anchor_google") and t.get("google_locations", 0) > 1 and t["anchor_google"] != t["google_total"]:
        s += f" (analyzed listing: {t['anchor_google']:,})"
    return s
