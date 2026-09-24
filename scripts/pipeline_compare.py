#!/usr/bin/env python
"""Side-by-side comparison: legacy analyzer vs. unified pipeline for one entity.

    .venv/bin/python scripts/pipeline_compare.py --type practice \
        --name "Orthocarolina Sports Medicine Center" --city Charlotte --state NC \
        --specialty "Sports Medicine"

Runs the legacy function first, then perception.pipeline.run_individual, both with
override_today_lock=True, force_rerun=True, skip_pdf=True, briefing_variant=None
(REAL API calls — roughly the cost of two Deep Diagnostics). Prints a comparison
table, then deletes the two runs it created from analysis_runs / ranked_providers
(and the FQHC extras tables) and restores today's canonical entity_scores row(s)
that the runs overwrote.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

_COMMON = dict(override_today_lock=True, force_rerun=True, skip_pdf=True, briefing_variant=None)
_PILLARS = ("clinical_outcomes_safety", "credentials_recognition", "patient_experience_reviews", "access_fit")


def _run_legacy(etype: str, name: str, city: str, state: str, specialty, out: Path):
    if etype == "community_health":
        from perception.fqhc_analyzer import analyze_fqhc
        return analyze_fqhc(entity_name=name, city=city, state=state, output_dir=out, **_COMMON)
    if etype in ("practice", "service_line"):
        from perception.practice_analyzer import analyze_practice
        return analyze_practice(entity_name=name, city=city, state=state, specialty=specialty,
                                output_dir=out, **_COMMON)
    from perception.analyzer import analyze_location
    return analyze_location(city=city, state=state, specialty=specialty, entity_name=name,
                            individual_report=True, entity_type="hospital", output_dir=out, **_COMMON)


def _run_unified(etype: str, name: str, city: str, state: str, specialty, out: Path):
    from perception.pipeline import run_individual
    return run_individual(etype, name, city, state, specialty=specialty, output_dir=out, **_COMMON)


def _saved_entity_type(run_id: str):
    from perception.db import get_connection
    con = get_connection()
    row = con.execute("SELECT entity_type FROM analysis_runs WHERE run_id = ?", [run_id]).fetchone()
    con.close()
    return row[0] if row else "(no row)"


def _snapshot_entity_scores(name: str, location: str) -> list:
    from perception.db import get_connection, _norm_entity_name, _norm_location
    con = get_connection()
    cur = con.execute("SELECT * FROM entity_scores WHERE norm_name = ? AND location = ? AND generated_at = ?",
                      [_norm_entity_name(name), _norm_location(location), date.today().isoformat()])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close()
    return rows


def _cleanup(run_ids: list, snapshot: list) -> None:
    from perception.db import get_connection
    con = get_connection()
    for rid in run_ids:
        for table in ("ranked_providers", "fqhc_intake", "fqhc_fact_audit", "fqhc_battery_results", "analysis_runs"):
            try:
                con.execute(f"DELETE FROM {table} WHERE run_id = ?", [rid])
            except Exception:
                pass
        try:
            con.execute("DELETE FROM entity_scores WHERE run_id = ?", [rid])
        except Exception:
            pass
    for row in snapshot:   # restore today's canonical row the runs overwrote
        cols = list(row)
        con.execute(
            f"INSERT INTO entity_scores ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            "ON CONFLICT (norm_name, location, generated_at) DO NOTHING",
            [row[c] for c in cols],
        )
    con.close()


def _summarize(res) -> dict:
    p = res.rankings[0] if res.rankings else None
    d = {
        "run_id": res.run_id,
        "weighting_profile": res.weighting_profile,
        "composite": p.ai_visibility_score if p else None,
    }
    for k in _PILLARS:
        d[k] = getattr(p.tier_scores, k) if p else None
    if res.fqhc_pillar_scores is not None:
        for k, v in res.fqhc_pillar_scores.as_dict().items():
            d[f"fqhc.{k}"] = v
    d["rankings"] = len(res.rankings)
    d["consolidated_locations"] = len(p.consolidated_locations) if p else 0
    d["sources"] = len(res.sources_consulted or [])
    d["entity_type (saved row)"] = _saved_entity_type(res.run_id)
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, choices=["hospital", "practice", "service_line", "community_health"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--specialty", default=None)
    ap.add_argument("--keep", action="store_true", help="do not delete the two runs afterwards")
    args = ap.parse_args()

    from perception.db import init_db
    init_db()
    import tempfile
    out = Path(tempfile.mkdtemp(prefix="pipeline_compare_"))   # markdown only (skip_pdf); outside the repo
    print(f"report markdown → {out}", file=sys.stderr)
    location = f"{args.city}, {args.state}"
    snapshot = _snapshot_entity_scores(args.name, location)

    run_ids: list = []
    results = {}
    try:
        for label, fn in (("legacy", _run_legacy), ("unified", _run_unified)):
            print(f"\n=== {label} ===", file=sys.stderr)
            t0 = time.time()
            res = fn(args.type, args.name, args.city, args.state, args.specialty, out)
            run_ids.append(res.run_id)
            results[label] = _summarize(res)
            results[label]["seconds"] = round(time.time() - t0)
    finally:
        # Print whatever we have, then clean up.
        keys: list = []
        for d in results.values():
            for k in d:
                if k not in keys:
                    keys.append(k)
        if results:
            w = max(len(k) for k in keys) + 2
            print("\n" + "metric".ljust(w) + "legacy".ljust(28) + "unified".ljust(28) + "delta")
            print("-" * (w + 64))
            for k in keys:
                a = results.get("legacy", {}).get(k)
                b = results.get("unified", {}).get(k)
                delta = ""
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
                    delta = f"{b - a:+}"
                print(k.ljust(w) + str(a).ljust(28) + str(b).ljust(28) + delta)
        if run_ids and not args.keep:
            _cleanup(run_ids, snapshot)
            print(f"\nDeleted {len(run_ids)} run(s) and restored {len(snapshot)} canonical entity_scores row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
