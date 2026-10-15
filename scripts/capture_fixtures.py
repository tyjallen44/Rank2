"""Capture the four report-quality regression fixtures from production.

Usage:  PULSE_TOKEN=<admin jwt> python scripts/capture_fixtures.py
Writes tests/fixtures/cleanup/<key>.json (stored AnalysisResult + content findings) for the
runs listed in FIXTURES. Needs GET /api/admin/runs/{run_id}/result.json on the server."""
import json, os, sys, pathlib
import requests

BASE = os.environ.get("PULSE_BASE", "https://rank2-883710187036.us-central1.run.app")
FIXTURES = {
    "gco":        "c9957013-a91f-42f4-8ccf-c9ef6316cd29",   # Georgia Carolina Orthopedics, 2026-10-06, 63
    "dmos":       "78df5fe7-3b63-4728-813f-6980167a3185",   # DMOS Orthopaedic Centers, 2026-10-06, 64
    "orthosouth": "2728588f-14b5-48de-a837-d029384365ff",   # OrthoSouth, 2026-10-12, 79
    "ibji":       "4ef87f53-e2f0-4c12-9e52-281d026e7d4f",   # IBJI, 2026-10-14, 69
}
OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cleanup"


def main() -> int:
    tok = os.environ.get("PULSE_TOKEN") or ""
    if not tok:
        print("PULSE_TOKEN is required", file=sys.stderr); return 2
    OUT.mkdir(parents=True, exist_ok=True)
    for key, rid in FIXTURES.items():
        r = requests.get(f"{BASE}/api/admin/runs/{rid}/result.json", headers={"Authorization": f"Bearer {tok}"}, timeout=120)
        if not r.ok:
            print(f"{key}: {r.status_code} {r.text[:120]}", file=sys.stderr); continue
        (OUT / f"{key}.json").write_text(json.dumps(r.json(), indent=1, sort_keys=True))
        res = r.json()["result"]
        p = (res.get("rankings") or [{}])[0]
        print(f"{key}: score={p.get('ai_visibility_score')} tiers={p.get('tier_scores')} rows={len(res.get('practice_composite_rows') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
