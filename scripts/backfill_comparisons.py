#!/usr/bin/env python3
"""Backfill History rows for Compare Two PDFs generated before comparisons were persisted.

Scans REPORTS_DIR for Compare-Two_<A>_Vs_<B>_<runid8>.pdf, recovers the side-A run from the
8-char run_id prefix (date, role, ran_by, location, specialty) and inserts a comparison_runs
row for each file not already recorded. Dry run by default.
Usage: .venv/bin/python scripts/backfill_comparisons.py [--apply]
"""
import re, sys, pathlib, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(str(pathlib.Path(__file__).resolve().parents[1] / ".env"))

def main(apply: bool) -> None:
    import server                                   # REPORTS_DIR resolved the same way the app does
    from perception.db import init_db, get_connection
    from perception.strings import FILE_COMPARISON_PFX
    init_db()
    con = get_connection()
    known = {r[0] for r in con.execute("SELECT pdf_path FROM comparison_runs").fetchall()}
    pat = re.compile(rf"^{re.escape(FILE_COMPARISON_PFX)}_(.+)_Vs_(.+?)_+([0-9a-f]{{8}})\.pdf$", re.I)
    added = skipped = 0
    for f in sorted(server.REPORTS_DIR.glob(f"{FILE_COMPARISON_PFX}*.pdf")):
        if str(f) in known:
            skipped += 1
            continue
        m = pat.match(f.name)
        if not m:
            print(f"  ? unrecognised name: {f.name}")
            continue
        a, b, pfx = m.group(1).replace("_", " ").strip(), m.group(2).replace("_", " ").strip(), m.group(3)
        row = con.execute(
            "SELECT run_id, generated_at, user_role, ran_by, location, specialty, entity_name "
            "FROM analysis_runs WHERE run_id LIKE ? ORDER BY generated_at DESC LIMIT 1", [pfx + "%"]).fetchone()
        run_id_a, gen, role, ran_by, loc_a, spec, ent_a = row if row else (None, None, "admin", None, None, None, None)
        if not gen:
            from datetime import datetime
            gen = datetime.fromtimestamp(f.stat().st_mtime).date()
        print(f"  {'ADD ' if apply else 'would add'}: {a} vs {b}  ({gen}, {role or 'admin'}, side-A run {run_id_a or 'unknown'})")
        if apply:
            from datetime import datetime
            con.execute(
                """INSERT INTO comparison_runs (id, run_id_a, run_id_b, entity_a, entity_b, location_a, location_b,
                                                specialty, pdf_path, teaser, user_role, ran_by, generated_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?)""",
                [uuid.uuid4().hex[:12], run_id_a, None, ent_a or a, b, loc_a, None, spec, str(f),
                 role or "admin", ran_by, gen, datetime.combine(gen, datetime.min.time())])
        added += 1
    con.close()
    print(f"\n{'Added' if apply else 'Would add'} {added}, already recorded {skipped}." + ("" if apply else "  Re-run with --apply."))

if __name__ == "__main__":
    main("--apply" in sys.argv)
