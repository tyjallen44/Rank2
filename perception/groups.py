"""Groups — associations, cohorts, programs: a named set of Deep Diagnostic runs that can be
ranked, tracked and benchmarked against each other.

A run can belong to any number of groups. Membership is by run; ranking is by organization
(each organization counts once, by its latest run in the group). A group benchmark (rank of
N, median) is printed on a member's PDF once the group has GROUP_MIN members and the group's
`show_on_pdf` flag is on; below that the PDF just names the group.
"""
from __future__ import annotations

import json
import statistics
import uuid
from datetime import datetime
from typing import Any, Optional

from .db import get_connection

GROUP_MIN = 10          # members before a rank / median is printed on a PDF
TREND_MIN = 3           # monthly data points before a Trend report is offered


def trend_note(points: int) -> str:
    """The downloads-area wording while a Trend report is not yet available."""
    if points >= TREND_MIN:
        return ""
    return f"Trend report available after {TREND_MIN} monthly reports ({points} so far)"


def ensure_tables(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id           VARCHAR PRIMARY KEY,
            name         VARCHAR NOT NULL,
            description  VARCHAR,
            type_hint    VARCHAR DEFAULT 'mixed',
            preset       VARCHAR,
            show_on_pdf  BOOLEAN DEFAULT TRUE,
            archived     BOOLEAN DEFAULT FALSE,
            created_by   VARCHAR,
            created_at   TIMESTAMP
        )
    """)
    cols = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name='groups'").fetchall()}
    if "default_specialty" not in cols:
        con.execute("ALTER TABLE groups ADD COLUMN default_specialty VARCHAR")
    # Monthly reports on the 1st: Deep Diagnostic + Competitor Rankings + Trend for every member, emailed
    for c, d in (("monthly_reports", "BOOLEAN DEFAULT FALSE"), ("monthly_last_run_at", "TIMESTAMP"),
                 ("monthly_last_status", "VARCHAR"), ("monthly_last_event_id", "VARCHAR")):
        if c not in cols:
            con.execute(f"ALTER TABLE groups ADD COLUMN {c} {d}")
    con.execute("""
        CREATE TABLE IF NOT EXISTS group_runs (
            group_id  VARCHAR NOT NULL,
            run_id    VARCHAR NOT NULL,
            added_by  VARCHAR,
            added_at  TIMESTAMP,
            PRIMARY KEY (group_id, run_id)
        )
    """)
    gr_cols = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name='group_runs'").fetchall()}
    if "member_status" not in gr_cols:
        con.execute("ALTER TABLE group_runs ADD COLUMN member_status VARCHAR DEFAULT 'member'")   # member | prospect


_COLS = ["id", "name", "description", "type_hint", "preset", "show_on_pdf", "archived", "created_by", "created_at", "default_specialty",
         "monthly_reports", "monthly_last_run_at", "monthly_last_status", "monthly_last_event_id"]


def _row(r) -> dict:
    d = dict(zip(_COLS, r))
    d["created_at"] = str(d["created_at"]) if d.get("created_at") else None
    d["show_on_pdf"] = bool(d.get("show_on_pdf"))
    d["archived"] = bool(d.get("archived"))
    d["monthly_reports"] = bool(d.get("monthly_reports"))
    d["monthly_last_run_at"] = str(d["monthly_last_run_at"])[:16] if d.get("monthly_last_run_at") else None
    return d


def create_group(name: str, description: str = "", type_hint: str = "mixed", created_by: str = "",
                 preset: Optional[str] = None, show_on_pdf: bool = True) -> dict:
    gid = uuid.uuid4().hex[:12]
    con = get_connection()
    con.execute("INSERT INTO groups (id, name, description, type_hint, preset, show_on_pdf, archived, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, FALSE, ?, ?)",
                [gid, name.strip(), (description or "").strip(), type_hint or "mixed", preset or None, bool(show_on_pdf),
                 created_by or "", datetime.utcnow()])
    con.close()
    return get_group(gid)


def get_group(group_id: str) -> Optional[dict]:
    con = get_connection()
    r = con.execute(f"SELECT {', '.join(_COLS)} FROM groups WHERE id = ?", [group_id]).fetchone()
    con.close()
    return _row(r) if r else None


def update_group(group_id: str, **fields) -> Optional[dict]:
    allowed = {"name", "description", "type_hint", "preset", "show_on_pdf", "archived", "default_specialty",
               "monthly_reports", "monthly_last_run_at", "monthly_last_status", "monthly_last_event_id"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = ?"); vals.append(v)
    if sets:
        con = get_connection()
        con.execute(f"UPDATE groups SET {', '.join(sets)} WHERE id = ?", vals + [group_id])
        con.close()
    return get_group(group_id)


def list_groups(preset: Optional[str] = None, include_archived: bool = False) -> list[dict]:
    """Groups visible to an account: all (preset None) or only the preset's. With member counts."""
    con = get_connection()
    sql = f"SELECT {', '.join(_COLS)} FROM groups"
    where, args = [], []
    if not include_archived:
        where.append("NOT COALESCE(archived, FALSE)")
    if preset:
        where.append("preset = ?"); args.append(preset)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    rows = [_row(r) for r in con.execute(sql, args).fetchall()]
    counts = {}
    if rows:
        ids = [r["id"] for r in rows]
        q = ",".join("?" for _ in ids)
        for gid, n in con.execute(
                f"SELECT g.group_id, COUNT(DISTINCT LOWER(COALESCE(a.entity_name, '')) || '|' || LOWER(COALESCE(a.location, ''))) "
                f"FROM group_runs g JOIN analysis_runs a ON a.run_id = g.run_id WHERE g.group_id IN ({q}) GROUP BY g.group_id", ids).fetchall():
            counts[gid] = int(n or 0)
    con.close()
    for r in rows:
        r["member_count"] = counts.get(r["id"], 0)
    return rows


