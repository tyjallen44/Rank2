"""Cloud Run Jobs entrypoint — the same image, started on its own to do one batch and exit.

    python -m perception.jobs            (CMD override on the `rank2-worker` job)

Environment (set by the launcher via container overrides):
    PULSE_JOB_KIND     event      — run the pending organizations of one Event Prep / group upload
    PULSE_EVENT_ID     <event id>
    CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT   (set by Cloud Run) — this task's shard of the work

Every task takes every Nth pending organization, runs the SAME code the web service runs for an
upload (server._run_event_job, 5 at a time), writes to the same Postgres and report storage, and the
task that finishes last builds the CSV / ZIP and marks the event complete. Deploys and web traffic
cannot interrupt it; a failed task is retried by Cloud Run and only re-runs what is still pending.
"""
from __future__ import annotations

import os
import sys
import time


def shard(items: list, index: int, count: int) -> list:
    """This task's slice: every `count`-th item starting at `index` (stable across tasks)."""
    if count <= 1:
        return list(items)
    return [it for i, it in enumerate(items) if i % max(count, 1) == index]


def main() -> int:
    kind = os.environ.get("PULSE_JOB_KIND", "event")
    event_id = os.environ.get("PULSE_EVENT_ID", "")
    idx = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0") or 0)
    cnt = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1") or 1)
    print(f"[job] kind={kind} event={event_id} task {idx + 1}/{cnt}", flush=True)
    if kind != "event" or not event_id:
        print("[job] nothing to do", flush=True)
        return 0
    import server                                   # the web app module; importing it starts no server
    from perception.db import init_db, get_event_run, get_event_entities
    init_db()
    ev = get_event_run(event_id)
    if not ev:
        print(f"[job] event {event_id} not found", flush=True)
        return 1
    ents = get_event_entities(event_id)
    pending = [e for e in ents if (e.get("status") or "") != "done"]
    mine = shard(pending, idx, cnt)
    print(f"[job] {len(ents)} organizations, {len(pending)} pending, {len(mine)} in this task", flush=True)
    job_id = server._job_stub(role=ev.get("user_role") or "admin", email=ev.get("created_by"),
                              label=ev.get("event_name") or f"Event {event_id[:8]}")
    t0 = time.time()
    server._run_event_job(job_id, event_id, mine, ev.get("entity_type") or "practice",
                          bool(ev.get("include_teaser")), bool(ev.get("override_cache")),
                          bool(ev.get("auto_practice_composite")), bool(ev.get("practice_content")),
                          ev.get("group_id") or None, bool(ev.get("spotcheck")), ev.get("group_role") or "member",
                          task_mode=True)
    j = server._jobs.get(job_id) or {}
    print(f"[job] task {idx + 1}/{cnt} finished in {time.time() - t0:.0f}s status={j.get('status')} {j.get('error') or ''}", flush=True)
    return 0 if j.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
