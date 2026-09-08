#!/usr/bin/env python3
"""Re-render a Content Analysis report from CACHED data — no re-analysis.

Rebuilds the PDF(s) for an existing content-analysis run using the findings +
service-line scorecards already stored in the DB, overwriting the same file
paths (so the History download link serves the updated PDF). Useful after a
rendering/label change.

Usage:
    python scripts/rerender_content_report.py                # list recent runs
    python scripts/rerender_content_report.py <ca_id>        # re-render that run
    python scripts/rerender_content_report.py latest         # re-render the newest run
"""
import json
import sys

from perception.db import (init_db, get_content_analysis_run, get_content_findings,
                            list_content_analysis_runs, get_recent_service_line_analysis,
                            get_recent_network_run, _norm_entity_name)
from perception.models import (ContentFinding, ContentFindings, ServiceLineSummary,
                               ServiceLineScorecardSet, NetworkResult)


def _service_line_payload(entity_name):
    cached = get_recent_service_line_analysis(_norm_entity_name(entity_name), days=3650)
    if not cached:
        return None
    d = json.loads(cached)
    return (ServiceLineSummary(**d["summary"]), ServiceLineScorecardSet(**d["cards"]).scorecards)


def rerender(ca_id):
    rec = get_content_analysis_run(ca_id)
    if not rec:
        print(f"No content-analysis run found for {ca_id!r}")
        return
    cf = get_content_findings(rec.get("base_run_id"))
    if not cf:
        print(f"No cached findings for base_run_id {rec.get('base_run_id')!r}")
        return
    findings = ContentFindings(
        run_id=rec.get("base_run_id") or "", source_snapshot=cf.get("source_snapshot") or {},
        status=cf.get("status", "verified"),
        findings=[ContentFinding(**f) for f in (cf.get("findings") or [])])
    name = rec.get("entity_name") or ""
    loc = rec.get("location") or ""
    title = rec.get("report_title") or name

    # Report 2 — the detailed content report (where the labels live).
    if rec.get("report2_path"):
        from perception.content_report_pdf import render_content_report_pdf
        render_content_report_pdf(name, loc, findings, rec["report2_path"],
                                  report_title=title,
                                  service_line=_service_line_payload(name))
        print(f"  ✓ Report 2 → {rec['report2_path']}")

    # Report 1 — network deep dive (needs the cached NetworkResult).
    if rec.get("report1_path") and rec.get("entity_type") == "network":
        nr = get_recent_network_run(name, days=3650)
        if nr and nr.get("result_json"):
            from perception.network_pdf import render_content_network
            result = NetworkResult.model_validate_json(nr["result_json"])
            render_content_network(result, rec["report1_path"], findings)
            print(f"  ✓ Report 1 → {rec['report1_path']}")
        else:
            print("  ⚠ Report 1 skipped (no cached NetworkResult for this system)")


def main():
    init_db()
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not arg:
        rows = list_content_analysis_runs(20)
        print("Recent content-analysis runs (pass an id to re-render):\n")
        for r in rows:
            print(f"  {r.get('id')}  {str(r.get('created_at'))[:19]}  "
                  f"{r.get('entity_name')}  [{r.get('status')}]")
        return
    if arg == "latest":
        rows = list_content_analysis_runs(1)
        if not rows:
            print("No runs found.")
            return
        arg = rows[0]["id"]
    print(f"Re-rendering {arg}…")
    rerender(arg)
    print("Done.")


if __name__ == "__main__":
    main()