STATUSES = ("member", "prospect")


def add_runs(group_id: str, run_ids: list[str], added_by: str = "", status: str = "member") -> int:
    status = status if status in STATUSES else "member"
    con = get_connection()
    n = 0
    for rid in run_ids:
        if not rid:
            continue
        exists = con.execute("SELECT 1 FROM group_runs WHERE group_id = ? AND run_id = ?", [group_id, rid]).fetchone()
        if exists:
            continue
        con.execute("INSERT INTO group_runs (group_id, run_id, added_by, added_at, member_status) VALUES (?, ?, ?, ?, ?)",
                    [group_id, rid, added_by or "", datetime.utcnow(), status])
        n += 1
    con.close()
    return n


def set_status(group_id: str, run_id: str, status: str) -> None:
    """Member ↔ prospect. Applied to every run of that organization in the group so the
    status follows the organization, not one report."""
    status = status if status in STATUSES else "member"
    con = get_connection()
    row = con.execute("SELECT entity_name, location FROM analysis_runs WHERE run_id = ?", [run_id]).fetchone()
    if row:
        con.execute("""UPDATE group_runs SET member_status = ? WHERE group_id = ? AND run_id IN (
                         SELECT run_id FROM analysis_runs WHERE LOWER(COALESCE(entity_name,'')) = LOWER(?) AND LOWER(COALESCE(location,'')) = LOWER(?))""",
                    [status, group_id, row[0] or "", row[1] or ""])
    else:
        con.execute("UPDATE group_runs SET member_status = ? WHERE group_id = ? AND run_id = ?", [status, group_id, run_id])
    con.close()


def remove_run(group_id: str, run_id: str, *, whole_org: bool = True) -> int:
    """Take a report out of the group. By default every run of the same organization in this group
    goes too (re-runs attach a new run and leave the earlier memberships in place, so removing only
    the latest would just reveal the previous one). Returns the number of memberships removed."""
    con = get_connection()
    try:
        ids = [run_id]
        if whole_org:
            row = con.execute("SELECT entity_name, location FROM analysis_runs WHERE run_id = ?", [run_id]).fetchone()
            if row:
                ids = [r[0] for r in con.execute(
                    """SELECT g.run_id FROM group_runs g JOIN analysis_runs a ON a.run_id = g.run_id
                       WHERE g.group_id = ? AND LOWER(COALESCE(a.entity_name,'')) = LOWER(?) AND LOWER(COALESCE(a.location,'')) = LOWER(?)""",
                    [group_id, row[0] or "", row[1] or ""]).fetchall()] or [run_id]
        n = 0
        for rid in ids:
            con.execute("DELETE FROM group_runs WHERE group_id = ? AND run_id = ?", [group_id, rid])
            n += 1
        return n
    finally:
        con.close()


def delete_group(group_id: str) -> int:
    """Hard delete: the group and its memberships. Member runs and their PDFs are untouched."""
    con = get_connection()
    n = con.execute("SELECT COUNT(*) FROM group_runs WHERE group_id = ?", [group_id]).fetchone()
    con.execute("DELETE FROM group_runs WHERE group_id = ?", [group_id])
    con.execute("DELETE FROM groups WHERE id = ?", [group_id])
    con.close()
    return int(n[0] if n else 0)


