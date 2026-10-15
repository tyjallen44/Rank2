"""Headline counts of a stored Deep Diagnostic result — the numbers the report-quality cleanup
tracks per fixture (tests/fixtures/cleanup). Used by the golden baseline test and by
`python scripts/fixture_replay.py` to print a before/after table.

Usage: python scripts/fixture_replay.py [key …]   (keys: gco dmos orthosouth ibji)"""
from __future__ import annotations

import json, pathlib, sys

FX = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cleanup"


def headline(res: dict) -> dict:
    p = (res.get("rankings") or [{}])[0]
    rows = res.get("practice_composite_rows") or []
    pf = res.get("physician_facts") or {}
    lr = res.get("location_resolution") or {}
    au = (res.get("profile_audit") or {}).get("summary") or {}
    sc = res.get("spotcheck") or {}
    tiers = p.get("tier_scores") or {}
    return {
        "score": p.get("ai_visibility_score"),
        "tiers": {k: tiers.get(k) for k in ("clinical_outcomes_safety", "credentials_recognition", "patient_experience_reviews", "access_fit")},
        "ceiling": bool(p.get("score_ceiling_applied")),
        "locations_composite": len(rows),
        "locations_established": sum(1 for r in rows if not r.get("not_established")),
        "location_names": sorted(r.get("practice_name") or "" for r in rows),
        "resolver_total": lr.get("total"),
        "resolver_rejected": len(lr.get("rejected") or []),
        "profiles_checked": au.get("checked"), "profiles_linked": au.get("linked"), "google_reviews_profiles": au.get("total_reviews"),
        "physicians_checked": pf.get("checked"), "physicians_linked": pf.get("linked"), "linkage_pct": pf.get("linkage_pct"),
        "cert_checked": pf.get("cert_checked"), "cert_stated": pf.get("cert_stated"),
        "physician_names": sorted((r.get("name") or "") for r in (pf.get("rows") or [])),
        "spotcheck_named": f"{sc.get('unprompted_mentioned')} of {sc.get('unprompted_asked')}" if sc else None,
        "spotcheck_errors": {a.get("assistant"): a.get("errors") for a in (sc.get("per_assistant") or [])} if sc else None,
        "website_cited": sc.get("our_domain_cited") if sc else None,
    }


def main(keys: list[str]) -> int:
    for key in keys or ("gco", "dmos", "orthosouth", "ibji"):
        f = FX / f"{key}.json"
        if not f.exists():
            print(f"{key}: not captured"); continue
        h = headline(json.loads(f.read_text())["result"])
        print(f"\n== {key}")
        for k, v in h.items():
            if k in ("location_names", "physician_names"):
                print(f"  {k} ({len(v)}): {', '.join(v)[:400]}")
            else:
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