def groups_for_runs(run_ids: list[str]) -> dict[str, list[dict]]:
    """run_id → [{id, name}] for History."""
    ids = [r for r in run_ids if r]
    if not ids:
        return {}
    con = get_connection()
    out: dict[str, list[dict]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" for _ in chunk)
        for rid, gid, name, st in con.execute(
                f"SELECT gr.run_id, g.id, g.name, COALESCE(gr.member_status, 'member') FROM group_runs gr JOIN groups g ON g.id = gr.group_id "
                f"WHERE gr.run_id IN ({q}) AND NOT COALESCE(g.archived, FALSE)", chunk).fetchall():
            out.setdefault(rid, []).append({"id": gid, "name": name, "status": st})
    con.close()
    return out


def members(group_id: str) -> list[dict]:
    """Latest run per organization in the group, ranked by Pulse Score (ties share a rank)."""
    con = get_connection()
    rows = con.execute(
        """SELECT a.run_id, a.entity_name, a.location, a.specialty, a.entity_type, a.service_line, a.parent_system,
                  a.generated_at, a.created_at, a.ran_by, a.confidence, a.pdf_path,
                  p.ai_visibility_score, p.tier_scores, a.result_json, COALESCE(g.member_status, 'member')
           FROM group_runs g
           JOIN analysis_runs a ON a.run_id = g.run_id
           LEFT JOIN ranked_providers p ON p.run_id = a.run_id AND p.rank = 1
           WHERE g.group_id = ?
           ORDER BY a.created_at DESC, a.run_id DESC""", [group_id]).fetchall()
    con.close()
    seen: set = set()
    out: list[dict] = []
    results: dict[str, dict] = {}     # run_id → parsed result_json, for the delivery check below
    for r in rows:
        (run_id, name, loc, spec, et, sl, ps, gen, created, ran_by, conf, pdf, score, ts_json, rj, status) = r
        key = f"{(name or '').strip().lower()}|{(loc or '').strip().lower()}"
        if key in seen:
            continue
        seen.add(key)
        ts: dict[str, Any] = {}
        try:
            ts = json.loads(ts_json) if ts_json else {}
        except Exception:
            ts = {}
        d: dict = {}
        if rj:
            try:
                d = json.loads(rj) or {}
            except Exception:
                d = {}
        if score is None and d:
            p0 = (d.get("rankings") or [{}])[0] or {}
            score = p0.get("ai_visibility_score")
            ts = p0.get("tier_scores") or ts
        wf_status = (d.get("website_facts") or {}).get("status")
        results[run_id] = d
        out.append({
            "run_id": run_id, "entity_name": name, "location": loc, "specialty": spec, "entity_type": et,
            "service_line": sl, "parent_system": ps, "generated_at": str(gen)[:10] if gen else None,
            "created_at": str(created) if created else None, "ran_by": ran_by, "confidence": conf,
            "has_pdf": bool(pdf), "score": score, "status": status or "member",
            "pillars": {k: ts.get(k) for k in ("clinical_outcomes_safety", "credentials_recognition", "patient_experience_reviews", "access_fit")},
            "website_status": wf_status,
        })
    # Members are ranked among themselves; a prospect gets the rank it WOULD hold among members.
    members_scored = sorted([m for m in out if m["score"] is not None and m["status"] == "member"], key=lambda m: -m["score"])
    rank, prev = 0, None
    for i, m in enumerate(members_scored, 1):
        if m["score"] != prev:
            rank, prev = i, m["score"]
        m["rank"] = rank
    member_scores = [m["score"] for m in members_scored]
    prospects_scored = sorted([m for m in out if m["score"] is not None and m["status"] != "member"], key=lambda m: -m["score"])
    for m in prospects_scored:
        m["rank"] = None
        m["would_rank"] = 1 + sum(1 for s in member_scores if s > m["score"])
    unscored = [m for m in out if m["score"] is None]
    for m in unscored:
        m["rank"] = None
    ranked = members_scored + prospects_scored + unscored
    # RLDatix customer flag (entity graph), keyed on name + "City, ST".
    try:
        from .graph import customer_flags, org_key as _ok
        def _cs(m):
            c, _, st = (m.get("location") or "").partition(",")
            return (m.get("entity_name") or "", c.strip(), st.strip())
        flags = customer_flags([_cs(m) for m in ranked])
        from .graph import contacts_for
        cmap = contacts_for([_cs(m) for m in ranked])
        for m in ranked:
            m["rldatix_customer"] = bool(flags.get(_ok(*_cs(m))))
            m["contacts"] = list(cmap.get(_ok(*_cs(m))) or [])
    except Exception:
        for m in ranked:
            m["rldatix_customer"] = False
            m["contacts"] = []
    # Competitor Rankings for each member's market: the row's city/state + the specialty (member's, else the
    # group default), 25-mile radius, practice rubric — this month's run when there is one.
    try:
        from .analyzer import market_key
        from .db import latest_market_run
        g0 = get_group(group_id) or {}
        seen_mk: dict = {}
        for m in ranked:
            spec = (m.get("specialty") or g0.get("default_specialty") or "").strip()
            mk = market_key(spec or None, "practice", True, True)
            loc = (m.get("location") or "").strip()
            m["market"] = {"location": loc, "specialty": spec, "key": mk}
            if (mk, loc.lower()) not in seen_mk:
                seen_mk[(mk, loc.lower())] = latest_market_run(mk, loc)
            r = seen_mk[(mk, loc.lower())]
            m["rankings"] = ({"run_id": r["run_id"], "generated_at": r["generated_at"], "has_pdf": bool(r.get("pdf_path"))} if r else None)
    except Exception:
        for m in ranked:
            m["market"], m["rankings"] = None, None
    # Trend data points (scored Deep Diagnostic runs) — the Trend report is offered at TREND_MIN.
    try:
        from .db import trend_point_counts
        tp = trend_point_counts([m.get("entity_name") for m in ranked])
        for m in ranked:
            m["trend_points"] = int(tp.get((m.get("entity_name") or "").strip().lower(), 0))
            m["trend_ready"] = m["trend_points"] >= TREND_MIN
            m["trend_note"] = trend_note(m["trend_points"])
    except Exception:
        for m in ranked:
            m["trend_points"], m["trend_ready"], m["trend_note"] = 0, False, trend_note(0)
    # Delivery check: what each latest report is missing before it goes out.
    from .delivery import check_result, spotcheck_required
    g = get_group(group_id) or {}
    bench_ready = bool(g.get("show_on_pdf")) and len(members_scored) >= GROUP_MIN
    need_sc = spotcheck_required(g)
    for m in ranked:
        d = results.get(m["run_id"]) or {}
        if not d:
            d = {"entity_type": m.get("entity_type"), "specialty": m.get("specialty"), "generated_at": m.get("generated_at"),
                 "rankings": [{"ai_visibility_score": m.get("score")}] if m.get("score") is not None else []}
        m["flags"] = check_result(d, group=g, benchmark_ready=bench_ready and m["status"] == "member", require_spotcheck=need_sc)
    return ranked


def member_history(group_id: str, run_id: str) -> dict:
    """Every run of one organization inside the group (oldest first) plus the AI Readiness
    Checklist summary of its latest run — what the group page shows when a row is expanded."""
    con = get_connection()
    row = con.execute("SELECT entity_name, location FROM analysis_runs WHERE run_id = ?", [run_id]).fetchone()
    if not row:
        con.close()
        return {"runs": [], "checklist": None}
    name, loc = row
    rows = con.execute(
        """SELECT a.run_id, a.generated_at, a.created_at, a.ran_by, p.ai_visibility_score, p.tier_scores, a.result_json
           FROM group_runs g JOIN analysis_runs a ON a.run_id = g.run_id
           LEFT JOIN ranked_providers p ON p.run_id = a.run_id AND p.rank = 1
           WHERE g.group_id = ? AND LOWER(COALESCE(a.entity_name,'')) = LOWER(?) AND LOWER(COALESCE(a.location,'')) = LOWER(?)
           ORDER BY a.created_at ASC""", [group_id, name or "", loc or ""]).fetchall()
    con.close()
    runs, latest_json = [], None
    for rid, gen, created, ran_by, score, ts_json, rj in rows:
        ts = {}
        try:
            ts = json.loads(ts_json) if ts_json else {}
        except Exception:
            pass
        if score is None and rj:
            try:
                p0 = (json.loads(rj).get("rankings") or [{}])[0]
                score, ts = p0.get("ai_visibility_score"), p0.get("tier_scores") or ts
            except Exception:
                pass
        runs.append({"run_id": rid, "generated_at": str(gen)[:10] if gen else None, "created_at": str(created) if created else None,
                     "ran_by": ran_by, "score": score,
                     "pillars": {k: ts.get(k) for k in ("clinical_outcomes_safety", "credentials_recognition", "patient_experience_reviews", "access_fit")}})
        latest_json = rj or latest_json
    for i, r in enumerate(runs):
        prev = next((x["score"] for x in reversed(runs[:i]) if x["score"] is not None), None)
        r["delta"] = (r["score"] - prev) if (r["score"] is not None and prev is not None) else None
    checklist = None
    if latest_json:
        try:
            from .models import AnalysisResult
            from .checklist import build_checklist, summarize
            res = AnalysisResult.model_validate_json(latest_json)
            rows_c = build_checklist(res)
            sm = summarize(rows_c)
            checklist = {**sm, "failing": [r["label"] for r in rows_c if r["status"] == "fail"],
                         "partial": [r["label"] for r in rows_c if r["status"] == "partial"]}
        except Exception:
            checklist = None
    return {"entity_name": name, "location": loc, "runs": runs, "checklist": checklist}


def benchmark(group_id: str, run_id: Optional[str] = None, entity_name: Optional[str] = None,
              location: Optional[str] = None) -> dict:
    """Group statistics and, when a run / organization is given, its rank in the group."""
    ms = members(group_id)
    scores = [m["score"] for m in ms if m["score"] is not None and m["status"] == "member"]
    out: dict[str, Any] = {"members": sum(1 for m in ms if m["status"] == "member"), "prospects": sum(1 for m in ms if m["status"] != "member"),
                           "scored": len(scores), "ready": len(scores) >= GROUP_MIN,
                           "min_members": GROUP_MIN, "median": None, "q1": None, "q3": None, "rank": None, "total": len(scores), "prospect": False}
    if scores:
        out["median"] = round(statistics.median(scores))
        if len(scores) >= 4:
            qs = statistics.quantiles(scores, n=4)
            out["q1"], out["q3"] = round(qs[0]), round(qs[2])
    key = None
    if entity_name is not None:
        key = f"{(entity_name or '').strip().lower()}|{(location or '').strip().lower()}"
    for m in ms:
        if (run_id and m["run_id"] == run_id) or (key and f"{(m['entity_name'] or '').strip().lower()}|{(m['location'] or '').strip().lower()}" == key):
            out["prospect"] = m.get("status") != "member"
            out["rank"] = m.get("rank") if not out["prospect"] else m.get("would_rank")
            out["score"] = m.get("score")
            break
    return out


def context_for_pdf(group_id: str, run_id: str, entity_name: str, location: str) -> Optional[dict]:
    """What the PDF prints: the group name always; rank-of-N and median once the group is ready
    and the group allows it."""
    g = get_group(group_id)
    if not g or g.get("archived"):
        return None
    b = benchmark(group_id, run_id=run_id, entity_name=entity_name, location=location)
    ctx = {"group_id": g["id"], "group_name": g["name"], "members": b["members"], "ready": b["ready"] and bool(g.get("show_on_pdf")),
           "prospect": bool(b.get("prospect"))}
    if ctx["ready"]:
        ctx.update({"rank": b.get("rank"), "total": b.get("total"), "median": b.get("median"), "q1": b.get("q1"), "q3": b.get("q3")})
    return ctx


def list_monthly_groups() -> list[dict]:
    """Active groups with the monthly switch on."""
    return [g for g in list_groups(None, False) if g.get("monthly_reports")]


def monthly_entities(group: dict, members: list[dict], event_id: str) -> list[dict]:
    """The event rows for a monthly run: every MEMBER (prospects are not reported on), one row per
    organization, resolved by name + 'City, ST', specialty from the member else the group default."""
    import uuid as _uuid
    rows = []
    for i, m in enumerate(members, 1):
        if (m.get("status") or "member") != "member":
            continue
        city, _, st = (m.get("location") or "").partition(",")
        if not (m.get("entity_name") and city.strip() and st.strip()):
            continue
        rows.append({"id": _uuid.uuid4().hex[:16], "event_id": event_id, "row_num": i,
                     "input_name": m["entity_name"], "input_city": city.strip(), "input_state": st.strip().upper(),
                     "input_url": "", "input_customer": "yes" if m.get("rldatix_customer") else "",
                     "input_specialty": (m.get("specialty") or group.get("default_specialty") or ""),
                     "resolved_name": m["entity_name"], "resolved_addr": ""})
    return rows


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
