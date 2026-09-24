"""
Rank2 — web server entry point.

Start:
    python server.py              # binds 0.0.0.0:8000
    PORT=9000 python server.py    # custom port

Required .env variables:
    ACCESS_PASSWORD   — protects every endpoint
    ANTHROPIC_API_KEY — your Claude API key
Optional:
    REPORTS_DIR       — where PDFs are saved  (default: ~/Documents/Rank2 Reports)
    HOST              — bind address          (default: 0.0.0.0)
    PORT              — listen port           (default: 8000)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac as _hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Optional

# Load .env before importing perception (it reads settings at import time)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
_PRESERVE_UPPERCASE: frozenset[str] = frozenset({
    # US state abbreviations
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
    # Common healthcare / general acronyms
    "USA", "US", "MRI", "CT", "ER", "ICU", "OR", "PT", "OT", "RT",
    "ENT", "OB", "GYN", "OBGYN", "ACL", "MCL", "ACL", "NPI",
    "HIPAA", "NCQA", "AAAHC", "AAAASF", "MIPS", "QPP", "CMS",
    "ASC", "PCMH", "DNV", "TJC",
})


def _smart_title(text: str) -> str:
    """Title-case text while preserving known all-caps acronyms and state codes."""
    return " ".join(
        w.upper() if w.upper() in _PRESERVE_UPPERCASE else w.capitalize()
        for w in text.title().split()
    )


def _normalize_input(text: str | None) -> str | None:
    """Title-case a free-text field received in ALL CAPS from the UI. HTML entities that
    leaked in from the page (e.g. '&amp;' for '&') are decoded first so names print as typed."""
    if not text:
        return text
    import html as _html
    return _smart_title(_html.unescape(text.strip()))

app = FastAPI(title="Pulse", docs_url=None, redoc_url=None)

@app.middleware("http")
async def no_cache_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

# ── Config ────────────────────────────────────────────────────────────────────
_raw_pw = os.environ.get("ACCESS_PASSWORD", "")
ACCESS_PASSWORDS: set[str] = {p.strip() for p in _raw_pw.split(",") if p.strip()}
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
_GOOGLE_REDIRECT_URI = f"{APP_URL}/auth/google/callback"
REPORTS_DIR = Path(os.environ.get(
    "REPORTS_DIR",
    str(Path.home() / "Documents" / "Rank2 Reports"),
))

# Per-run cost metering: hooks the Anthropic SDK, Google Places and Gemini calls.
from perception import cost_tracker as _cost
_cost.install()


def _finish_cost(job_id: str) -> None:
    """Close the job's cost accumulator, persist it, and attach it to the job/result."""
    try:
        acc = _cost.finish(job_id)
        if not acc or not (acc.get("calls") or acc.get("places_calls") or acc.get("gemini_calls")):
            return
        j = _jobs.get(job_id) or {}
        res = j.get("result") or {}
        run_id = res.get("run_id") or res.get("event_id")
        j["cost"] = acc
        if isinstance(res, dict):
            res["cost"] = {"cost_usd": acc.get("cost_usd"), "calls": acc.get("calls"), "web_searches": acc.get("web_searches"),
                           "places_calls": acc.get("places_calls"), "gemini_calls": acc.get("gemini_calls"),
                           "input_tokens": acc.get("input_tokens"), "output_tokens": acc.get("output_tokens"),
                           "seconds": acc.get("seconds"), "summary": _cost.summary_line(acc)}
        from perception.db import record_run_cost
        record_run_cost(job_id, run_id, j.get("kind") or acc.get("kind") or "", str(j.get("label") or ""), acc)
        print(f"[cost] job={job_id[:8]} kind={j.get('kind')} {_cost.summary_line(acc)}")
    except Exception as exc:
        print(f"[cost] finish failed: {type(exc).__name__}: {exc}")


# password → (role_id, display_name); anything not listed defaults to admin
_ROLE_MAP: dict[str, tuple[str, str]] = {
    "RLD_Data_Access":  ("rldatix",         "RLDatix Team"),
    "Partner_Access":   ("partner",          "Partner User"),
    "CSRank2Access":    ("customersuccess",  "Customer Success"),
    "SalesTeamRank2":   ("salesteam",        "Sales Team"),
    "Rank2Marketing":   ("marketing",        "Marketing"),
}
_ROLE_DISPLAY: dict[str, str] = {v[0]: v[1] for v in _ROLE_MAP.values()}
_ROLE_DISPLAY["admin"] = "Admin"
_ROLE_DISPLAY["integrations_admin"] = "Integrations Admin"

def _password_role(pw: str) -> tuple[str, str]:
    return _ROLE_MAP.get(pw, ("admin", "Admin"))

# ── Auth ──────────────────────────────────────────────────────────────────────
_SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))


def _signing_key() -> bytes:
    """Derive a consistent HMAC signing key from all access passwords, sorted for stability."""
    combined = "|".join(sorted(ACCESS_PASSWORDS)) or "rank2"
    return _hmac.new(combined.encode(), b"rank2-session-v1", hashlib.sha256).digest()


def _create_token(role_id: str, **extra: object) -> str:
    payload = {"role": role_id, "exp": int(time.time()) + _SESSION_TTL_DAYS * 86400, **extra}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = _hmac.new(_signing_key(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def _verify_token(token: str) -> str | None:
    """Returns role_id if the token is valid and not expired, else None."""
    payload = _verify_token_full(token)
    return payload["role"] if payload else None


def _verify_token_full(token: str) -> dict | None:
    """Returns the full decoded payload dict if valid, else None."""
    try:
        b64, sig = token.rsplit(".", 1)
        expected = _hmac.new(_signing_key(), b64.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload["exp"] < time.time():
            return None
        return payload
    except Exception:
        return None


class LoginRequest(BaseModel):
    password: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if not ACCESS_PASSWORDS:
        raise HTTPException(500, "ACCESS_PASSWORD not configured in .env")
    if req.password not in ACCESS_PASSWORDS:
        raise HTTPException(401, "Invalid password")
    role_id, display_name = _password_role(req.password)
    token = _create_token(role_id)
    return {"token": token, "role": role_id, "display_name": display_name}


def _extract_token(request: Request, token: Optional[str]) -> str | None:
    hdr = request.headers.get("Authorization", "")
    return hdr[7:] if hdr.startswith("Bearer ") else token


def require_auth(request: Request, token: Optional[str] = Query(None)) -> str:
    """Accepts Bearer header or ?token= query param. Returns the user's role_id."""
    t = _extract_token(request, token)
    role = _verify_token(t) if t else None
    if role is None:
        raise HTTPException(401, "Session expired — please log in again")
    return role


def get_current_user_payload(request: Request, token: Optional[str] = Query(None)) -> dict:
    """Returns the full token payload dict; raises 401 if invalid."""
    t = _extract_token(request, token)
    payload = _verify_token_full(t) if t else None
    if payload is None:
        raise HTTPException(401, "Session expired — please log in again")
    return payload


def require_admin(payload: dict = Depends(get_current_user_payload)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return payload


def require_integration_admin(payload: dict = Depends(get_current_user_payload)) -> dict:
    """Super-admin OR the scoped Integrations Admin role. Integration-management
    endpoints use this; user-management stays behind require_admin (super-admin only)."""
    if payload.get("role") not in ("admin", "integrations_admin"):
        raise HTTPException(403, "Integration admin access required")
    return payload


@app.get("/api/auth/me")
async def me(payload: dict = Depends(get_current_user_payload)):
    role = payload.get("role", "")
    name = payload.get("name") or _ROLE_DISPLAY.get(role, "Admin")
    return {
        "role": role,
        "display_name": name,
        "email": payload.get("email"),
        "brand": payload.get("brand", "original"),
    }


_APP_VERSION = "1.09"
_SERVER_STARTED = time.time()
_SERVER_START = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get_commit_sha() -> str:
    try:
        return Path(__file__).parent.joinpath("VERSION").read_text().strip()
    except Exception:
        pass
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


class PrefsRequest(BaseModel):
    notify_complete: Optional[bool] = None


@app.get("/api/me/prefs")
async def me_prefs(payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_notify_pref
    init_db()
    return {"notify_complete": get_notify_pref(payload.get("email") or "")}


@app.put("/api/me/prefs")
async def me_prefs_update(req: PrefsRequest, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, set_notify_pref, get_notify_pref
    init_db()
    email = payload.get("email") or ""
    if req.notify_complete is not None and email:
        set_notify_pref(email, req.notify_complete)
    return {"notify_complete": get_notify_pref(email)}


@app.get("/api/jobs/mine")
async def jobs_mine(payload: dict = Depends(get_current_user_payload)):
    """This user's runs known to this server process: running, and recently finished."""
    me = _owner_key(payload.get("email"), payload.get("role"))
    out = []
    now = time.time()
    for jid, j in list(_jobs.items()):
        if (j.get("owner") or _owner_key(j.get("email"), j.get("role"))) != me:
            continue
        started = j.get("started_at") or now
        if j.get("status") != "running" and now - started > 6 * 3600:
            continue
        res = j.get("result") or {}
        label = (j.get("label") or j.get("entity_name") or res.get("network_name") or res.get("location")
                 or ("Compare Two" if res.get("comparison") else "Report"))
        out.append({"job_id": jid, "status": j.get("status", "running"), "label": label,
                    "started_at": started, "minutes": round((now - started) / 60, 1),
                    "run_id": res.get("run_id"), "error": (j.get("error") if j.get("status") == "error" else None)})
    out.sort(key=lambda x: -x["started_at"])
    return out[:12]


@app.get("/api/zip/{code}")
async def zip_lookup(code: str, _: dict = Depends(get_current_user_payload)):
    """City + state for a US ZIP (form autofill). Fail-soft 404."""
    code = (code or "").strip()[:5]
    if not (code.isdigit() and len(code) == 5):
        raise HTTPException(400, "5-digit ZIP required")
    try:
        city, state = await asyncio.get_running_loop().run_in_executor(None, _zip_to_city_state, code)
        return {"zip": code, "city": city, "state": state}
    except Exception:
        raise HTTPException(404, "ZIP not found")


@app.get("/api/team/emails")
async def team_emails(_: dict = Depends(get_current_user_payload)):
    """Active teammates' addresses, for recipient autofill (any signed-in user)."""
    from perception.db import init_db, get_connection
    init_db()
    con = get_connection()
    try:
        rows = con.execute("SELECT email FROM users WHERE COALESCE(is_active, TRUE) AND email IS NOT NULL ORDER BY LOWER(email)").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows if r and r[0]]


@app.get("/api/entities/suggest")
async def entities_suggest(q: str = "", limit: int = 8, kinds: str = "",
                           payload: dict = Depends(get_current_user_payload)):
    """Typeahead: organizations this team already analyzed or tracked."""
    from perception.db import init_db, suggest_entities
    init_db()
    ks = [k.strip() for k in (kinds or "").split(",") if k.strip()]
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: suggest_entities(q, role=payload.get("role"), limit=max(1, min(int(limit or 8), 15)), kinds=ks))


class RecentMatchRequest(BaseModel):
    kind: str = "analysis"          # analysis | network | comparison
    name: str
    name_b: str = ""
    city: str = ""
    days: int = 14


@app.post("/api/runs/recent-match")
async def runs_recent_match(req: RecentMatchRequest, payload: dict = Depends(get_current_user_payload)):
    """Finished runs of the same organization within `days` — the duplicate-run warning."""
    from perception.db import init_db, recent_runs_matching
    init_db()
    days = max(1, min(int(req.days or 14), 90))
    return recent_runs_matching(req.kind, (req.name or "").strip(), (req.name_b or "").strip(),
                                role=payload.get("role"), city=(req.city or "").strip(), days=days)


@app.get("/api/admin/jobs")
async def admin_jobs(payload: dict = Depends(require_admin)):
    """Every job this server process knows about, across all users (Admin → Operations)."""
    now = time.time()
    out = []
    for jid, j in list(_jobs.items()):
        started = j.get("started_at") or now
        res = j.get("result") or {}
        label = (j.get("label") or j.get("entity_name") or res.get("network_name") or res.get("location")
                 or ("Compare Two" if res.get("comparison") else "—"))
        live = j.get("cost") or _cost.snapshot(jid) or {}
        out.append({"job_id": jid, "status": j.get("status", "running"), "kind": j.get("kind") or "—",
                    "cost_usd": round(float(live.get("cost_usd") or 0), 2) if live else None,
                    "label": label, "email": j.get("email") or "", "role": j.get("role") or "",
                    "started_at": started, "minutes": round((now - started) / 60, 1),
                    "run_id": res.get("run_id"), "error": j.get("error") if j.get("status") == "error" else None})
    out.sort(key=lambda x: -x["started_at"])
    stats = {"running": sum(1 for o in out if o["status"] == "running"),
             "done": sum(1 for o in out if o["status"] == "done"),
             "error": sum(1 for o in out if o["status"] == "error")}
    return {"jobs": out[:200], "stats": stats, "server_started": _SERVER_STARTED,
            "uptime_minutes": round((now - _SERVER_STARTED) / 60),
            "version": _APP_VERSION, "workers": getattr(_pool, "_max_workers", None)}


_MAINT_REBRAND_PAIRS = [("AI Visibility Intelligence", "AI Reputation Analysis Platform"),
                        ("AI Reputation Intelligence", "AI Reputation Analysis Platform"),
                        ("AI Visibility", "AI Reputation"), ("AI-Visibility", "AI-Reputation"),
                        ("AI visibility", "AI reputation"), ("AI-visibility", "AI-reputation")]

_MAINT_TASKS = {
    "rebrand-learn": "Rename 'AI Visibility' → 'AI Reputation' in the live Learn / Methodology articles (idempotent).",
    "backfill-comparisons": "Record History rows for Compare Two PDFs on disk that pre-date persisted comparisons.",
    "retrack-practices": "Tracked entities that have a specialty but are typed Hospital → type Specialty Practice so the next snapshot uses the practice rubric.",
    "apply-learn-content": "Sync the live Learn / Methodology articles to the reviewed seed (perception/learn_seed.py); custom articles are left alone.",
    "backfill-tracked-ran-by": "History rows with no 'Run by' that are Trends snapshots → attribute them to whoever set up that tracked entity.",
    "seed-org-graph": "Entity graph: seed organizations + confirmed locations from every tracked entity's fixed roster (practice, service line, community health).",
}


@app.get("/api/admin/maintenance")
async def admin_maintenance_list(_: dict = Depends(require_admin)):
    return [{"task": k, "description": v} for k, v in _MAINT_TASKS.items()]


@app.post("/api/admin/maintenance/{task}")
async def admin_maintenance(task: str, apply: bool = False, _: dict = Depends(require_admin)):
    """One-off data cleanups, run where the database and the reports volume are both
    mounted. Dry run by default; ?apply=true writes. Same logic as scripts/*.py."""
    if task not in _MAINT_TASKS:
        raise HTTPException(404, f"Unknown task; choose one of {', '.join(_MAINT_TASKS)}")
    from perception.db import init_db, get_connection
    init_db()

    def _go() -> dict:
        import uuid as _uuid
        from datetime import datetime as _dt
        con = get_connection()
        lines: list = []
        try:
            if task == "rebrand-learn":
                total = 0
                for col in ("title", "body", "category"):
                    for old, new in _MAINT_REBRAND_PAIRS:
                        if apply:
                            cur = con.execute(f"UPDATE learn_articles SET {col} = replace({col}, ?, ?) WHERE {col} LIKE ?",
                                              [old, new, f"%{old}%"])
                            total += getattr(cur, "rowcount", 0) or 0
                        else:
                            total += con.execute(f"SELECT COUNT(*) FROM learn_articles WHERE {col} LIKE ?",
                                                 [f"%{old}%"]).fetchone()[0]
                lines.append(f"learn_articles row-edits {'applied' if apply else 'pending'}: {total}")
            elif task == "backfill-comparisons":
                from perception.strings import FILE_COMPARISON_PFX
                known = {r[0] for r in con.execute("SELECT pdf_path FROM comparison_runs").fetchall()}
                pat = re.compile(rf"^{re.escape(FILE_COMPARISON_PFX)}_(.+)_Vs_(.+?)_+([0-9a-f]{{8}})\.pdf$", re.I)
                added = skipped = 0
                for f in sorted(REPORTS_DIR.glob(f"{FILE_COMPARISON_PFX}*.pdf")):
                    if str(f) in known:
                        skipped += 1
                        continue
                    m = pat.match(f.name)
                    if not m:
                        lines.append(f"? unrecognised name: {f.name}")
                        continue
                    a = m.group(1).replace("_", " ").strip(); b = m.group(2).replace("_", " ").strip(); pfx = m.group(3)
                    row = con.execute(
                        "SELECT run_id, generated_at, user_role, ran_by, location, specialty, entity_name "
                        "FROM analysis_runs WHERE run_id LIKE ? ORDER BY generated_at DESC LIMIT 1", [pfx + "%"]).fetchone()
                    run_id_a, gen, role, ran_by, loc_a, spec, ent_a = row if row else (None, None, "admin", None, None, None, None)
                    if not gen:
                        gen = _dt.fromtimestamp(f.stat().st_mtime).date()
                    lines.append(f"{'ADD' if apply else 'would add'}: {a} vs {b}  ({gen}, {role or 'admin'}, side-A run {run_id_a or 'unknown'})")
                    if apply:
                        con.execute(
                            """INSERT INTO comparison_runs (id, run_id_a, run_id_b, entity_a, entity_b, location_a, location_b,
                                                            specialty, pdf_path, teaser, user_role, ran_by, generated_at, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?)""",
                            [_uuid.uuid4().hex[:12], run_id_a, None, ent_a or a, b, loc_a, None, spec, str(f),
                             role or "admin", ran_by, gen, _dt.combine(gen, _dt.min.time())])
                    added += 1
                lines.append(f"{'Added' if apply else 'Would add'} {added}, already recorded {skipped}.")
            elif task == "apply-learn-content":
                from perception.db import list_learn_articles, update_learn_article
                from perception.learn_seed import STARTER_ARTICLES, METHODOLOGY_ARTICLES
                renames = {"learn": {"Why AI visibility matters": "Why AI reputation matters"},
                           "methodology": {"How AI assistants are queried": "How the score is produced"}}
                changed = 0
                for page, seed in (("learn", STARTER_ARTICLES), ("methodology", METHODOLOGY_ARTICLES)):
                    live = list_learn_articles(include_unpublished=True, page=page)
                    by_title = {a["title"].strip().lower(): a for a in live}
                    for art in seed:
                        new_title = art["title"]
                        old_title = next((o for o, n in renames[page].items() if n == new_title), new_title)
                        row = by_title.get(new_title.strip().lower()) or by_title.get(old_title.strip().lower())
                        if not row:
                            lines.append(f"[{page}] MISSING live row for '{new_title}' — use 'Load starter articles' to add it")
                            continue
                        same = (row["title"] == new_title and (row.get("category") or "") == art["category"]
                                and (row.get("body") or "").strip() == art["body"].strip())
                        if same:
                            continue
                        changed += 1
                        lines.append(f"[{page}] {'UPDATED' if apply else 'would update'} '{row['title']}'" + (f" → '{new_title}'" if row["title"] != new_title else ""))
                        if apply:
                            update_learn_article(row["id"], title=new_title, category=art["category"], body=art["body"])
                lines.append(f"{'Applied' if apply else 'Would update'}: {changed} article(s).")
            elif task == "seed-org-graph":
                from perception.graph import upsert_org as _g_upsert, stats as _g_stats
                rows = con.execute("SELECT entity_name, city, state, entity_type, specialty, confirmed_roster, anchor_listing, created_by, created_at "
                                   "FROM tracked_entities WHERE COALESCE(entity_type,'hospital') IN ('practice','service_line','community_health') "
                                   "AND COALESCE(confirmed_roster,'') <> '' ORDER BY created_at ASC").fetchall()
                n = 0
                for nm, city, st, et, spec, roster, anchor, who, created in rows:
                    try:
                        locs = json.loads(roster) if roster else []
                        locs = [({"name": x} if isinstance(x, str) else x) for x in locs]
                        anc = json.loads(anchor) if anchor else None
                    except Exception:
                        continue
                    lines.append(f"{'SEED' if apply else 'would seed'} {nm} ({city}, {st}) — {len(locs)} location(s), confirmed by {who} on {str(created)[:10]}")
                    if apply:
                        _g_upsert(nm, city, st, et, specialty=spec, anchor=anc, locations=locs, source="trends", by=who)
                    n += 1
                lines.append(f"{'Seeded' if apply else 'Would seed'} {n} organization(s)." + (f" Graph now: {_g_stats()}" if apply else ""))
            elif task == "backfill-tracked-ran-by":
                # Trends snapshots used to be created without the user's email, so History showed
                # "—" for them. Attribute each unattributed run whose name matches a tracked entity
                # (and that was generated on/after that tracking was set up) to the entity's owner.
                ents = con.execute(
                    "SELECT LOWER(entity_name), created_by, created_at FROM tracked_entities "
                    "WHERE COALESCE(created_by, '') <> '' ORDER BY created_at ASC").fetchall()
                owners: dict = {}
                for nm, who, created in ents:
                    owners.setdefault(nm, (who, created))       # earliest tracking wins
                fixed = 0
                for table, name_col in (("analysis_runs", "entity_name"), ("network_runs", "network_name")):
                    rows = con.execute(
                        f"SELECT run_id, {name_col}, generated_at FROM {table} "
                        f"WHERE COALESCE(ran_by, '') = '' AND COALESCE({name_col}, '') <> '' ORDER BY generated_at ASC").fetchall()
                    for rid, nm, gen in rows:
                        own = owners.get(str(nm or "").lower())
                        if not own:
                            continue
                        who, created = own
                        try:
                            if created and gen and str(gen)[:10] < str(created)[:10]:
                                continue                        # run pre-dates the tracking
                        except Exception:
                            pass
                        fixed += 1
                        lines.append(f"{'SET' if apply else 'would set'} {table} {str(rid)[:8]} {nm} ({str(gen)[:10]}) → {who}")
                        if apply:
                            con.execute(f"UPDATE {table} SET ran_by = ? WHERE run_id = ?", [who, rid])
                lines.append(f"{'Attributed' if apply else 'Would attribute'} {fixed} run(s).")
            elif task == "retrack-practices":
                rows = con.execute(
                    "SELECT id, entity_name, specialty, city, state FROM tracked_entities "
                    "WHERE COALESCE(entity_type, 'hospital') = 'hospital' AND COALESCE(specialty, '') <> '' "
                    "ORDER BY entity_name").fetchall()
                for eid, name, spec, city, state in rows:
                    lines.append(f"{'SET' if apply else 'would set'} Specialty Practice: {name} — {spec} ({city}, {state})")
                    if apply:
                        con.execute("UPDATE tracked_entities SET entity_type = 'practice' WHERE id = ?", [eid])
                lines.append(f"{'Updated' if apply else 'Would update'} {len(rows)} tracked entit{'y' if len(rows) == 1 else 'ies'}."
                             + ("" if not rows else " Open each in Trends → configuration to confirm its Locations roster before the next run."))
        finally:
            con.close()
        return {"task": task, "apply": apply, "lines": lines}

    return await asyncio.get_running_loop().run_in_executor(None, _go)


@app.get("/api/admin/costs")
async def admin_costs(days: int = 30, _: dict = Depends(require_admin)):
    """Estimated API spend per analysis run: totals/averages by kind and the most recent runs."""
    from perception.db import init_db, cost_summary
    init_db()
    return await asyncio.get_running_loop().run_in_executor(None, lambda: cost_summary(max(1, min(int(days or 30), 365))))


class SendReportRequest(BaseModel):
    emails: List[str]
    note: str = ""


def _report_files_for(run_id: str) -> tuple:
    """(kind, title, [files]) for an analysis, network or comparison run id."""
    from perception.db import get_comparison_run, get_connection
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")
    c = get_comparison_run(safe)
    if c:
        return "Compare Two report", f"{c['entity_a']} vs {c['entity_b']}", [c.get("pdf_path")]
    con = get_connection()
    r = con.execute("SELECT network_name, pdf_path, teaser_pdf_path, full_detail_pdf_path FROM network_runs WHERE run_id = ?", [safe]).fetchone()
    if r:
        con.close()
        return "Hospital Network report", r[0], [r[1], r[2], r[3]]
    r = con.execute("SELECT entity_name, location, pdf_path, teaser_pdf_path, briefing_pdf_path, individual_report FROM analysis_runs WHERE run_id = ?", [safe]).fetchone()
    con.close()
    if r:
        kind = "Deep Diagnostic" if r[5] else "Competitors Rankings report"
        return kind, r[0] or r[1], [r[2], r[3], r[4]]
    return None, None, []


@app.post("/api/reports/{run_id}/send")
async def send_report(run_id: str, req: SendReportRequest, payload: dict = Depends(get_current_user_payload)):
    """Email a finished report (PDF attached) to one or more addresses."""
    from perception.db import init_db
    from perception.email_utils import send_report_copy
    init_db()
    emails = _clean_emails(req.emails)
    if not emails:
        raise HTTPException(400, "Provide at least one valid email address")
    kind, title, files = _report_files_for(run_id)
    files = [f for f in files if f and Path(f).exists()]
    if not kind or not files:
        raise HTTPException(404, "Report file not found")
    sender = payload.get("name") or (payload.get("email") or "").split("@")[0].replace(".", " ").title()
    sent = []
    def _go():
        for addr in emails:
            try:
                send_report_copy(addr, kind, title, files[:1] if kind != "Hospital Network report" else files, sender=sender, note=req.note or "")
                sent.append(addr)
            except Exception as exc:
                print(f"[send-report] FAILED to={addr}: {type(exc).__name__}: {exc}")
    await asyncio.get_running_loop().run_in_executor(None, _go)
    if not sent:
        raise HTTPException(502, "The report could not be sent — check the email service configuration.")
    return {"sent": len(sent), "emails": sent}


@app.get("/api/version")
async def version():
    return {"version": _APP_VERSION, "commit": _get_commit_sha(), "deployed": _SERVER_START}


# ── Job management ────────────────────────────────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_pool = ThreadPoolExecutor(max_workers=2)


def _put(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event: Any) -> None:
    asyncio.run_coroutine_threadsafe(queue.put(event), loop)


def _job_error(exc: Exception) -> str:
    s = str(exc)
    if "529" in s or "overloaded" in s.lower():
        return "__OVERLOADED__"
    return s


def _ensure_individual_teaser(result, job: dict) -> None:
    """Individual reports (hospital, community health): the main PDF is always the full
    report. When a teaser was requested, render it as a SEPARATE file into
    result.teaser_pdf_path (never over the main PDF). Fail-soft."""
    if not job.get("teaser_report") or job.get("skip_pdf"):
        return
    if result.teaser_pdf_path and Path(result.teaser_pdf_path).exists():
        return
    try:
        import re as _re
        from datetime import datetime as _dt
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        _ts = _dt.utcnow().strftime("%y%m%d-%H%M")
        _safe = _re.sub(r"\W+", "_", result.entity_name or "entity")[:40]
        t_pdf = REPORTS_DIR / f"{_safe}_Teaser-{_ts}.pdf"
        copy = result.model_copy()
        copy.teaser_report = True
        copy.patient_perspective = True
        if (result.entity_type or "") == "community_health":
            from perception.fqhc_pdf import render_fqhc_pdf
            render_fqhc_pdf(copy, str(t_pdf), brand=job.get("brand", "original"))
        else:
            from perception.pdf import render_pdf
            render_pdf(copy, t_pdf, brand=job.get("brand", "original"))
        result.teaser_pdf_path = str(t_pdf)
        from perception.db import get_connection
        with get_connection() as _con:
            _con.execute("UPDATE analysis_runs SET teaser_pdf_path = ? WHERE run_id = ?", [str(t_pdf), result.run_id])
    except Exception as exc:
        print(f"[teaser] separate teaser render failed: {type(exc).__name__}: {exc}")


def _backfill_teaser_pdf(result, job: dict) -> None:
    """Re-render result as teaser PDF when cache returned a stale or missing PDF.

    Covers two cases:
    1. Cache returned an old non-teaser result (result.teaser_report is False).
    2. The stored pdf_path refers to a file that no longer exists on this machine
       (e.g., a path from a production container that differs from local REPORTS_DIR).
    """
    if not job.get("teaser_report") or job.get("skip_pdf"):
        return
    pdf_ok = bool(result.pdf_path and Path(result.pdf_path).exists())
    if result.teaser_report and pdf_ok:
        return  # already have a valid teaser PDF

    import re as _re
    from datetime import datetime as _dt
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _ts = _dt.utcnow().strftime("%y%m%d-%H%M")
    _safe = _re.sub(r"\W+", "_", result.entity_name or "entity")[:40]
    t_pdf = REPORTS_DIR / f"{_safe}_Summary-Report-{_ts}.pdf"

    result.teaser_report = True
    if result.entity_type == "community_health":
        from perception.fqhc_pdf import render_fqhc_pdf
        render_fqhc_pdf(result, str(t_pdf), brand=job.get("brand", "original"))
    else:
        from perception.pdf import render_pdf
        render_pdf(result, t_pdf, brand=job.get("brand", "original"))
    result.pdf_path = str(t_pdf)

    from perception.db import get_connection
    with get_connection() as _con:
        _con.execute(
            "UPDATE analysis_runs SET pdf_path = ? WHERE run_id = ?",
            [str(t_pdf), result.run_id],
        )


def _job_run_single(
    job_id: str, city: str, state: str, specialty: Optional[str],
    aggregate: bool = False, radius_miles: Optional[int] = None,
    entity_type: Optional[str] = None,
) -> None:
    job = _jobs[job_id]
    if job.get("individual_report") and job.get("entity_name"):
        # Every individual report (hospital / service line / practice / community health) runs
        # through ONE job so the post-run hooks live in one place (see _job_run_individual).
        return _job_run_individual(job_id, job["entity_name"], city, state, specialty, aggregate,
                                   radius_miles, entity_type or "hospital")
    _cost.begin(job_id, job.get("kind") or "")
    job["kind"] = "Deep Diagnostic" if job.get("individual_report") else "Competitors Rankings"
    job.setdefault("label", job.get("entity_name") or f"{city}, {state}")
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, set_run_role
        from perception.analyzer import analyze_location

        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        result = analyze_location(
            city=city, state=state, specialty=specialty, aggregate=aggregate,
            radius_miles=radius_miles, zip_code=job.get("zip_code"),
            patient_perspective=job.get("patient_perspective", False),
            # Individual reports: the main PDF is always the full report; the teaser is a
            # separate file (see _ensure_individual_teaser). Market runs keep the flag.
            teaser_report=bool(job.get("teaser_report", False)) and not job.get("individual_report"),
            simplified=job.get("simplified_patient", False),
            obscure_competitors=job.get("obscure_competitors", True),
            target_entity=job.get("target_entity"),
            entity_name=job.get("entity_name"),
            individual_report=job.get("individual_report", False),
            output_dir=REPORTS_DIR, on_event=emit,
            brand=job.get("brand", "original"),
            skip_pdf=job.get("skip_pdf", False),
            practice_composite=job.get("practice_composite", False),
            practice_roster=job.get("practice_roster") or [],
            physician_composite=job.get("physician_composite", False),
            physician_roster=job.get("physician_roster") or {},
            force_rerun=job.get("force_rerun", False),
            override_today_lock=job.get("override_today_lock", False),
            briefing_variant=job.get("briefing_variant"),
            entity_type=entity_type,
            report_title=job.get("report_title"),
            service_line=job.get("service_line"),
            parent_system=job.get("parent_system"),
        )
        if job.get("individual_report"):
            _plain_ensure(result, job)          # cached results too; before the teaser is built
            _ensure_individual_teaser(result, job)
            if result.rankings and job.get("entity_name"):
                from perception.graph import upsert_org as _g_upsert
                _p = result.rankings[0]
                _graph_write(_g_upsert, job["entity_name"], city, state, entity_type or "hospital", website=_p.website_url,
                             locations=[{"name": l.name, "address": l.address, "rating": l.google_rating, "review_count": l.google_review_count}
                                        for l in (_p.consolidated_locations or [])], source="analysis")
        else:
            _backfill_teaser_pdf(result, job)
        set_run_role(result.run_id, job["role"], _job_ran_by(job))

        # Single-hospital Deep Diagnostic: fold the content analysis + prescription
        # into the run (the standalone Content Analysis panel is retired). Market
        # reports, FQHC and skip-pdf data pulls are unchanged. Fail-soft.
        if (job.get("individual_report") and entity_type in (None, "hospital")
                and job.get("entity_name") and not job.get("skip_pdf")):
            if job.get("spotcheck"):
                _run_spotcheck(result, job["entity_name"], city, state, None, "hospital", emit)
            try:
                _finalize_hospital_combined(result, job["entity_name"], city, state,
                                            job.get("brand", "original"), job, emit)
            except Exception as _cexc:
                emit({"type": "text",
                      "text": f"\n⚠ Content analysis failed ({type(_cexc).__name__}: {_cexc}) — base report kept"})

        job["status"] = "done"
        job["result"] = {
            "run_id": result.run_id,
            "location": result.location,
            "specialty": result.specialty,
            "provider_count": len(result.rankings),
            "pdf_path": result.pdf_path,
            "briefing_pdf_path": result.briefing_pdf_path,
            "briefing_skipped_reason": result.briefing_skipped_reason,
            "entity_name": job.get("entity_name"),
            "entity_type": entity_type or "hospital",
            "city": city, "state": state,
            "individual_report": bool(job.get("individual_report")),
            "confidence": _run_confidence(result) if job.get("individual_report") else None,
            "rubric_note": getattr(result, "rubric_note", "") or None,
            "spotcheck": _spotcheck_brief(result),
        }
        if not job.get("skip_pdf"):
            _title = job.get("entity_name") or result.report_title or result.location
            _kind = "Deep Diagnostic" if job.get("individual_report") else "Competitors Rankings report"
            _notify_run_complete(job, _kind, _title, [result.pdf_path, result.teaser_pdf_path, result.briefing_pdf_path])
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)  # sentinel → closes SSE stream


def _finalize_hospital_combined(result, entity_name: str, city: str, state: str,
                                brand: str, job: dict, emit) -> None:
    """Fold the content analysis into a single-hospital Deep Diagnostic run:
    verified content checks → drafted prescription → Report 1 (Deep Diagnostic +
    Content Improvement Keys, replaces the base PDF) + Report 2 (detailed content
    report). Recorded as a content_analysis_runs row pointing at this run so the
    History Downloads menu surfaces both files. Fail-soft: on any error the base
    report is left intact."""
    from perception.db import (save_content_findings, _norm_entity_name, get_connection,
                               create_content_analysis_run, finalize_content_analysis_run,
                               set_content_analysis_drafted)
    from perception.content_analyzer import analyze_content
    from perception.content_drafting import draft_findings
    from perception.models import ContentFinding

    prov = result.rankings[0] if result.rankings else None
    urls = [u for u in (job.get("content_urls") or []) if (u or "").strip()]
    if not urls:
        try:
            from perception.data.places import fetch_provider
            from urllib.parse import urlsplit, urlunsplit
            _read, _ = fetch_provider(entity_name, city, state)
            if _read and _read.website:
                _s = urlsplit(_read.website)
                urls = [urlunsplit((_s.scheme, _s.netloc, _s.path, "", "")).rstrip("/")]
        except Exception:
            pass
    if not urls and prov is not None and prov.website_url:
        urls = [prov.website_url]

    rep = _build_practice_reputation(result)   # generic: per-location Google data from the base run
    saf = None
    if prov is not None:
        saf = {"entity_kind": "hospital", "name": entity_name,
               "leapfrog_grade": getattr(prov, "leapfrog_grade", None),
               "cms_star_rating": getattr(prov, "cms_star_rating", None)}
    emit({"type": "phase", "name": "content",
          "text": "Analyzing content: website, Wikidata, Wikipedia, reputation, and safety"})
    findings = analyze_content(entity_name, urls, city, state,
                               entity_kind="hospital", reputation=rep, safety=saf, on_event=emit)
    findings.run_id = result.run_id

    # Drafted prescription (always — same as the practice combined report / network Full Detail).
    snap = findings.source_snapshot or {}
    fdicts = [f.model_dump() for f in findings.findings]
    needs = [f for f in fdicts if not f.get("draft_content") and f.get("remediation_type")
             and f.get("status") != "not_assessed"]
    if needs:
        emit({"type": "phase", "name": "drafting", "text": "Drafting the content prescription"})
        facts = {"website_urls": snap.get("website_urls", []),
                 "wikidata_qid": snap.get("wikidata_qid"),
                 "wikipedia_article": snap.get("wikipedia_article")}
        try:
            drafts = draft_findings(entity_name, result.location, "hospital", facts, fdicts)
        except Exception:
            drafts = {}
        for f in fdicts:
            if f.get("finding_id") in drafts:
                f["draft_content"] = drafts[f["finding_id"]]
        findings.findings = [ContentFinding(**f) for f in fdicts]
    try:
        from perception.citations import attach_finding_reasons
        attach_finding_reasons(findings.findings, getattr(result, "spotcheck", None))
    except Exception as _cx:
        print(f"[citations] finding reasons failed: {type(_cx).__name__}: {_cx}")
    save_content_findings(result.run_id, _norm_entity_name(entity_name),
                          snap, [f.model_dump() for f in findings.findings], findings.status)
    for f in findings.findings:
        emit({"type": "text", "text": f"\n• [{f.severity}] {f.teaser_summary}"})

    # Persist as a content-analysis run bound to this base run (History reads it).
    ca_id = uuid.uuid4().hex[:12]
    loc = ", ".join([p for p in [city, state] if p])
    create_content_analysis_run(ca_id, entity_name, loc, "hospital", urls,
                                result.report_title or entity_name, job.get("role", ""))

    emit({"type": "phase", "name": "pdf", "text": "Building the report with Content Improvement Keys"})
    report1 = ""
    try:
        from perception.pdf import render_content_deep_dive
        _r1 = REPORTS_DIR / f"content_{ca_id}_report1.pdf"
        render_content_deep_dive(result, _r1, findings, brand=brand)
        report1 = str(_r1)
    except Exception as _pe:
        emit({"type": "text", "text": f"\n(augmented report render failed: {type(_pe).__name__}) — using base report"})
        report1 = result.pdf_path or ""
    report2 = ""
    try:
        from perception.content_report_pdf import render_content_report_pdf
        _r2 = REPORTS_DIR / f"content_{ca_id}_report2.pdf"
        render_content_report_pdf(entity_name, loc, findings, str(_r2),
                                  report_title=result.report_title or entity_name)
        report2 = str(_r2)
    except Exception as _pe2:
        emit({"type": "text", "text": f"\n(content report render failed: {type(_pe2).__name__})"})
    finalize_content_analysis_run(ca_id, result.run_id, findings.status,
                                  len(findings.findings), report1, report2)
    if report2 and needs:
        try:
            set_content_analysis_drafted(ca_id, report2)
        except Exception:
            pass
    # The augmented report becomes THE report for this run.
    if report1 and report1 != (result.pdf_path or ""):
        result.pdf_path = report1
        with get_connection() as con:
            con.execute("UPDATE analysis_runs SET pdf_path = ? WHERE run_id = ?",
                        [report1, result.run_id])


def _facts_evidence(facts: Optional[dict]) -> str:
    """Evidence-block text for owner-attested facts (item 3)."""
    if not facts:
        return ""
    lines = ["", "=== Owner-attested facts (supplied by the practice — treat as true) ==="]
    if facts.get("profiles_claimed"):
        lines.append("Google Business Profiles: CLAIMED and actively managed by the practice"
                     + (" (via RLDatix Reputation Management)" if facts.get("managed_by_rldatix") else "")
                     + ". Do NOT describe profiles as unclaimed; assess whether that ownership is VISIBLE to AI assistants (consistent NAP, website links, owner responses, recency) instead.")
    if facts.get("reviews_since"):
        lines.append(f"Review-invitation program active since {facts['reviews_since']} — expect rising review recency/volume; do not treat a thin count as neglect.")
    if facts.get("locations"):
        lines.append(f"The practice operates {facts['locations']} locations. If fewer surface online, that is a visibility gap, not a smaller practice.")
    if facts.get("notes"):
        lines.append(f"Practice note: {str(facts['notes'])[:300]}")
    return "\n".join(lines) + "\n"


def _profile_audit(result, job: dict, emit=None) -> None:
    """Item 2: what is ACTUALLY on each confirmed Google Business Profile (Place Details):
    website link (and whether it matches the practice domain), phone, hours, photos,
    status, review count. Stored on result.profile_audit; feeds the content-analysis
    reputation finding and a PDF block. Fail-soft."""
    try:
        from urllib.parse import urlparse
        from perception.data.places import place_details
        roster = []
        a = job.get("anchor_listing") or {}
        if a.get("place_id"):
            roster.append({"name": result.entity_name, "city": job.get("city") or "", "place_id": a["place_id"]})
        for sbl in (job.get("confirmed_siblings") or []):
            if sbl.get("place_id"):
                roster.append({"name": sbl.get("original_name") or sbl.get("name"), "city": sbl.get("city") or "", "place_id": sbl["place_id"]})
        if not roster:
            return
        if emit:
            emit({"type": "text", "text": f"\nChecking {len(roster)} Google Business Profile{'s' if len(roster) != 1 else ''} (website, phone, hours, photos, status)…"})
        def _dom(u):
            try:
                return urlparse(u if "://" in str(u) else "https://" + str(u)).netloc.lower().replace("www.", "") if u else ""
            except Exception:
                return ""
        site = (result.rankings[0].website_url if result.rankings else None) or ((job.get("content_urls") or [None])[0])
        org_domain = _dom(site)
        profiles = []
        for r in roster[:25]:
            d = place_details(r["place_id"]) or {}
            web = d.get("websiteUri") or ""
            wd = _dom(web)
            profiles.append({"name": r["name"], "city": r["city"], "place_id": r["place_id"],
                             "website": web, "website_domain": wd,
                             "has_phone": bool(d.get("nationalPhoneNumber")), "has_hours": bool(d.get("regularOpeningHours")),
                             "photos": len(d.get("photos") or []), "rating": d.get("rating"), "review_count": d.get("userRatingCount"),
                             "status": d.get("businessStatus") or "", "primary_type": d.get("primaryType") or "", "found": bool(d)})
        if not org_domain:
            doms = [p["website_domain"] for p in profiles if p["website_domain"]]
            org_domain = max(set(doms), key=doms.count) if doms else ""
        for p in profiles:
            p["domain_matches"] = bool(org_domain and p["website_domain"] and (p["website_domain"] == org_domain or p["website_domain"].endswith("." + org_domain)))
        found = [p for p in profiles if p["found"]]
        summary = {"checked": len(found), "requested": len(roster), "linked": sum(1 for p in found if p["domain_matches"]),
                   "with_website": sum(1 for p in found if p["website"]), "with_phone": sum(1 for p in found if p["has_phone"]),
                   "with_hours": sum(1 for p in found if p["has_hours"]), "with_photos": sum(1 for p in found if (p["photos"] or 0) >= 3),
                   "thin_reviews": sum(1 for p in found if (p["review_count"] or 0) < 5),
                   "not_operational": sum(1 for p in found if p["status"] and p["status"] != "OPERATIONAL"),
                   "total_reviews": sum(int(p["review_count"] or 0) for p in found)}
        result.profile_audit = {"domain": org_domain, "profiles": profiles, "summary": summary,
                                "owner_attested": bool((job.get("practice_facts") or {}).get("profiles_claimed"))}
        if emit:
            s = summary
            emit({"type": "text", "text": f"\nProfiles checked: {s['checked']} · linked to {org_domain or 'the practice site'}: {s['linked']} · hours: {s['with_hours']} · phone: {s['with_phone']} · under 5 reviews: {s['thin_reviews']}\n"})
    except Exception as exc:
        print(f"[profile-audit] failed: {type(exc).__name__}: {exc}")


def _build_practice_reputation(result) -> Optional[dict]:
    """Reputation input for the practice content analysis, from the base diagnostic's
    verified Google data across ALL of the practice's locations (no new crawling)."""
    prov = result.rankings[0] if result.rankings else None
    if prov is None:
        return None
    fp = prov.google_footprint
    agg = fp.system_aggregate if fp else None
    fd = fp.front_door if fp else None
    agg_rating = (agg.rating if (agg and agg.rating is not None) else None)
    agg_count = (agg.total_reviews if (agg and agg.rating is not None) else None)
    if agg_rating is None and fd and getattr(fd, "verified", False) and fd.rating is not None:
        agg_rating, agg_count = fd.rating, fd.count
    return {
        "locations": [
            {"name": l.name, "google_rating": l.google_rating,
             "google_review_count": l.google_review_count, "address": l.address}
            for l in (prov.consolidated_locations or [])
        ],
        "footprint": {"rating_range": (fp.rating_range if fp else ""),
                      "consistency": (fp.consistency if fp else "")},
        "aggregate_rating": agg_rating,
        "aggregate_count": agg_count,
        "profile_audit": getattr(result, "profile_audit", None),
        "owner_facts": getattr(result, "owner_facts", None),
    }


def _finalize_practice_combined(result, entity_name: str, city: str, state: str,
                                brand: str, job: dict, emit) -> None:
    """Run the practice content analysis + drafting, synthesize a findings-citing
    Diagnostic Assessment, and render the combined practice report (four-pillar +
    Assessment + embedded Content Report/prescription), replacing the base PDF.
    Fail-soft: on any error the base report is left intact."""
    from perception.db import (save_content_findings, _norm_entity_name, get_connection)
    from perception.content_analyzer import analyze_content
    from perception.content_drafting import draft_findings
    from perception.practice_assessment import synthesize_assessment
    from perception.pdf import render_practice_combined
    from perception.models import ContentFinding

    prov = result.rankings[0] if result.rankings else None
    urls = [u for u in (job.get("content_urls") or []) if (u or "").strip()]
    if not urls:
        # Prefer the AUTHORITATIVE website from the resolved Google Places listing
        # over the LLM-guessed prov.website_url (which invents plausible-but-wrong
        # domains for practices with non-obvious sites, e.g. doclv.com).
        try:
            from perception.data.places import fetch_provider
            from urllib.parse import urlsplit, urlunsplit
            _read, _ = fetch_provider(entity_name, city, state)
            if _read and _read.website:
                # Drop tracking query/fragment (Places often appends UTM params) so
                # content checks hit the clean base site.
                _s = urlsplit(_read.website)
                urls = [urlunsplit((_s.scheme, _s.netloc, _s.path, "", "")).rstrip("/")]
        except Exception:
            pass
    if not urls and prov is not None and prov.website_url:
        urls = [prov.website_url]

    rep = _build_practice_reputation(result)
    emit({"type": "phase", "name": "content",
          "text": "Analyzing content: website, Wikidata, Wikipedia, and listings/reputation"})
    findings = analyze_content(entity_name, urls, city, state,
                               entity_kind="practice", reputation=rep, on_event=emit)
    findings.run_id = result.run_id

    # Draft the full prescription (always — the practice report includes it).
    snap = findings.source_snapshot or {}
    fdicts = [f.model_dump() for f in findings.findings]
    needs = [f for f in fdicts if not f.get("draft_content") and f.get("remediation_type")
             and f.get("status") != "not_assessed"]
    if needs:
        emit({"type": "phase", "name": "drafting", "text": "Drafting the content prescription"})
        facts = {"website_urls": snap.get("website_urls", []), "specialty": result.specialty,
                 "wikidata_qid": snap.get("wikidata_qid"), "wikipedia_article": snap.get("wikipedia_article")}
        try:
            drafts = draft_findings(entity_name, result.location, "practice", facts, fdicts)
        except Exception:
            drafts = {}
        for f in fdicts:
            if f.get("finding_id") in drafts:
                f["draft_content"] = drafts[f["finding_id"]]
        findings.findings = [ContentFinding(**f) for f in fdicts]
    try:
        from perception.citations import attach_finding_reasons
        attach_finding_reasons(findings.findings, getattr(result, "spotcheck", None))
    except Exception as _cx:
        print(f"[citations] finding reasons failed: {type(_cx).__name__}: {_cx}")
    save_content_findings(result.run_id, _norm_entity_name(entity_name),
                          snap, [f.model_dump() for f in findings.findings], findings.status)

    # Assessment synthesis (cites the findings) → replaces top_recommendation.
    emit({"type": "phase", "name": "assessment", "text": "Writing the diagnostic assessment"})
    result.top_recommendation = synthesize_assessment(
        entity_name, result.location, result.top_recommendation,
        result.ai_visibility_verdict or result.top_recommendation, findings.findings)
    # The synthesized assessment is a paragraph again — condense it back to "what to do first".
    from perception.plain import condense as _plain_condense
    _plain_condense(result, only_assessment=True)

    # Render the combined report, replacing the base PDF. Force teaser_report off so
    # the full render never uses the legacy blurred-card teaser layout — content_teaser
    # controls the (separate) teaser instead.
    result.teaser_report = False
    emit({"type": "phase", "name": "pdf", "text": "Building the combined practice report"})
    from pathlib import Path as _Path
    # Write next to the base PDF so callers that use their own output folder
    # (Event Prep writes into the event's directory) keep everything together.
    _out_dir = _Path(result.pdf_path).parent if result.pdf_path else REPORTS_DIR
    combined = _out_dir / f"{_Path(result.pdf_path).stem if result.pdf_path else result.run_id}.pdf"
    render_practice_combined(result, findings, str(combined), brand=brand)
    result.pdf_path = str(combined)

    # Teaser (opt-in): same combined report with the content analysis + prescription
    # blurred behind a gate; the score, ratings, and Assessment stay visible.
    if job.get("teaser_report"):
        teaser_path = _out_dir / f"{combined.stem}_teaser.pdf"
        try:
            render_practice_combined(result, findings, str(teaser_path), brand=brand, teaser=True)
            result.teaser_pdf_path = str(teaser_path)
        except Exception:
            pass

    with get_connection() as con:
        con.execute("UPDATE analysis_runs SET pdf_path = ?, teaser_pdf_path = ? WHERE run_id = ?",
                    [str(combined), result.teaser_pdf_path, result.run_id])


def _job_run_practice(
    job_id: str, entity_name: str, city: str, state: str,
    specialty: Optional[str] = None, aggregate: bool = False,
    radius_miles: Optional[int] = None,
) -> None:
    """Kept for callers; every individual report runs through _job_run_individual."""
    _job_run_individual(job_id, entity_name, city, state, specialty, aggregate, radius_miles,
                        "service_line" if _jobs[job_id].get("service_line") else "practice")


def _job_run_fqhc(
    job_id: str, entity_name: str, city: str, state: str,
    aggregate: bool = False,
) -> None:
    """Kept for callers; every individual report runs through _job_run_individual."""
    _job_run_individual(job_id, entity_name, city, state, None, aggregate, None, "community_health")


# ── ONE job for every individual report (heart-surgery item 6, increment A) ──────────────
# The model step still goes to the type-specific analyzer; everything around it — cost
# metering, plain-language pass, teaser, attribution, profile audit, entity-graph write,
# observed spot-check, content analysis, re-save, result dict, completion email — is
# defined ONCE here and gated by entity type. New hooks are added in one place.

_INDIVIDUAL_KINDS = {"hospital": "Deep Diagnostic", "service_line": "Deep Diagnostic",
                     "practice": "Deep Diagnostic", "community_health": "Community Health"}


def _run_type_analyzer(etype: str, job: dict, entity_name: str, city: str, state: str,
                       specialty: Optional[str], aggregate: bool, radius_miles: Optional[int], emit):
    """Dispatch the individual report and return its AnalysisResult.

    Default: the unified pipeline (perception.pipeline.run_individual — one phase list,
    entity type as a parameter). PULSE_PIPELINE=legacy selects the three original
    analyzers instead; both receive exactly the same job-derived arguments."""
    common = dict(output_dir=REPORTS_DIR, on_event=emit, brand=job.get("brand", "original"),
                  skip_pdf=job.get("skip_pdf", False), force_rerun=job.get("force_rerun", False),
                  override_today_lock=job.get("override_today_lock", False),
                  briefing_variant=job.get("briefing_variant"), report_title=job.get("report_title"))
    if os.environ.get("PULSE_PIPELINE", "unified") != "legacy":
        from perception.pipeline import run_individual
        return run_individual(
            etype, entity_name, city, state, specialty=specialty, aggregate=aggregate, teaser_report=False,
            radius_miles=radius_miles, zip_code=job.get("zip_code"),
            patient_perspective=job.get("patient_perspective", False),
            simplified=job.get("simplified_patient", False),
            obscure_competitors=job.get("obscure_competitors", True), target_entity=job.get("target_entity"),
            practice_composite=job.get("practice_composite", False), practice_roster=job.get("practice_roster") or [],
            physician_composite=job.get("physician_composite", False), physician_roster=job.get("physician_roster") or {},
            service_line=job.get("service_line"), parent_system=job.get("parent_system"),
            practice_profile=job.get("practice_profile"), confirmed_siblings=job.get("confirmed_siblings"),
            org_name=job.get("org_name"), anchor_listing=job.get("anchor_listing"),
            extra_evidence=_facts_evidence(job.get("practice_facts")),
            fqhc_intake=job.get("fqhc_intake"), site_roster=job.get("site_roster") or [], **common)
    if etype == "community_health":
        from perception.fqhc_analyzer import analyze_fqhc
        return analyze_fqhc(entity_name=entity_name, city=city, state=state, fqhc_intake=job.get("fqhc_intake"),
                            aggregate=aggregate, site_roster=job.get("site_roster") or [],
                            teaser_report=False, **common)
    if etype in ("practice", "service_line"):
        from perception.practice_analyzer import analyze_practice
        return analyze_practice(entity_name=entity_name, city=city, state=state, specialty=specialty, aggregate=aggregate,
                                practice_profile=job.get("practice_profile"), teaser_report=False,
                                practice_composite=job.get("practice_composite", False),
                                practice_roster=job.get("practice_roster") or [],
                                physician_composite=job.get("physician_composite", False),
                                physician_roster=job.get("physician_roster") or {},
                                confirmed_siblings=job.get("confirmed_siblings"), org_name=job.get("org_name"),
                                service_line=job.get("service_line"), parent_system=job.get("parent_system"),
                                anchor_listing=job.get("anchor_listing"),
                                extra_evidence=_facts_evidence(job.get("practice_facts")), **common)
    from perception.analyzer import analyze_location
    return analyze_location(city=city, state=state, specialty=specialty, aggregate=aggregate,
                            radius_miles=radius_miles, zip_code=job.get("zip_code"),
                            patient_perspective=job.get("patient_perspective", False),
                            teaser_report=False,            # main PDF is the full report; the teaser is a separate file
                            simplified=job.get("simplified_patient", False),
                            obscure_competitors=job.get("obscure_competitors", True),
                            target_entity=job.get("target_entity"), entity_name=entity_name, individual_report=True,
                            practice_composite=job.get("practice_composite", False),
                            practice_roster=job.get("practice_roster") or [],
                            physician_composite=job.get("physician_composite", False),
                            physician_roster=job.get("physician_roster") or {},
                            entity_type=etype, service_line=job.get("service_line"),
                            parent_system=job.get("parent_system"), **common)


def _job_run_individual(job_id: str, entity_name: str, city: str, state: str,
                        specialty: Optional[str], aggregate: bool, radius_miles: Optional[int],
                        etype: str) -> None:
    job = _jobs[job_id]
    etype = etype if etype in _INDIVIDUAL_KINDS else "hospital"
    is_practice = etype in ("practice", "service_line")
    is_fqhc = etype == "community_health"
    _cost.begin(job_id, job.get("kind") or "")
    job["kind"] = _INDIVIDUAL_KINDS[etype]
    job.setdefault("label", entity_name)
    job["entity_type"] = etype
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, set_run_role
        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        result = _run_type_analyzer(etype, job, entity_name, city, state, specialty, aggregate, radius_miles, emit)

        # ── post-run hooks, in one order for every type ──
        if is_practice:
            result.owner_facts = job.get("practice_facts") or None
        _plain_ensure(result, job)                      # cached results too; before any re-render
        if is_practice:
            _profile_audit(result, job, emit)            # Place Details per confirmed profile
        if not is_practice:
            _ensure_individual_teaser(result, job)       # practice teaser comes from the combined render below
        # entity graph: practices refresh location details from the audit; hospitals store the
        # related campuses the analysis found (unconfirmed candidates); FQHC sites came in via the form
        try:
            from perception.graph import upsert_org as _g_upsert
            if is_practice and result.profile_audit and result.profile_audit.get("profiles"):
                _graph_write(_g_upsert, entity_name, city, state, etype,
                             website=(result.rankings[0].website_url if result.rankings else None),
                             locations=[{"name": p["name"], "city": p.get("city"), "place_id": p.get("place_id"), "website": p.get("website")}
                                        for p in result.profile_audit["profiles"] if p.get("place_id")], source="analysis")
            elif etype == "hospital" and result.rankings:
                _p = result.rankings[0]
                _graph_write(_g_upsert, entity_name, city, state, "hospital", website=_p.website_url,
                             locations=[{"name": l.name, "address": l.address, "rating": l.google_rating, "review_count": l.google_review_count}
                                        for l in (_p.consolidated_locations or [])], source="analysis")
        except Exception as _gx:
            print(f"[graph] post-run write failed: {type(_gx).__name__}: {_gx}", flush=True)
        if job.get("roster_from_graph"):
            emit({"type": "text", "text": "\nLocations taken from your confirmed roster (entity graph) — no discovery needed."})

        set_run_role(result.run_id, job["role"], _job_ran_by(job))

        if not job.get("skip_pdf"):
            if job.get("spotcheck") and not is_fqhc:
                _run_spotcheck(result, entity_name, city, state, specialty if is_practice else None, etype, emit)
            try:
                if is_practice:
                    _finalize_practice_combined(result, entity_name, city, state, job.get("brand", "original"), job, emit)
                elif etype == "hospital":
                    _finalize_hospital_combined(result, entity_name, city, state, job.get("brand", "original"), job, emit)
            except Exception as _cexc:
                emit({"type": "text", "text": f"\n⚠ Content analysis failed ({type(_cexc).__name__}: {_cexc}) — base report kept"})
        try:
            from perception.analyzer import _save_to_db as _resave
            _resave(result)          # persist audit / facts / spotcheck / sources / plain text into result_json
        except Exception as _se:
            print(f"[{etype}] result re-save failed: {_se}", flush=True)

        job["status"] = "done"
        job["result"] = {
            "run_id": result.run_id, "location": result.location, "specialty": result.specialty,
            "provider_count": len(result.rankings) or (1 if is_fqhc else 0),
            "pdf_path": result.pdf_path, "teaser_pdf_path": result.teaser_pdf_path,
            "briefing_pdf_path": result.briefing_pdf_path, "briefing_skipped_reason": result.briefing_skipped_reason,
            "entity_name": entity_name, "entity_type": etype,
            "service_line": job.get("service_line"), "parent_system": job.get("parent_system"),
            "city": city, "state": state, "individual_report": True,
            "confidence": _run_confidence(result),
            "rubric_note": getattr(result, "rubric_note", "") or None,
            "spotcheck": _spotcheck_brief(result),
            "mqcr": getattr(result, "fqhc_mqcr", None) if is_fqhc else None,
        }
        if not job.get("skip_pdf"):
            _notify_run_complete(job, "Community Health report" if is_fqhc else "Deep Diagnostic",
                                 result.report_title or entity_name,
                                 [result.pdf_path, result.teaser_pdf_path, result.briefing_pdf_path])
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


def _job_run_battery(job_id: str, fqhc_run_id: str, entity_name: str, city: str, state: str) -> None:
    job = _jobs[job_id]
    _cost.begin(job_id, job.get("kind") or "")
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, get_connection
        from perception.fqhc_battery import run_battery
        from perception.models import AnalysisResult

        init_db()
        battery = run_battery(
            fqhc_run_id=fqhc_run_id,
            entity_name=entity_name,
            city=city,
            state=state,
            on_event=emit,
        )

        # Re-render PDF with MQCR populated
        new_pdf_path = None
        try:
            emit({"type": "phase", "name": "pdf", "text": "Re-rendering PDF with MQCR results"})
            with get_connection() as con:
                row = con.execute(
                    "SELECT result_json, pdf_path FROM analysis_runs WHERE run_id = ?",
                    [fqhc_run_id],
                ).fetchone()
            if row and row[0]:
                ar = AnalysisResult.model_validate_json(row[0])
                ar.fqhc_mqcr = battery.mqcr
                # Update battery-derived sub-scores and recompute composite
                if ar.fqhc_pillar_scores is not None:
                    from perception.fqhc_scoring import mqcr_to_score as _m2s, composite as _fqhc_composite
                    from perception.scoring import grade_from_score as _gfs
                    ar.fqhc_pillar_scores.mqcr_score = _m2s(battery.mqcr)
                    if battery.multilingual_mqcr is not None:
                        ar.fqhc_pillar_scores.multilingual_score = _m2s(battery.multilingual_mqcr)
                    new_score = _fqhc_composite(ar.fqhc_pillar_scores.as_dict())
                    if ar.rankings and new_score is not None:
                        ar.rankings[0].ai_visibility_score = new_score
                        ar.rankings[0].overall_rating, _ = _gfs(new_score)
                old_pdf = row[1] or ""
                from pathlib import Path as _Path
                REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                # Reuse same filename stem so download links stay valid
                if old_pdf and _Path(old_pdf).exists():
                    new_pdf_path = old_pdf
                else:
                    slug = re.sub(r"[^a-z0-9]+", "-", (entity_name or "report").lower()).strip("-")
                    new_pdf_path = str(REPORTS_DIR / f"{slug}-community-health-mqcr.pdf")
                from perception.fqhc_pdf import render_fqhc_pdf
                render_fqhc_pdf(ar, new_pdf_path)
                with get_connection() as con:
                    con.execute(
                        "UPDATE analysis_runs SET pdf_path = ?, result_json = ? WHERE run_id = ?",
                        [new_pdf_path, ar.model_dump_json(), fqhc_run_id],
                    )
        except Exception:
            pass  # PDF re-render failure is non-fatal

        job["status"] = "done"
        job["result"] = {
            "run_id": fqhc_run_id,
            "mqcr": battery.mqcr,
            "surfaced_count": battery.surfaced_count,
            "total": battery.total,
            "pdf_path": new_pdf_path,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


def _job_run_batch(job_id: str, groups: List[dict]) -> None:
    job = _jobs[job_id]
    _cost.begin(job_id, job.get("kind") or "")
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, set_run_role
        from perception.analyzer import analyze_location

        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        results = []
        total = len(groups)
        for i, g in enumerate(groups):
            g["city"] = _normalize_input(g.get("city")) or g.get("city", "")
            g["specialty"] = _normalize_input(g.get("specialty"))
            loc = f"{g['city']}, {g['state']}"
            if g.get("specialty"):
                loc += f" — {g['specialty']}"
            emit({"type": "batch_item", "current": i + 1, "total": total, "location": loc})
            result = analyze_location(
                city=g["city"], state=g["state"], specialty=g.get("specialty"),
                output_dir=REPORTS_DIR, on_event=emit,
                brand=job.get("brand", "original"),
            )
            set_run_role(result.run_id, job["role"], _job_ran_by(job))
            results.append({
                "run_id": result.run_id,
                "location": result.location,
                "specialty": result.specialty,
                "provider_count": len(result.rankings),
                "pdf_path": result.pdf_path,
            })

        job["status"] = "done"
        job["results"] = results
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


def _new_job(role: str, brand: str = "original", email: Optional[str] = None) -> str:
    job_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = {"status": "running", "loop": loop, "queue": queue, "role": role,
                     "brand": brand, "email": email, "started_at": time.time(),
                     "owner": _owner_key(email, role)}
    return job_id


def _owner_key(email: Optional[str], role: Optional[str]) -> str:
    """Who a job belongs to for the Home 'Your analysis runs' list: the signed-in email, or —
    for password sessions (admin / partner) that carry no email — the shared role."""
    return (email or "").lower() or f"role:{role or 'user'}"


def _job_ran_by(job: dict) -> Optional[str]:
    """History attribution: the launcher, or (scheduled Trends snapshots) the tracking owner."""
    return job.get("ran_by") or job.get("email") or None


def _plain_ensure(result, job: dict) -> None:
    """Make sure the executive sections are condensed on EVERY path — including results served
    from the same-day lock or the 30-day cache, whose stored text and PDF pre-date the pass.
    When anything changed and a main PDF exists, re-render it in place and re-save. Fail-soft."""
    try:
        from perception.plain import condense
        if not getattr(result, "individual_report", False) or not condense(result):
            return
        if result.pdf_path and not job.get("skip_pdf"):
            try:
                if (result.entity_type or "") == "community_health":
                    from perception.fqhc_pdf import render_fqhc_pdf
                    render_fqhc_pdf(result, str(result.pdf_path), brand=job.get("brand", "original"))
                else:
                    from perception.pdf import render_pdf
                    render_pdf(result, Path(result.pdf_path), brand=job.get("brand", "original"))
            except Exception as exc:
                print(f"[plain] re-render failed run={result.run_id}: {type(exc).__name__}: {exc}")
        from perception.analyzer import _save_to_db as _resave_plain
        _resave_plain(result)
    except Exception as exc:
        print(f"[plain] ensure failed run={getattr(result, 'run_id', '?')}: {type(exc).__name__}: {exc}")


def _run_confidence(result) -> Optional[dict]:
    """Compute + persist the evidence level behind a Deep Diagnostic score. Fail-soft."""
    try:
        from perception.confidence import score_confidence
        from perception.db import set_run_confidence
        conf = score_confidence(result)
        if conf:
            set_run_confidence(result.run_id, conf["level"], conf["note"])
        return conf
    except Exception as exc:
        print(f"[confidence] failed: {type(exc).__name__}: {exc}")
        return None


def _run_spotcheck(result, entity_name: str, city: str, state: str, specialty, entity_type: str, emit) -> None:
    """Observed assistant check for an individual report (fail-soft, ~30–60 s, ~$0.30–0.60).
    Off with SPOTCHECK_ENABLED=0."""
    if os.environ.get("SPOTCHECK_ENABLED", "1") in ("0", "false", "no"):
        return
    try:
        from perception.spotcheck import run_spotcheck, summary_sentence
        from perception.db import set_run_spotcheck
        aliases = [r.get("name") for r in (result.practice_composite_rows or []) if r.get("name")]
        aliases += [getattr(result, "report_title", None) or ""]
        website = result.rankings[0].website_url if result.rankings else None
        sc = run_spotcheck(entity_name, city, state, specialty, entity_type, aliases=aliases, website=website, emit=emit)
        result.spotcheck = sc
        set_run_spotcheck(result.run_id, sc)
        try:
            from perception.citations import attach_roadmap_reasons
            _n_reasons = attach_roadmap_reasons(result)
            if emit and _n_reasons:
                emit({"type": "text", "text": f"\nCitation evidence attached to {_n_reasons} roadmap item{'s' if _n_reasons != 1 else ''}."})
        except Exception as _cx:
            print(f"[citations] roadmap reasons failed: {type(_cx).__name__}: {_cx}")
        line = summary_sentence(sc)
        if emit and line:
            emit({"type": "text", "text": f"\nObserved check: {line}"})
    except Exception as exc:
        print(f"[spotcheck] failed: {type(exc).__name__}: {exc}")


def _spotcheck_brief(result) -> Optional[dict]:
    sc = getattr(result, "spotcheck", None)
    if not sc:
        return None
    from perception.spotcheck import summary_sentence
    return {"asked": sc.get("asked"), "mentioned": sc.get("mentioned"), "unprompted_asked": sc.get("unprompted_asked"),
            "unprompted_mentioned": sc.get("unprompted_mentioned"), "assistants": sc.get("assistants"),
            "per_assistant": sc.get("per_assistant"), "top_competitors": sc.get("top_competitors"),
            "our_domain_cited": sc.get("our_domain_cited"), "summary": summary_sentence(sc),
            "sourcing": (sc.get("sourcing") or {}).get("sentence") or "",
            "passes": sc.get("passes"), "queries": sc.get("queries"), "rate_range": sc.get("rate_range")}


def _notify_run_complete(job: dict, kind: str, title: str, files: list) -> None:
    """Email the person who started a long run when it finishes (per-user preference,
    default on). Fail-soft: never affects the run."""
    try:
        email = job.get("email")
        if not email:
            return
        from perception.db import get_notify_pref
        from perception.email_utils import send_run_complete
        if not get_notify_pref(email):
            return
        minutes = (time.time() - job.get("started_at", time.time())) / 60.0
        job["label"] = title
        send_run_complete(email, kind, title, [p for p in (files or []) if p], minutes=minutes)
    except Exception as exc:
        print(f"[notify] failed: {type(exc).__name__}: {exc}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _zip_to_city_state(zip_code: str) -> tuple[str, str]:
    """Resolve a US ZIP code to (city, state_abbr) using the free zippopotam.us API."""
    url = f"https://api.zippopotam.us/us/{zip_code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Rank2/1.0"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read())
    place = data["places"][0]
    return place["place name"], place["state abbreviation"]


class EntitySearchRequest(BaseModel):
    name: str
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None


class AnalyzeRequest(BaseModel):
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    radius_miles: int = 25
    specialty: Optional[str] = None
    aggregate: bool = False
    patient_perspective: bool = False
    teaser_report: bool = False
    simplified_patient: bool = False            # Simplified Patient Pulse (compact 2-block cards)
    obscure_competitors: bool = True            # True=Enticement (obscure non-targets); False=Market Summary (all clear)
    target_entity: Optional[str] = None         # prospect/target entity name (enticement mode)
    service_line: Optional[str] = None          # enticement target is a hospital service line
    parent_system: Optional[str] = None
    entity_name: Optional[str] = None
    report_title: Optional[str] = None          # display override for PDF title
    org_name: Optional[str] = None              # parent org brand name; drives prompt subject in org mode
    confirmed_siblings: Optional[List[dict]] = None  # pre-confirmed location list from initiation flow; None = run discovery inside analyzer
    anchor_listing: Optional[dict] = None   # the Google candidate picked in the search step (place_id, address, rating, review_count, maps_url)
    individual_report: bool = False
    skip_pdf: bool = False
    content_urls: List[str] = []            # user-supplied website URL(s) for the content analysis
    entity_type: Optional[str] = None       # "practice" routes to practice_analyzer
    practice_profile: Optional[str] = None  # override auto-classified profile
    practice_composite: bool = False        # append practice reputation table
    practice_roster: List[dict] = []        # confirmed practice list for reputation collection
    physician_composite: bool = False       # include physician sub-rows in practice composite
    physician_roster: dict = {}             # {practice_name: [{name, npi, specialty, credential}]}
    practice_facts: Optional[dict] = None   # owner-attested: {profiles_claimed: bool, reviews_since: 'YYYY-MM-DD', locations: int, notes: str}
    spotcheck: bool = False                 # opt-in: ask real AI assistants ~30 questions × 2 passes (~$3, ~5 min)
    force_rerun: bool = False               # bypass 90-day score cache
    override_today_lock: bool = False       # admin only: bypass same-day cache lock and regenerate
    briefing_variant: Optional[str] = None  # "sales" | "cs" | None — generates Pulse Briefing companion
    # Community Health Edition fields
    fqhc_intake: Optional[dict] = None     # client-attested intake facts
    fqhc_site_roster: Optional[List[str]] = None  # confirmed site names for aggregate runs


class BatchRequest(BaseModel):
    groups: List[AnalyzeRequest]


class CompareRequest(BaseModel):
    entity_a_name: str
    city_a: str
    state_a: str
    specialty_a: Optional[str] = None
    aggregate_a: bool = True
    entity_b_name: str
    city_b: str
    state_b: str
    specialty_b: Optional[str] = None
    aggregate_b: bool = True
    teaser_report: bool = False
    entity_type_a: Optional[str] = None    # "practice" or None/hospital
    entity_type_b: Optional[str] = None
    practice_profile_a: Optional[str] = None
    practice_profile_b: Optional[str] = None
    practice_composite_a: bool = False
    practice_composite_b: bool = False
    practice_roster_a: List[dict] = []
    practice_roster_b: List[dict] = []
    service_line_a: Optional[str] = None    # analyze this side as a hospital service line
    parent_system_a: Optional[str] = None
    service_line_b: Optional[str] = None
    parent_system_b: Optional[str] = None
    force_rerun_a: bool = False
    force_rerun_b: bool = False
    override_today_lock: bool = False       # admin only: bypass same-day cache lock and regenerate
    confirmed_siblings_a: Optional[List[dict]] = None   # practice types: confirmed Locations list (None = discover inside)
    confirmed_siblings_b: Optional[List[dict]] = None
    anchor_listing_a: Optional[dict] = None             # flagship Google listing picked in the search step
    anchor_listing_b: Optional[dict] = None


@app.post("/api/analyze")
async def start_analysis(req: AnalyzeRequest, payload: dict = Depends(get_current_user_payload)):
    role  = payload["role"]
    brand = payload.get("brand", "original")
    city, state = req.city, req.state
    radius = None

    if req.zip_code:
        try:
            city, state = _zip_to_city_state(req.zip_code)
            radius = req.radius_miles
        except Exception as exc:
            raise HTTPException(400, f"Could not resolve ZIP code {req.zip_code}: {exc}")
    elif not city or not state:
        raise HTTPException(400, "Provide either city+state or zip_code.")

    city = _normalize_input(city)
    specialty = _normalize_input(req.specialty)
    entity_name = _normalize_input(req.entity_name)

    job_id = _new_job(role, brand, payload.get("email"))
    _jobs[job_id]["zip_code"] = req.zip_code if req.zip_code else None
    _jobs[job_id]["patient_perspective"] = req.patient_perspective
    _jobs[job_id]["teaser_report"] = req.teaser_report
    _jobs[job_id]["simplified_patient"] = req.simplified_patient
    _jobs[job_id]["obscure_competitors"] = req.obscure_competitors
    _jobs[job_id]["target_entity"] = _normalize_input(req.target_entity) if req.target_entity else None
    _jobs[job_id]["service_line"] = req.service_line
    _jobs[job_id]["parent_system"] = req.parent_system
    _jobs[job_id]["entity_name"] = entity_name
    _jobs[job_id]["individual_report"] = req.individual_report
    _jobs[job_id]["skip_pdf"] = req.skip_pdf
    _jobs[job_id]["entity_type"] = req.entity_type
    _jobs[job_id]["practice_profile"] = req.practice_profile
    _jobs[job_id]["practice_composite"] = req.practice_composite
    _jobs[job_id]["practice_roster"] = req.practice_roster
    _jobs[job_id]["physician_composite"] = req.physician_composite
    _jobs[job_id]["physician_roster"] = req.physician_roster
    _jobs[job_id]["force_rerun"] = req.force_rerun
    _jobs[job_id]["override_today_lock"] = req.override_today_lock and (role == "admin")
    _jobs[job_id]["briefing_variant"] = req.briefing_variant
    _jobs[job_id]["report_title"] = _normalize_input(req.report_title) if req.report_title else None
    _jobs[job_id]["org_name"] = _normalize_input(req.org_name) if req.org_name else None
    _jobs[job_id]["confirmed_siblings"] = req.confirmed_siblings  # None or list
    _jobs[job_id]["anchor_listing"] = req.anchor_listing
    if entity_name and req.entity_type in ("practice", "service_line") or (entity_name and req.confirmed_siblings is not None):
        from perception.graph import upsert_org as _g_upsert, get_org as _g_get
        _etype = "service_line" if req.service_line else (req.entity_type or "practice")
        if req.confirmed_siblings is not None:
            _graph_write(_g_upsert, entity_name, city, state, _etype, specialty=specialty, anchor=req.anchor_listing,
                         locations=req.confirmed_siblings, physicians=[p for ps in (req.physician_roster or {}).values() for p in ps],
                         source="deep_diagnostic", by=_jobs[job_id].get("email") or role, replace_locations=True)
        else:
            _g = _graph_write(_g_get, entity_name, city, state)
            if _g and _g.get("locations"):
                _jobs[job_id]["confirmed_siblings"] = _g["locations"]
                _jobs[job_id]["anchor_listing"] = _jobs[job_id]["anchor_listing"] or _g.get("anchor")
                _jobs[job_id]["roster_from_graph"] = True
    _jobs[job_id]["practice_facts"] = req.practice_facts
    _jobs[job_id]["spotcheck"] = bool(req.spotcheck)
    _jobs[job_id]["content_urls"] = [
        (u.strip() if u.strip().lower().startswith(("http://", "https://")) else "https://" + u.strip())
        for u in (req.content_urls or []) if (u or "").strip()]

    if req.entity_type == "community_health" and entity_name:
        _jobs[job_id]["fqhc_intake"] = req.fqhc_intake
        _jobs[job_id]["site_roster"] = req.fqhc_site_roster or []
        if req.fqhc_site_roster:
            from perception.graph import upsert_org as _g_upsert
            _graph_write(_g_upsert, entity_name, city, state, "community_health", locations=[{"name": s} for s in req.fqhc_site_roster],
                         source="deep_diagnostic", by=_jobs[job_id].get("email") or role, replace_locations=True)
        _pool.submit(_job_run_fqhc, job_id, entity_name, city, state, req.aggregate)
    elif req.entity_type == "practice" and entity_name:
        _pool.submit(_job_run_practice, job_id, entity_name, city, state, specialty, req.aggregate, radius)
    else:
        _pool.submit(_job_run_single, job_id, city, state, specialty, req.aggregate, radius, req.entity_type)
    return {"job_id": job_id}


@app.post("/api/analyze/batch")
async def start_batch(req: BatchRequest, payload: dict = Depends(get_current_user_payload)):
    role  = payload["role"]
    brand = payload.get("brand", "original")
    job_id = _new_job(role, brand, payload.get("email"))
    _pool.submit(_job_run_batch, job_id, [g.dict() for g in req.groups])
    return {"job_id": job_id}


def _job_run_comparison(job_id: str, req_dict: dict) -> None:
    job = _jobs[job_id]
    _cost.begin(job_id, job.get("kind") or "")
    job["kind"] = "Compare Two"
    job.setdefault("label", f"{req_dict.get('entity_a_name') or 'A'} vs {req_dict.get('entity_b_name') or 'B'}")
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)
    try:
        from perception.db import init_db
        from perception.analyzer import compare_locations
        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        result_a, result_b, comparison, pdf_path = compare_locations(
            entity_a_name=req_dict["entity_a_name"],
            city_a=req_dict["city_a"],
            state_a=req_dict["state_a"],
            entity_b_name=req_dict["entity_b_name"],
            city_b=req_dict["city_b"],
            state_b=req_dict["state_b"],
            specialty_a=req_dict.get("specialty_a"),
            specialty_b=req_dict.get("specialty_b"),
            aggregate_a=req_dict.get("aggregate_a", True),
            aggregate_b=req_dict.get("aggregate_b", True),
            teaser_report=req_dict.get("teaser_report", False),
            output_dir=REPORTS_DIR,
            on_event=emit,
            brand=job.get("brand", "original"),
            entity_type_a=req_dict.get("entity_type_a"),
            entity_type_b=req_dict.get("entity_type_b"),
            practice_profile_a=req_dict.get("practice_profile_a"),
            practice_profile_b=req_dict.get("practice_profile_b"),
            practice_composite_a=req_dict.get("practice_composite_a", False),
            practice_composite_b=req_dict.get("practice_composite_b", False),
            practice_roster_a=req_dict.get("practice_roster_a") or [],
            practice_roster_b=req_dict.get("practice_roster_b") or [],
            service_line_a=req_dict.get("service_line_a"),
            parent_system_a=req_dict.get("parent_system_a"),
            service_line_b=req_dict.get("service_line_b"),
            parent_system_b=req_dict.get("parent_system_b"),
            force_rerun_a=req_dict.get("force_rerun_a", False),
            force_rerun_b=req_dict.get("force_rerun_b", False),
            override_today_lock=req_dict.get("override_today_lock", False),
            confirmed_siblings_a=req_dict.get("confirmed_siblings_a"),
            confirmed_siblings_b=req_dict.get("confirmed_siblings_b"),
            anchor_listing_a=req_dict.get("anchor_listing_a"),
            anchor_listing_b=req_dict.get("anchor_listing_b"),
        )
        # Persist the comparison so History can list/download it and the link survives restarts.
        cid = uuid.uuid4().hex[:12]
        try:
            from perception.db import create_comparison_run
            create_comparison_run(
                cid, getattr(result_a, "run_id", None), getattr(result_b, "run_id", None),
                getattr(result_a, "report_title", None) or result_a.entity_name,
                getattr(result_b, "report_title", None) or result_b.entity_name,
                f"{req_dict['city_a']}, {req_dict['state_a']}", f"{req_dict['city_b']}, {req_dict['state_b']}",
                result_a.specialty or result_b.specialty, str(pdf_path),
                bool(req_dict.get("teaser_report")), job.get("role", ""), job.get("email"))
        except Exception as _pexc:
            emit({"type": "text", "text": f"\n(could not save the comparison to History: {type(_pexc).__name__})"})
        job["status"] = "done"
        job["result"] = {
            "run_id": cid,             # download URL: /api/compare/{id}/pdf (persisted id; job id also accepted)
            "location": f"{result_a.entity_name} vs {result_b.entity_name}",
            "specialty": result_a.specialty,
            "provider_count": 2,
            "pdf_path": pdf_path,
            "comparison": True,
        }
        _notify_run_complete(job, "Compare Two report", f"{result_a.entity_name} vs {result_b.entity_name}", [pdf_path])
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


@app.post("/api/compare")
async def start_comparison(req: CompareRequest, payload: dict = Depends(get_current_user_payload)):
    brand = payload.get("brand", "original")
    role  = payload.get("role", "user")
    job_id = _new_job(role, brand, payload.get("email"))
    req_dict = req.dict()
    # Gate override_today_lock to admin users only
    req_dict["override_today_lock"] = req.override_today_lock and (role == "admin")
    _pool.submit(_job_run_comparison, job_id, req_dict)
    return {"job_id": job_id}


@app.get("/api/compare/{job_id}/pdf")
async def download_comparison_pdf(job_id: str, _: str = Depends(require_auth)):
    """Accepts a persisted comparison id (History) or a live job id (result screen)."""
    from perception.db import init_db, get_comparison_run
    pdf_path = None
    job = _jobs.get(job_id)
    if job and job.get("status") == "done":
        pdf_path = job.get("result", {}).get("pdf_path")
    if not pdf_path:
        init_db()
        rec = get_comparison_run("".join(ch for ch in job_id if ch.isalnum()))
        pdf_path = rec.get("pdf_path") if rec else None
    if not pdf_path:
        raise HTTPException(404, "Comparison report not found")
    pdf = Path(pdf_path)
    if not pdf.exists():
        raise HTTPException(404, "PDF file not found on disk")
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, _: str = Depends(require_auth)):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    job = _jobs[job_id]
    return {"job_id": job_id, "status": job.get("status", "running")}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str, _: str = Depends(require_auth)):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    queue: asyncio.Queue = _jobs[job_id]["queue"]

    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                yield 'data: {"type":"ping"}\n\n'
                continue

            if event is None:
                job = _jobs[job_id]
                if job["status"] == "done":
                    payload: dict = {"type": "done"}
                    if job.get("result"):
                        payload.update(job["result"])
                    elif job.get("results"):
                        payload["results"] = job["results"]
                else:
                    payload = {"type": "error", "message": job.get("error", "Unknown error")}
                yield f"data: {json.dumps(payload)}\n\n"
                break

            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/fqhc-battery/{run_id}")
async def start_fqhc_battery(run_id: str, role: str = Depends(require_auth)):
    """Start the MQCR battery for an existing FQHC run. Returns a job_id for SSE streaming."""
    from perception.db import init_db, get_connection

    init_db()
    with get_connection() as con:
        row = con.execute(
            "SELECT entity_name, location, entity_type FROM analysis_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()

    if not row:
        raise HTTPException(404, "Run not found")
    if row[2] != "community_health":
        raise HTTPException(400, "Battery only available for Community Health Edition runs")

    entity_name: str = row[0] or ""
    location: str = row[1] or ""
    # location is stored as "City, ST" — split on last comma
    parts = [p.strip() for p in location.rsplit(",", 1)]
    city = parts[0] if parts else location
    state = parts[1] if len(parts) > 1 else ""

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "running",
        "role": role,
        "loop": loop,
        "queue": queue,
        "result": None,
        "error": None,
    }
    _pool.submit(_job_run_battery, job_id, run_id, entity_name, city, state)
    return {"job_id": job_id}


_DIR_LIST_CACHE: dict[str, tuple[float, set]] = {}
_DIR_LIST_TTL = 30.0


def _existing_files(paths: list, recent: Optional[set] = None) -> set:
    """Return the subset of `paths` that exist, using ONE directory listing per
    distinct parent folder (cached 30 s) instead of a stat per file. REPORTS_DIR
    is a Cloud Storage FUSE mount in production, where every stat is a network
    round trip — History used to do ~1,000 of them per load. Paths in `recent`
    (files that may have been written since the listing was cached) fall back to a
    direct existence check so a just-finished run shows its download immediately."""
    import os as _os, time as _time
    by_dir: dict[str, set] = {}
    for p in paths:
        if p:
            by_dir.setdefault(str(Path(p).parent), set()).add(Path(p).name)
    present: set = set()
    now = _time.monotonic()
    for d, names in by_dir.items():
        cached = _DIR_LIST_CACHE.get(d)
        if not cached or now - cached[0] > _DIR_LIST_TTL:
            try:
                listing = set(_os.listdir(d))
            except OSError:
                listing = set()
            cached = (now, listing)
            _DIR_LIST_CACHE[d] = cached
        for n in names:
            full = str(Path(d) / n)
            if n in cached[1]:
                present.add(full)
            elif recent and full in recent and Path(full).exists():
                present.add(full)
                cached[1].add(n)
    return present


def _row_ts(r: dict):
    """Best-effort timestamp for a history row (created_at, else generated_at date)."""
    from datetime import datetime as _dt
    for key in ("created_at", "generated_at"):
        v = r.get(key)
        if not v:
            continue
        try:
            return _dt.fromisoformat(str(v).replace("Z", "")).replace(tzinfo=None)
        except Exception:
            continue
    return _dt.min


def _history_row_type(r: dict) -> str:
    """Classify a history row for the Type filter."""
    if r.get("report_type") == "comparison":
        return "comparison"
    if r.get("report_type") == "network" or r.get("entity_type") == "hospital_network":
        return "network"
    if r.get("event_id"):
        return "event"
    if r.get("entity_type") == "community_health":
        return "community_health"
    if r.get("service_line"):
        return "service_line"
    if r.get("entity_type") == "practice" or r.get("specialty"):
        return "practice"
    return "hospital"


@app.get("/api/history")
async def get_history(payload: dict = Depends(get_current_user_payload), days: int = 45,
                      before: Optional[str] = None, q: Optional[str] = None, all: int = 0,
                      type: Optional[str] = None, ran_by: Optional[str] = None, mine: int = 0):
    """History rows, newest first. Default: the last `days` days (45). `before=<iso>`
    with `days` returns the next older slice; `q` searches ALL history (no window);
    `all=1` returns everything. `type` (hospital|network|service_line|practice|
    community_health|comparison|event), `ran_by` (email) and `mine=1` are AND filters that
    apply in every mode. Response: {runs, has_more, since, until, total, filtered_total,
    ran_by_options}."""
    from perception.db import init_db, query_history
    from datetime import datetime as _dt, timedelta as _td
    role = payload.get("role", "")
    init_db()
    everything = query_history(role)
    ran_by_options = sorted({str(r.get("ran_by")) for r in everything if r.get("ran_by")}, key=str.lower)
    rb = (payload.get("email") or "").strip().lower() if mine else (ran_by or "").strip().lower()
    every = [r for r in everything
             if (not type or _history_row_type(r) == type)
             and (not rb or str(r.get("ran_by") or "").strip().lower() == rb)]
    days = max(1, min(int(days or 45), 3650))
    since = until = None
    has_more = False
    if q and q.strip():
        needle = q.strip().lower()
        def _hit(r):
            return any(needle in str(r.get(k) or "").lower()
                       for k in ("location", "entity_name", "ran_by", "specialty", "run_id",
                                 "service_line", "parent_system"))
        rows = [r for r in every if _hit(r)]
    elif all:
        rows = every
    else:
        until = _dt.fromisoformat(before.replace("Z", "")).replace(tzinfo=None) if before else _dt.utcnow()
        since = until - _td(days=days)
        rows = [r for r in every if since <= _row_ts(r) < until] if before else \
               [r for r in every if _row_ts(r) >= since]
        has_more = any(_row_ts(r) < since for r in every)
    # Files that might post-date the cached listing: rows created in the last 10 min.
    cutoff = _dt.utcnow() - _td(minutes=10)
    recent: set = set()
    to_check: list = []
    for r in rows:
        if r.get("report_type") == "network":
            continue   # network PDFs regenerate on demand; presence of a path is enough
        for key in ("pdf_path", "briefing_pdf_path"):
            p = r.get(key)
            if p:
                to_check.append(p)
                ca = r.get("created_at")
                try:
                    if ca and _dt.fromisoformat(str(ca).replace("Z", "")).replace(tzinfo=None) >= cutoff:
                        recent.add(str(Path(p)))
                except Exception:
                    pass
    present = _existing_files(to_check, recent)

    result = []
    for r in rows:
        pdf_path = r.get("pdf_path")
        if r.get("report_type") == "network":
            has_pdf = bool(pdf_path)
        else:
            has_pdf = bool(pdf_path and str(Path(pdf_path)) in present)
        bp = r.get("briefing_pdf_path")
        result.append({
            **r,
            "generated_at": str(r["generated_at"]),
            "created_at": str(r["created_at"]) if r.get("created_at") else None,
            "has_pdf": has_pdf,
            "has_teaser_pdf": bool(r.get("teaser_pdf_path")),
            "has_full_detail_pdf": bool(r.get("full_detail_pdf_path")),
            "has_briefing_pdf": bool(bp and str(Path(bp)) in present),
        })
    return {"runs": result, "has_more": has_more,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "total": len(everything), "filtered_total": len(every),
            "ran_by_options": ran_by_options}


@app.delete("/api/reports/{run_id}")
async def delete_report_run(run_id: str, _: dict = Depends(require_admin)):
    """Admin: delete one run (Deep Diagnostic / market / FQHC or Hospital Network) with its
    dependent rows and files. Cleanup for junk/test runs — History keeps everything else."""
    from perception.db import init_db, delete_analysis_run, delete_network_run, delete_comparison_run
    init_db()
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")
    res = delete_comparison_run(safe) or delete_network_run(safe) or delete_analysis_run(safe)
    if res is None:
        raise HTTPException(404, "Run not found")
    for p in res.get("files") or []:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    _DIR_LIST_CACHE.clear()
    return Response(status_code=204)


@app.get("/api/reports/{run_id}/pdf")
async def download_pdf(run_id: str, role: str = Depends(require_auth)):
    from perception.db import query_history
    run = next((r for r in query_history(role) if r["run_id"] == run_id), None)
    if not run or not run.get("pdf_path"):
        raise HTTPException(404, "Report not found")
    pdf = Path(run["pdf_path"])
    if not pdf.exists():
        raise HTTPException(404, "PDF file not found on disk")
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@app.get("/api/reports/{run_id}/teaser-pdf")
async def download_report_teaser_pdf(run_id: str, role: str = Depends(require_auth)):
    """Download the practice combined report's teaser (blurred content) by run_id."""
    from perception.db import query_history
    run = next((r for r in query_history(role) if r["run_id"] == run_id), None)
    if not run or not run.get("teaser_pdf_path"):
        raise HTTPException(404, "Teaser report not found")
    pdf = Path(run["teaser_pdf_path"])
    if not pdf.exists():
        raise HTTPException(404, "Teaser PDF file not found on disk")
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@app.get("/api/reports/{run_id}/briefing-pdf")
async def download_briefing_pdf(run_id: str, role: str = Depends(require_auth)):
    from perception.db import query_history
    run = next((r for r in query_history(role) if r["run_id"] == run_id), None)
    if not run or not run.get("briefing_pdf_path"):
        raise HTTPException(404, "Briefing PDF not found for this run")
    pdf = Path(run["briefing_pdf_path"])
    if not pdf.exists():
        raise HTTPException(404, "Briefing PDF file not found on disk")
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@app.get("/api/practice/profiles")
async def practice_profiles(_: str = Depends(require_auth)):
    """Return the four practice profile options for the UI profile selector."""
    return {
        "profiles": [
            {"value": "practice_procedural",   "label": "Procedural",   "description": "Elective/destination procedures (ortho, plastics, ophthalmology, fertility, bariatrics, cosmetic dermatology, oral surgery)"},
            {"value": "practice_relationship",  "label": "Relationship", "description": "Primary care, pediatrics, OB/GYN, behavioral health, geriatrics, dental-general"},
            {"value": "practice_referral_fed",  "label": "Referral-Fed", "description": "Reached mainly via PCP referral: oncology, cardiology, nephrology, rheumatology, surgical subspecialties"},
            {"value": "practice_hybrid",        "label": "Hybrid",       "description": "Multi-specialty groups: blend of procedural and relationship profiles"},
        ]
    }


class ClassifyPracticeRequest(BaseModel):
    specialty: Optional[str] = None


@app.post("/api/practice/classify")
async def classify_practice(req: ClassifyPracticeRequest, _: str = Depends(require_auth)):
    """Auto-classify a specialty into a practice profile."""
    from perception.practice_models import classify_practice_profile, PROFILE_DISPLAY
    profile = classify_practice_profile(req.specialty)
    return {
        "profile": profile,
        "label": PROFILE_DISPLAY.get(profile, "Procedural"),
    }


@app.post("/api/search/entity")
async def search_entity(req: EntitySearchRequest, _: str = Depends(require_auth)):
    try:
        from perception.data.places import search_entity_candidates
        city, state = req.city, req.state
        if req.zip_code and not (city and state):
            try:
                city, state = _zip_to_city_state(req.zip_code)
            except Exception:
                pass
        city = _normalize_input(city)
        name = _normalize_input(req.name)
        candidates = search_entity_candidates(name, city, state)
        return {"candidates": candidates, "city": city, "state": state}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Search error: {type(exc).__name__}: {exc}")


@app.get("/api/hrsa-prefill")
async def hrsa_prefill(entity_name: str, city: str = "", state: str = "", _: str = Depends(require_auth)):
    try:
        import asyncio
        from perception.data.hrsa import lookup
        from perception.data.fqhc_web_facts import fetch as web_fetch

        loop = asyncio.get_event_loop()
        hrsa_task = loop.run_in_executor(None, lookup, entity_name, city, state)
        web_task  = loop.run_in_executor(None, web_fetch, entity_name, city, state)
        hrsa_data, web_data = await asyncio.gather(hrsa_task, web_task)

        # Merge: HRSA data is authoritative for what it carries;
        # web_data fills in service_lines, languages, and policy fields.
        merged = {**hrsa_data}
        if web_data.get("service_lines"):
            merged["service_lines"] = web_data["service_lines"]
        if web_data.get("languages_served"):
            merged["languages"] = web_data["languages_served"]
        for key in ("accepts_medicaid", "accepts_medicare", "accepts_uninsured",
                    "enrollment_assistance", "new_patients_accepted"):
            if web_data.get(key) is not None:
                merged[key] = web_data[key]

        return merged
    except Exception as exc:
        raise HTTPException(500, f"HRSA prefill error: {type(exc).__name__}: {exc}")


# ── Network Pulse ─────────────────────────────────────────────────────────────

@app.get("/api/network-discover")
async def network_discover(
    network_name: str,
    hq_location: str = "",
    facility_type: str = "hospital",
    _: str = Depends(require_auth),
):
    """Ask Claude + Gemini to enumerate all facilities owned by a named healthcare network.
    Hospitals a user added by hand for this system earlier are merged in (flagged added_earlier)."""
    try:
        loop = asyncio.get_event_loop()
        from perception.network_analyzer import discover_hospitals_by_name
        data = await loop.run_in_executor(
            None, discover_hospitals_by_name, network_name, hq_location, facility_type
        )
        try:
            from perception.db import list_roster_additions
            extra = list_roster_additions(data.get("network_canonical_name") or network_name) or list_roster_additions(network_name)
            have = {(str(f.get("name", "")).lower(), str(f.get("city", "")).lower()) for f in (data.get("facilities") or [])}
            for a in extra:
                if (a["name"].lower(), (a.get("city") or "").lower()) not in have:
                    data.setdefault("facilities", []).append({"name": a["name"], "city": a.get("city") or "", "state": a.get("state") or "",
                                                              "beds": a.get("beds"), "added_earlier": True, "addition_id": a["id"]})
            data["additions"] = extra
        except Exception as _exc:
            print(f"[roster-additions] merge failed: {_exc}")
        return data
    except Exception as exc:
        raise HTTPException(500, f"Network discover error: {type(exc).__name__}: {exc}")


class FacilityResolveRequest(BaseModel):
    name: str
    city: str = ""
    state: str = ""


@app.post("/api/network/resolve-facility")
async def network_resolve_facility(req: FacilityResolveRequest, _: dict = Depends(get_current_user_payload)):
    """Verify a hospital a user wants to add to a network roster: Google listing (name,
    address, rating) plus a CMS Care Compare match (facility id, type, overall stars)."""
    from perception.data.places import search_entity_candidates
    from perception.data import cms
    name, city, state = req.name.strip(), req.city.strip(), req.state.strip().upper()
    if not name:
        raise HTTPException(400, "Enter the hospital name")

    def _go():
        cands = []
        try:
            cands = search_entity_candidates(name, city, state) or []
        except Exception as exc:
            print(f"[resolve-facility] places failed: {exc}")
        best = cands[0] if cands else None
        cms_rec = None
        try:
            if state:
                hs = cms.list_hospitals(state, cities=[city] if city else None)
                m = cms._best_match(name, city, hs) if hs else None
                if m is None and city:
                    hs = cms.list_hospitals(state)
                    m = cms._best_match(name, city, hs) if hs else None
                if m:
                    cms_rec = {"facility_id": m.facility_id, "name": m.name, "city": m.city, "hospital_type": m.hospital_type,
                               "overall_rating": m.overall_rating, "emergency_services": m.emergency_services}
        except Exception as exc:
            print(f"[resolve-facility] cms failed: {exc}")
        out_city = city or (cms_rec or {}).get("city") or ""
        return {"found": bool(best or cms_rec),
                "name": (best or {}).get("name") or (cms_rec or {}).get("name") or name,
                "address": (best or {}).get("address") or "", "rating": (best or {}).get("rating"),
                "review_count": (best or {}).get("review_count"), "place_id": (best or {}).get("place_id"),
                "city": out_city, "state": state or (best or {}).get("resolved_state") or "",
                "cms": cms_rec, "candidates": cands[:4]}
    return await asyncio.get_running_loop().run_in_executor(None, _go)


@app.post("/api/track/entities/{entity_id}/roster/add")
async def track_roster_add(entity_id: str, req: "RosterAdditionRequest", payload: dict = Depends(get_current_user_payload)):
    """Add a hospital the AI missed to a tracked Hospital Network's fixed roster. The change is
    recorded as a dated trend note ("Roster changed: …") so the step is visible on the chart and
    in the Trend Report, and the addition is remembered for future discoveries of the system."""
    from datetime import date as _date
    from perception.db import init_db, get_tracked_entity, update_tracked_entity, add_annotation, add_roster_addition
    init_db()
    ent = get_tracked_entity(entity_id)
    if not ent:
        raise HTTPException(404, "tracked entity not found")
    etype = ent.get("entity_type") or "hospital"
    if etype not in _ROLLUP_TYPES:
        raise HTTPException(400, "Only Hospital Network, practice, service line and community health entities have a fixed roster")
    roster, _ = _entity_roster(ent)
    roster = list(roster or [])
    name, city, state = req.name.strip(), req.city.strip(), req.state.strip().upper()
    if not name:
        raise HTTPException(400, "Enter the name")
    if any((req.place_id and f.get("place_id") == req.place_id)
           or (str(f.get("original_name") or f.get("name") or "").lower() == name.lower() and str(f.get("city", "")).lower() == city.lower())
           for f in roster):
        raise HTTPException(400, f"{name} is already on the roster")
    item = {"name": name, "city": city, "state": state, "place_id": req.place_id, "added_by_user": True,
            "added_on": _date.today().isoformat(), "added_by": payload.get("email") or payload.get("name") or ""}
    if etype == "hospital_network":
        item["beds"] = req.beds
    else:
        item.update({"original_name": name, "entity_type": "clinic", "address": req.address or "",
                     "rating": req.rating, "review_count": req.review_count})
    roster.append(item)
    update_tracked_entity(entity_id, confirmed_roster=json.dumps(roster))
    who = payload.get("email") or payload.get("name") or ""
    word = _roster_note_word(ent)
    note = f"Roster changed: added {name}{(' (' + city + ')') if city else ''} — now {len(roster)} {word}. Snapshots before this date measured {len(roster) - 1}."
    add_annotation(entity_id, _date.today(), note, who)
    if etype == "hospital_network":
        try:
            add_roster_addition(ent["entity_name"], name, city, state, req.beds, req.place_id, added_by=who)
        except Exception:
            pass
    else:
        from perception.graph import upsert_org as _g_upsert
        _graph_write(_g_upsert, ent["entity_name"], ent.get("city") or "", ent.get("state") or "", etype, locations=[item], source="roster_edit", by=who)
    return {"ok": True, "facilities": len(roster), "note": note, "roster": roster}


@app.post("/api/track/entities/{entity_id}/roster/remove")
async def track_roster_remove(entity_id: str, req: RosterRemovalRequest, payload: dict = Depends(get_current_user_payload)):
    """Remove a facility / location from a tracked entity's fixed roster. Recorded as a dated
    'Roster changed' trend note, like additions, so the step is explained on the chart."""
    from datetime import date as _date
    from perception.db import init_db, get_tracked_entity, update_tracked_entity, add_annotation
    init_db()
    ent = get_tracked_entity(entity_id)
    if not ent:
        raise HTTPException(404, "tracked entity not found")
    etype = ent.get("entity_type") or "hospital"
    if etype not in _ROLLUP_TYPES:
        raise HTTPException(400, "This entity has no fixed roster")
    roster, _ = _entity_roster(ent)
    roster = list(roster or [])
    def _match(f):
        if req.place_id and f.get("place_id"):
            return f.get("place_id") == req.place_id
        return (str(f.get("original_name") or f.get("name") or "").lower() == req.name.strip().lower()
                and str(f.get("city") or "").lower() == req.city.strip().lower())
    idx = next((i for i, f in enumerate(roster) if _match(f)), None)
    if idx is None:
        raise HTTPException(404, "That entry is not on the roster")
    if etype == "hospital_network" and len(roster) <= 1:
        raise HTTPException(400, "A network needs at least one facility on its roster")
    gone = roster.pop(idx)
    update_tracked_entity(entity_id, confirmed_roster=json.dumps(roster))
    who = payload.get("email") or payload.get("name") or ""
    gname = gone.get("original_name") or gone.get("name") or "entry"
    word = _roster_note_word(ent)
    note = f"Roster changed: removed {gname}{(' (' + gone['city'] + ')') if gone.get('city') else ''} — now {len(roster)} {word}. Snapshots before this date measured {len(roster) + 1}."
    add_annotation(entity_id, _date.today(), note, who)
    if etype != "hospital_network":
        from perception.graph import remove_location as _g_remove
        _graph_write(_g_remove, ent["entity_name"], ent.get("city") or "", ent.get("state") or "", gone, by=who)
    return {"ok": True, "facilities": len(roster), "note": note, "roster": roster}


class LocationResolveRequest(BaseModel):
    name: str
    address: str = ""
    city: str = ""
    state: str = ""


@app.post("/api/practice/resolve-location")
async def practice_resolve_location(req: LocationResolveRequest, _: dict = Depends(get_current_user_payload)):
    """Match one line of a customer's location/profile list to its Google listing."""
    from perception.data.places import text_search, search_entity_candidates
    name, addr, city, state = req.name.strip(), req.address.strip(), req.city.strip(), req.state.strip().upper()
    if not name:
        raise HTTPException(400, "Enter the location name")

    def _go():
        cands = []
        try:
            q = " ".join(x for x in [name, addr, city, state] if x)
            cands = text_search(q, max_results=5) or []
            if not cands and city:
                cands = search_entity_candidates(name, city, state) or []
        except Exception as exc:
            print(f"[resolve-location] failed: {exc}")
        best = cands[0] if cands else None
        if not best:
            return {"found": False, "name": name, "address": addr, "city": city, "state": state}
        return {"found": True, "name": best.get("name") or name, "address": best.get("formatted_address") or best.get("address") or addr,
                "city": best.get("city") or city, "state": best.get("state") or state, "place_id": best.get("place_id"),
                "rating": best.get("rating"), "review_count": best.get("review_count"), "maps_url": best.get("maps_url"),
                "business_status": best.get("business_status")}
    return await asyncio.get_running_loop().run_in_executor(None, _go)


class RosterAdditionRequest(BaseModel):
    network_name: str = ""
    name: str
    city: str = ""
    state: str = ""
    beds: Optional[int] = None
    place_id: Optional[str] = None
    address: str = ""                       # practice / community health locations
    rating: Optional[float] = None
    review_count: Optional[int] = None


class RosterRemovalRequest(BaseModel):
    name: str = ""
    city: str = ""
    place_id: Optional[str] = None


_ROLLUP_TYPES = ("hospital_network", "practice", "service_line", "community_health")


def _roster_note_word(ent: dict) -> str:
    return "facilities" if (ent.get("entity_type") or "hospital") == "hospital_network" else "locations"


@app.post("/api/network/additions")
async def network_addition_add(req: RosterAdditionRequest, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, add_roster_addition
    init_db()
    return add_roster_addition(req.network_name, req.name.strip(), req.city.strip(), req.state.strip(), req.beds, req.place_id,
                               added_by=payload.get("email") or payload.get("name") or "")


@app.delete("/api/network/additions/{aid}")
async def network_addition_delete(aid: str, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, delete_roster_addition
    init_db()
    delete_roster_addition(aid)
    return {"ok": True}


@app.get("/api/network-prefill")
async def network_prefill(url: str, _: str = Depends(require_auth)):
    """Extract hospital roster from a network locations page URL (supplementary)."""
    try:
        loop = asyncio.get_event_loop()
        from perception.network_analyzer import extract_roster_from_url
        data = await loop.run_in_executor(None, extract_roster_from_url, url)
        return data
    except Exception as exc:
        raise HTTPException(500, f"Network prefill error: {type(exc).__name__}: {exc}")


class NetworkAnalyzeRequest(BaseModel):
    network_name: str
    hq_location: str = ""
    source_url: str = ""
    facilities: list[dict]
    facility_type: str = "hospital"
    brand: str = "original"
    ignore_cache: bool = False   # admin only: bypass same-day cache and regenerate
    teaser: bool = False
    service_line_audit: bool = False   # internal: add the service-line scorecard section
    full_detail: bool = False    # also generate the Hospital Network Full Detail report


@app.post("/api/network/analyze")
async def network_analyze(req: NetworkAnalyzeRequest, payload: dict = Depends(get_current_user_payload)):
    """Start a Network Pulse analysis job. Returns job_id for SSE streaming."""
    role  = payload["role"]
    brand = payload.get("brand", req.brand)
    ignore_cache = req.ignore_cache and (role == "admin")
    job_id = _new_job(role, brand, payload.get("email"))
    _pool.submit(_job_network_analyze, job_id, req.network_name, req.hq_location,
                 req.source_url, req.facilities, req.facility_type, brand, ignore_cache,
                 req.teaser, req.service_line_audit, req.full_detail)
    return {"job_id": job_id}


def _job_network_analyze(job_id: str, network_name: str, hq_location: str,
                          source_url: str, facilities: list[dict],
                          facility_type: str = "hospital",
                          brand: str = "original",
                          ignore_cache: bool = False,
                          teaser: bool = False,
                          service_line_audit: bool = False,
                          full_detail: bool = False) -> None:
    job = _jobs[job_id]
    _cost.begin(job_id, job.get("kind") or "")
    job["kind"] = "Hospital Network"
    job.setdefault("label", network_name)
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)
    try:
        from perception.db import init_db
        from perception.network_analyzer import analyze_network
        init_db()
        result = analyze_network(
            network_name=network_name,
            hq_location=hq_location,
            source_url=source_url,
            facilities=facilities,
            facility_type=facility_type,
            brand=brand,
            on_event=emit,
            ignore_cache=ignore_cache,
            teaser=teaser,
            content_summary=True,   # standard report includes the content summary + CTA
            service_line_audit=service_line_audit,   # internal opt-in: scorecard section
            full_detail=full_detail,   # opt-in: Hospital Network Full Detail report
        )
        if _job_ran_by(job):
            from perception.db import get_connection as _gc
            with _gc() as _con:
                _con.execute("UPDATE network_runs SET ran_by = ? WHERE run_id = ?",
                             [_job_ran_by(job), result.run_id])
        job["status"] = "done"
        _notify_run_complete(job, "Hospital Network report", result.network_canonical_name or result.network_name,
                             [result.pdf_path, result.teaser_pdf_path, getattr(result, "full_detail_pdf_path", None)])
        job["result"] = {
            "run_id": result.run_id,
            "entity_type": "hospital_network",
            "network_name": result.network_name,
            "network_canonical_name": result.network_canonical_name,
            "ai_visibility_score": result.ai_visibility_score,
            "grade": result.grade,
            "grade_band": result.grade_band,
            "total_hospitals": result.total_hospitals,
            "states_covered": result.states_covered,
            "pdf_path": result.pdf_path,
            "teaser_pdf_path": result.teaser_pdf_path,
            "full_detail_pdf_path": result.full_detail_pdf_path,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


@app.get("/api/network/{run_id}/pdf")
async def network_pdf(run_id: str, _: str = Depends(require_auth)):
    """Download a Network Pulse PDF by run_id.

    If the PDF file no longer exists on disk (e.g. after a Cloud Run container
    restart), it is regenerated from the stored result_json before being served.
    """
    import re as _re
    from datetime import datetime as _dt
    from perception.db import get_connection
    with get_connection() as con:
        row = con.execute(
            "SELECT pdf_path, network_name, result_json FROM network_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
    if not row:
        raise HTTPException(404, "Hospital Network run not found")

    pdf_path = Path(row[0]) if row[0] else None

    if not pdf_path or not pdf_path.exists():
        # PDF missing from disk — regenerate from stored result_json
        result_json = row[2]
        if not result_json:
            raise HTTPException(404, "Hospital Network PDF missing and no stored result to regenerate from")
        try:
            from perception.models import NetworkResult
            from perception.network_pdf import render_network_pdf
            result = NetworkResult.model_validate_json(result_json)
            output_dir = Path("reports")
            output_dir.mkdir(parents=True, exist_ok=True)
            slug = _re.sub(r"[^a-z0-9]+", "-", (result.network_name or "network").lower()).strip("-")
            _ts = _dt.utcnow().strftime("%y%m%d-%H%M")
            pdf_filename = f"{slug}-network-pulse-{_ts}.pdf"
            pdf_path = output_dir / pdf_filename
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, render_network_pdf, result, str(pdf_path))
            # Persist regenerated path so next download skips regeneration
            with get_connection() as con:
                con.execute(
                    "UPDATE network_runs SET pdf_path = ? WHERE run_id = ?",
                    [str(pdf_path), run_id],
                )
        except Exception as exc:
            raise HTTPException(500, f"PDF regeneration failed: {type(exc).__name__}: {exc}")

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'},
    )


@app.get("/api/network/{run_id}/teaser-pdf")
async def network_teaser_pdf(run_id: str, _: str = Depends(require_auth)):
    """Download the teaser Network Pulse PDF by run_id."""
    from perception.db import get_connection
    with get_connection() as con:
        row = con.execute(
            "SELECT teaser_pdf_path FROM network_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "Teaser PDF not found for this run")
    pdf_path = Path(row[0])
    if not pdf_path.exists():
        raise HTTPException(404, "Teaser PDF file missing from disk")
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'},
    )


@app.get("/api/network/{run_id}/full-detail-pdf")
async def network_full_detail_pdf(run_id: str, _: str = Depends(require_auth)):
    """Download the Hospital Network Full Detail PDF by run_id."""
    from perception.db import get_connection
    with get_connection() as con:
        row = con.execute(
            "SELECT full_detail_pdf_path FROM network_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "Full Detail PDF not found for this run")
    pdf_path = Path(row[0])
    if not pdf_path.exists():
        raise HTTPException(404, "Full Detail PDF file missing from disk")
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'},
    )


# ── Hospital Network — bulk (headless) scoring from an uploaded list ──────────

_Q_ORDINAL = {"Q1": "1st Quartile", "Q2": "2nd Quartile",
              "Q3": "3rd Quartile", "Q4": "4th Quartile"}
_NETWORK_BULK_DIR = REPORTS_DIR / "network_bulk"


def _detect_network_cols(header: list):
    """Find the (name, city, state) column indices in an arbitrary CSV header."""
    low = [(h or "").strip().lower() for h in header]
    def _find(preds):
        for i, h in enumerate(low):
            if any(p in h for p in preds):
                return i
        return None
    name_i  = _find(["entity_name", "system_name", "hospital_name", "organization", "name"])
    city_i  = _find(["primary_city", "city"])
    state_i = _find(["primary_state", "state"])
    return name_i, city_i, state_i


# Four-pillar columns appended to the bulk CSV — key + header, in report order.
# The values come free from the same evaluation that produces the Pulse Score
# (returned as tier_scores; also stored in the canonical cache), on a 0-100 scale.
_NETWORK_PILLARS = [
    ("clinical_outcomes_safety",   "Outcomes & Safety"),
    ("credentials_recognition",    "Credentials & Recognition"),
    ("patient_experience_reviews", "Experience & Reviews"),
    ("access_fit",                 "Access & Fit"),
]


def _pillar_cells(tiers: dict) -> list:
    """Four pillar scores as CSV cells (blank if a pillar is missing)."""
    out = []
    for key, _label in _NETWORK_PILLARS:
        v = (tiers or {}).get(key)
        out.append(str(int(v)) if isinstance(v, (int, float)) else "")
    return out


def _score_network_row(name: str, city: str, state: str, brand: str = "original",
                       attempts: int = 3):
    """Headless four-pillar score for one system →
    (score:int, quartile_label:str, tier_scores:dict).
    Reuses a fresh canonical score if one exists; otherwise scores headless (no
    PDF, no History) and seeds the canonical cache. Retries transient failures."""
    from perception.db import get_entity_score
    from perception.scoring import grade_from_score
    loc = ", ".join([p for p in [(city or "").strip(), (state or "").strip()] if p])
    _last = None
    for _a in range(attempts):
        try:
            canon = get_entity_score(name, loc, days=30)
            if canon and canon.get("pulse_score") is not None:
                score = canon["pulse_score"]
                tiers = canon.get("tier_scores") or {}
            else:
                from perception.network_analyzer import _entity_pulse_score
                score, tiers, _ai, _p = _entity_pulse_score(name, loc, brand=brand, headless=True)
            if score is None:
                raise ValueError("no score produced")
            q_code, _band = grade_from_score(score)
            return int(score), _Q_ORDINAL.get(q_code, q_code), (tiers or {})
        except Exception as exc:
            _last = exc
            if _a < attempts - 1:
                time.sleep(1.5 * (_a + 1))
    raise _last or ValueError("no score produced")


def _run_network_bulk_job(job_id: str, bulk_id: str, input_path: str, brand: str) -> None:
    """Score every row of the stored input CSV headlessly. Progress is persisted
    to the DB row each time an entity finishes, so a dropped connection or a
    recycled instance never loses the count — and Resume re-runs the same input
    (already-scored systems return instantly from the canonical cache)."""
    job = _jobs[job_id]
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)
    try:
        import csv as _csv
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
        from perception.db import init_db, bump_network_bulk_progress, finalize_network_bulk_run
        init_db()
        _NETWORK_BULK_DIR.mkdir(parents=True, exist_ok=True)
        with open(input_path, newline="", encoding="utf-8-sig") as fh:
            reader = list(_csv.reader(fh))
        header = reader[0]
        rows = [r for r in reader[1:] if any((c or "").strip() for c in r)]
        name_i, city_i, state_i = _detect_network_cols(header)
        total = len(rows)
        emit({"type": "phase", "name": "starting", "text": f"Scoring {total} systems"})
        out: dict = {}

        _fail_cols = ["Failure to run", "", "", "", "", ""]   # score, quartile, 4 pillars

        def _work(idx: int):
            row = rows[idx]
            name = (row[name_i] if name_i is not None and name_i < len(row) else "").strip()
            city = row[city_i] if city_i is not None and city_i < len(row) else ""
            state = row[state_i] if state_i is not None and state_i < len(row) else ""
            if not name:
                return idx, list(_fail_cols)
            emit({"type": "text", "text": f"▶ {name}"})
            try:
                score, quartile, tiers = _score_network_row(name, city, state, brand)
                emit({"type": "text", "text": f"✓ {name} — {score} ({quartile})"})
                return idx, [str(score), quartile] + _pillar_cells(tiers)
            except Exception as exc:
                emit({"type": "text", "text": f"✗ {name} — failed ({type(exc).__name__})"})
                return idx, list(_fail_cols)

        done = 0
        with _TPE(max_workers=5) as ex:
            for f in _ac([ex.submit(_work, i) for i in range(total)]):
                idx, cols = f.result()
                out[idx] = cols
                done += 1
                bump_network_bulk_progress(bulk_id, 1)     # durable, reconnectable progress
                emit({"type": "phase", "name": "scoring", "text": f"Scored {done} of {total}"})

        out_path = _NETWORK_BULK_DIR / f"{bulk_id}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(list(header) + ["Pulse_Score", "Quartile"]
                       + [lbl for _k, lbl in _NETWORK_PILLARS])
            for i, row in enumerate(rows):
                w.writerow(list(row) + out.get(i, list(_fail_cols)))

        failed = sum(1 for c in out.values() if c[0] == "Failure to run")
        finalize_network_bulk_run(bulk_id, total - failed, failed, str(out_path))
        job["status"] = "done"
        job["result"] = {"bulk_id": bulk_id, "total": total,
                         "scored": total - failed, "failed": failed, "bulk": True}
    except Exception as exc:
        try:
            from perception.db import fail_network_bulk_run
            fail_network_bulk_run(bulk_id)
        except Exception:
            pass
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


@app.post("/api/network/bulk/run")
async def network_bulk_run(file: UploadFile = File(...),
                           payload: dict = Depends(require_admin)):
    """Admin only. Headless bulk scoring of an uploaded list of hospital systems. The
    upload is saved so the run can be resumed. Returns a job_id (SSE) + bulk_id (CSV)."""
    import csv as _csv, io as _io
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = list(_csv.reader(_io.StringIO(text)))
    if not reader:
        raise HTTPException(400, "The uploaded file is empty.")
    header = reader[0]
    rows = [r for r in reader[1:] if any((c or "").strip() for c in r)]
    name_i, _ci, _si = _detect_network_cols(header)
    if name_i is None:
        raise HTTPException(400, "Could not find a system/entity name column "
                                 "(expected a header containing 'name').")
    if not rows:
        raise HTTPException(400, "No data rows found in the uploaded file.")
    from perception.db import init_db, create_network_bulk_run
    init_db()
    _NETWORK_BULK_DIR.mkdir(parents=True, exist_ok=True)
    bulk_id = uuid.uuid4().hex[:12]
    input_path = _NETWORK_BULK_DIR / f"{bulk_id}_input.csv"
    input_path.write_bytes(raw)
    create_network_bulk_run(bulk_id, (file.filename or "list.csv"),
                            len(rows), payload.get("role", ""), str(input_path))
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"), payload.get("email"))
    _pool.submit(_run_network_bulk_job, job_id, bulk_id, str(input_path),
                 payload.get("brand", "original"))
    return {"job_id": job_id, "bulk_id": bulk_id, "total": len(rows)}


@app.post("/api/network/bulk/{bulk_id}/resume")
async def network_bulk_resume(bulk_id: str,
                              payload: dict = Depends(require_admin)):
    """Admin only. Re-run a bulk list from its saved upload. Already-scored systems return
    instantly from the canonical cache, so only the remainder is re-computed."""
    from perception.db import init_db, get_network_bulk_run, reset_network_bulk_run
    init_db()
    rec = get_network_bulk_run(bulk_id)
    if not rec:
        raise HTTPException(404, "Run not found")
    input_path = rec.get("input_path")
    if not input_path or not Path(input_path).exists():
        raise HTTPException(400, "The original upload is no longer available to resume.")
    reset_network_bulk_run(bulk_id)
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"), payload.get("email"))
    _pool.submit(_run_network_bulk_job, job_id, bulk_id, input_path,
                 payload.get("brand", "original"))
    return {"job_id": job_id, "bulk_id": bulk_id, "total": rec.get("total", 0)}


@app.get("/api/network/bulk/runs")
async def network_bulk_runs_list(_: str = Depends(require_auth)):
    """List all National Entity (bulk network) runs for the History page."""
    from perception.db import init_db, list_network_bulk_runs
    init_db()
    return list_network_bulk_runs()


@app.delete("/api/network/bulk/{bulk_id}")
async def network_bulk_delete(bulk_id: str, _: dict = Depends(require_admin)):
    from perception.db import init_db, delete_network_bulk_run
    init_db()
    delete_network_bulk_run(bulk_id)
    return {"ok": True}


@app.post("/api/admin/service-line-cache/clear")
async def clear_service_line_cache_endpoint(_: dict = Depends(require_admin)):
    """Admin: clear the whole service-line audit cache (handy while iterating)."""
    from perception.db import init_db, clear_service_line_cache
    init_db()
    return {"cleared": clear_service_line_cache()}


@app.get("/api/network/bulk/{bulk_id}/csv")
async def network_bulk_csv(bulk_id: str, _: str = Depends(require_auth)):
    safe = "".join(ch for ch in bulk_id if ch.isalnum())
    path = _NETWORK_BULK_DIR / f"{safe}.csv"
    if not path.exists():
        raise HTTPException(404, "Enriched CSV not found (the run may still be in progress).")
    return FileResponse(str(path), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="hospital-network-scores.csv"'})


@app.post("/api/upload")
async def upload_csv(file: UploadFile = File(...), _: str = Depends(require_auth)):
    from perception.loader import load
    suffix = Path(file.filename or "upload.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        entities = load(tmp_path)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        os.unlink(tmp_path)

    seen: dict[tuple, None] = {}
    for e in entities:
        seen[((e.city or "").strip().title(), (e.state or "").strip().upper(), e.specialty)] = None

    return {
        "entity_count": len(entities),
        "groups": [{"city": c, "state": s, "specialty": sp} for c, s, sp in seen],
    }


# ── SSO — models ─────────────────────────────────────────────────────────────

class RequestAccessBody(BaseModel):
    email: str
    name: Optional[str] = None
    request_type: str = "google"  # "google" or "native"


class NativeLoginRequest(BaseModel):
    email: str
    password: str


class SetPasswordRequest(BaseModel):
    token: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class UpdateRoleRequest(BaseModel):
    role: str


class UpdateBrandRequest(BaseModel):
    brand: str


class InviteUserRequest(BaseModel):
    email: str
    name: Optional[str] = None
    auth_type: str = "native"
    role: str = "user"
    brand: str = "original"


# ══ Student Health Clinics — Competitors Rankings for on-campus clinics ═══════
_STUDENT_HEALTH_DIR = REPORTS_DIR / "student_health"

# Student pillar slot → CSV/label, in display order.
_STUDENT_PILLARS = [
    ("credentials_recognition",    "Findability & Identity"),
    ("clinical_outcomes_safety",   "Services & Access"),
    ("patient_experience_reviews", "Reviews & Reputation"),
    ("access_fit",                 "Machine-Readability & Digital Presence"),
]


class StudentRosterRequest(BaseModel):
    mode: str                                # "state" | "radius" | "conference"
    state: Optional[str] = None
    conference: Optional[str] = None
    anchor_school: Optional[str] = None
    radius_miles: Optional[int] = None


class StudentRunRequest(BaseModel):
    group_label: str = ""
    mode: str = ""
    subject: str = ""            # state / conference / "anchor NNNmi" — drives the filename
    override_cache: bool = False
    schools: list[dict]


@app.post("/api/student-health/resolve")
async def student_health_resolve(req: StudentRosterRequest,
                                 _: dict = Depends(get_current_user_payload)):
    """Resolve the roster of universities → student health clinics for confirmation."""
    from perception.student_health import resolve_roster
    loop = asyncio.get_event_loop()
    roster = await loop.run_in_executor(None, lambda: resolve_roster(
        req.mode, state=req.state, conference=req.conference,
        anchor_school=req.anchor_school, radius_miles=req.radius_miles))
    return roster


@app.post("/api/student-health/run")
async def student_health_run(req: StudentRunRequest,
                             payload: dict = Depends(get_current_user_payload)):
    """Score + rank a confirmed roster of student health clinics (background job)."""
    schools = [s for s in (req.schools or []) if (s.get("clinic_name") or s.get("school"))]
    if not schools:
        raise HTTPException(400, "No clinics to score.")
    from perception.db import init_db, create_student_health_run
    init_db()
    run_id = uuid.uuid4().hex[:12]
    label = req.group_label or "Student Health Clinics"
    import re as _re
    from datetime import date as _date
    subj = _re.sub(r"[^A-Za-z0-9]+", "", (req.subject or "")) or "StudentHealth"
    _t = _date.today()
    title = f"{subj}_StudentHealthClinics_{_t.day}.{_t.month}.{_t.year}"
    override = bool(req.override_cache) and payload.get("role") == "admin"
    create_student_health_run(run_id, label, req.mode or "", len(schools),
                              payload.get("role", ""), title)
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"), payload.get("email"))
    _pool.submit(_run_student_health_job, job_id, run_id, label, req.mode or "", schools, override)
    return {"job_id": job_id, "run_id": run_id, "total": len(schools)}


def _run_student_health_job(job_id: str, run_id: str, group_label: str,
                            mode: str, schools: list, override: bool = False) -> None:
    job = _jobs[job_id]
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)
    try:
        import csv as _csv, json as _json
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
        from perception.db import init_db, finalize_student_health_run
        from perception.student_health import score_clinic
        init_db()
        _STUDENT_HEALTH_DIR.mkdir(parents=True, exist_ok=True)
        total = len(schools)
        emit({"type": "phase", "name": "starting", "text": f"Scoring {total} student health clinics"})
        results: dict = {}

        def _work(i: int):
            c = schools[i]
            nm = c.get("clinic_name") or c.get("school") or f"row {i}"
            emit({"type": "text", "text": f"▶ {nm}"})
            try:
                r = score_clinic(c, override=override)
                emit({"type": "text", "text": f"✓ {nm} — {r.get('pulse_score')}"})
                return i, r
            except Exception as exc:
                emit({"type": "text", "text": f"✗ {nm} — failed ({type(exc).__name__})"})
                return i, {"pulse_score": None, "tiers": {}, "quartile": "—",
                           "band_label": "", "ai_says": ""}

        done = 0
        with _TPE(max_workers=5) as ex:
            for f in _ac([ex.submit(_work, i) for i in range(total)]):
                i, r = f.result()
                results[i] = r
                done += 1
                emit({"type": "phase", "name": "scoring", "text": f"Scored {done} of {total}"})

        rows = [{**schools[i], **results.get(i, {})} for i in range(total)]
        rows.sort(key=lambda x: (x.get("pulse_score") is None, -(x.get("pulse_score") or 0)))
        for idx, row in enumerate(rows, 1):
            row["rank"] = idx

        csv_path = _STUDENT_HEALTH_DIR / f"{run_id}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["Rank", "School", "Clinic", "City", "State", "URL",
                        "Pulse_Score", "Quartile"] + [lbl for _k, lbl in _STUDENT_PILLARS]
                       + ["Google Reviews"])
            for row in rows:
                t = row.get("tiers") or {}
                score = row.get("pulse_score")
                gr = row.get("google_rating")
                gr_txt = (f"{gr}★ ({row.get('google_review_count') or 0})"
                          if gr is not None else "")
                w.writerow([row["rank"], row.get("school", ""), row.get("clinic_name", ""),
                            row.get("city", ""), row.get("state", ""), row.get("url", ""),
                            score if score is not None else "Failed",
                            _Q_ORDINAL.get(row.get("quartile"), row.get("quartile") or "")]
                           + [(t.get(k) if t.get(k) is not None else "") for k, _lbl in _STUDENT_PILLARS]
                           + [gr_txt])

        scored = sum(1 for row in rows if row.get("pulse_score") is not None)
        result = {"group_label": group_label, "mode": mode, "rows": [
            {"rank": r["rank"], "school": r.get("school"), "clinic_name": r.get("clinic_name"),
             "city": r.get("city"), "state": r.get("state"), "url": r.get("url"),
             "pulse_score": r.get("pulse_score"), "quartile": r.get("quartile"),
             "band_label": r.get("band_label"), "tiers": r.get("tiers"),
             "ai_says": r.get("ai_says"), "reviews_source": r.get("reviews_source"),
             "google_rating": r.get("google_rating"),
             "google_review_count": r.get("google_review_count")} for r in rows]}
        # Branded ranked PDF (best-effort — CSV/results still deliver if it fails).
        pdf_path = ""
        try:
            emit({"type": "phase", "name": "pdf", "text": "Rendering report"})
            from perception.student_health_pdf import render_student_health_pdf
            _pp = _STUDENT_HEALTH_DIR / f"{run_id}.pdf"
            render_student_health_pdf(result, str(_pp))
            pdf_path = str(_pp)
        except Exception as _pe:
            emit({"type": "text", "text": f"(PDF render skipped: {type(_pe).__name__})"})
        finalize_student_health_run(run_id, scored, str(csv_path), pdf_path, _json.dumps(result))
        job["status"] = "done"
        job["result"] = {"run_id": run_id, "student_health": True,
                         "group_label": group_label, "total": total, "scored": scored}
    except Exception as exc:
        try:
            from perception.db import fail_student_health_run
            fail_student_health_run(run_id)
        except Exception:
            pass
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


@app.get("/api/student-health/runs")
async def student_health_runs_list(_: str = Depends(require_auth)):
    from perception.db import init_db, list_student_health_runs
    init_db()
    rows = list_student_health_runs()
    for r in rows:
        if r.get("created_at") is not None:
            r["created_at"] = str(r["created_at"])
    return rows


@app.get("/api/student-health/{run_id}/csv")
async def student_health_csv(run_id: str, _: str = Depends(require_auth)):
    from perception.db import init_db, get_student_health_run
    init_db()
    rec = get_student_health_run(run_id)
    if not rec or not rec.get("csv_path") or not Path(rec["csv_path"]).exists():
        raise HTTPException(404, "CSV not found")
    fname = (rec.get("title") or Path(rec["csv_path"]).stem) + ".csv"
    return FileResponse(rec["csv_path"], media_type="text/csv", filename=fname)


@app.get("/api/student-health/{run_id}/pdf")
async def student_health_pdf(run_id: str, _: str = Depends(require_auth)):
    from perception.db import init_db, get_student_health_run
    init_db()
    rec = get_student_health_run(run_id)
    if not rec or not rec.get("pdf_path") or not Path(rec["pdf_path"]).exists():
        raise HTTPException(404, "PDF not found")
    fname = (rec.get("title") or Path(rec["pdf_path"]).stem) + ".pdf"
    return FileResponse(rec["pdf_path"], media_type="application/pdf", filename=fname)


@app.get("/api/student-health/{run_id}")
async def student_health_get(run_id: str, _: str = Depends(require_auth)):
    import json as _json
    from perception.db import init_db, get_student_health_run
    init_db()
    rec = get_student_health_run(run_id)
    if not rec:
        raise HTTPException(404, "Run not found")
    result = None
    if rec.get("result_json"):
        try:
            result = _json.loads(rec["result_json"])
        except Exception:
            result = None
    rec.pop("result_json", None)
    rec["created_at"] = str(rec.get("created_at"))
    rec["result"] = result
    return rec


# ══ Content Analysis sandbox — verified content-visibility, two reports ═══════

class ContentAnalysisRequest(BaseModel):
    entity_name: str
    city: str = ""
    state: str = ""
    entity_type: str = "hospital"            # "hospital" | "practice"
    specialty: Optional[str] = None
    practice_profile: Optional[str] = None
    report_title: Optional[str] = None
    urls: list[str] = []                     # confirmed/added website URLs
    override_cache: bool = False             # admin only: re-run the base diagnostic fresh


@app.post("/api/content-analysis/run")
async def content_analysis_run(req: ContentAnalysisRequest,
                               payload: dict = Depends(get_current_user_payload)):
    """Run the contained Content Analysis: reuse-or-run the base Deep Diagnostic,
    then the verified Content Analyzer, producing two downloadable reports."""
    if not req.entity_name.strip():
        raise HTTPException(400, "entity_name is required")
    from perception.db import init_db, create_content_analysis_run
    init_db()
    ca_id = uuid.uuid4().hex[:12]
    loc = ", ".join([p for p in [req.city.strip(), req.state.strip()] if p])
    create_content_analysis_run(ca_id, req.entity_name.strip(), loc,
                                req.entity_type, req.urls,
                                req.report_title or req.entity_name.strip(),
                                payload.get("role", ""))
    req_d = req.dict()
    # Gate the cache override to admins (like every other report type).
    req_d["override_cache"] = bool(req.override_cache) and payload.get("role") == "admin"
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"), payload.get("email"))
    _pool.submit(_job_content_analysis, job_id, ca_id, req_d,
                 payload.get("brand", "original"))
    return {"job_id": job_id, "ca_id": ca_id}


def _job_content_analysis(job_id: str, ca_id: str, req: dict, brand: str) -> None:
    job = _jobs[job_id]
    _cost.begin(job_id, job.get("kind") or "")
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)
    try:
        from perception.db import (init_db, set_run_role, save_content_findings,
                                   finalize_content_analysis_run, _norm_entity_name)
        from perception.content_analyzer import analyze_content
        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        entity_name = (req.get("entity_name") or "").strip()
        city, state = req.get("city", ""), req.get("state", "")
        entity_type = req.get("entity_type") or "hospital"
        override = bool(req.get("override_cache"))

        # 1. Base Deep Diagnostic — reuse-if-fresh, else run (analyzer caches
        #    internally). Admin override forces a fully fresh base run.
        emit({"type": "phase", "name": "diagnostic",
              "text": "Refreshing the Deep Diagnostic" if override else "Running the Deep Diagnostic"})
        if entity_type == "practice":
            from perception.practice_analyzer import analyze_practice
            result = analyze_practice(
                entity_name=entity_name, city=city, state=state,
                specialty=req.get("specialty"), aggregate=True,
                practice_profile=req.get("practice_profile"),
                output_dir=REPORTS_DIR, on_event=emit, brand=brand,
                report_title=req.get("report_title"),
                force_rerun=override, override_today_lock=override,
            )
        else:
            from perception.analyzer import analyze_location
            # aggregate=True so the base run consolidates the system's locations,
            # giving the reputation layer per-location Google data (ratings,
            # volume, fragmented/unclaimed listings) — not just the front door.
            result = analyze_location(
                city=city, state=state, specialty=req.get("specialty"),
                entity_name=entity_name, individual_report=True, aggregate=True,
                entity_type="hospital", output_dir=REPORTS_DIR, on_event=emit,
                brand=brand, report_title=req.get("report_title"),
                force_rerun=override, override_today_lock=override,
            )
        set_run_role(result.run_id, job["role"], _job_ran_by(job))

        # 2. Website URLs: user-confirmed, else the resolved provider's site.
        urls = [u for u in (req.get("urls") or []) if (u or "").strip()]
        if not urls and result.rankings and result.rankings[0].website_url:
            urls = [result.rankings[0].website_url]

        # 3. Verified content analysis. Reputation (location basis) reuses the
        #    base diagnostic's verified Google data — no new crawling.
        rep = None
        prov = result.rankings[0] if result.rankings else None
        if prov is not None:
            fp = prov.google_footprint
            agg = fp.system_aggregate if fp else None
            fd = fp.front_door if fp else None
            # Single-entity fallback: the front-door Google read is populated even
            # for a non-aggregate individual report; the system aggregate only
            # populates for multi-location/aggregate runs.
            agg_rating = (agg.rating if (agg and agg.rating is not None) else None)
            agg_count = (agg.total_reviews if (agg and agg.rating is not None) else None)
            if agg_rating is None and fd and getattr(fd, "verified", False) and fd.rating is not None:
                agg_rating, agg_count = fd.rating, fd.count
            rep = {
                "locations": [
                    {"name": l.name, "google_rating": l.google_rating,
                     "google_review_count": l.google_review_count, "address": l.address}
                    for l in (prov.consolidated_locations or [])
                ],
                "footprint": {"rating_range": (fp.rating_range if fp else ""),
                              "consistency": (fp.consistency if fp else "")},
                "aggregate_rating": agg_rating,
                "aggregate_count": agg_count,
            }
        saf = None
        if entity_type == "hospital" and prov is not None:
            saf = {"entity_kind": "hospital", "name": entity_name,
                   "leapfrog_grade": getattr(prov, "leapfrog_grade", None),
                   "cms_star_rating": getattr(prov, "cms_star_rating", None)}
        emit({"type": "phase", "name": "content", "text": "Checking website, Wikidata, Wikipedia, reputation, and safety"})
        findings = analyze_content(entity_name, urls, city, state,
                                   entity_kind=entity_type, reputation=rep, safety=saf, on_event=emit)
        findings.run_id = result.run_id
        save_content_findings(
            result.run_id, _norm_entity_name(entity_name),
            findings.source_snapshot,
            [f.model_dump() for f in findings.findings],
            findings.status,
        )
        for f in findings.findings:
            emit({"type": "text", "text": f"\n• [{f.severity}] {f.teaser_summary}"})

        # 4. Report 1 = Deep Diagnostic + Content Improvement Keys section.
        #    (Report 2, the detailed content report, arrives in step 5.)
        emit({"type": "phase", "name": "pdf", "text": "Building the report"})
        report1 = ""
        try:
            from perception.pdf import render_content_deep_dive
            _r1 = REPORTS_DIR / f"content_{ca_id}_report1.pdf"
            render_content_deep_dive(result, _r1, findings, brand=brand)
            report1 = str(_r1)
        except Exception as _pe:
            emit({"type": "text", "text": f"\n(augmented report render failed: {type(_pe).__name__}) — using base report"})
            report1 = result.pdf_path or ""

        # 5. Report 2 = the detailed content report (the CIP format).
        report2 = ""
        try:
            from perception.content_report_pdf import render_content_report_pdf
            _r2 = REPORTS_DIR / f"content_{ca_id}_report2.pdf"
            loc = ", ".join([p for p in [city, state] if p])
            render_content_report_pdf(entity_name, loc, findings, str(_r2),
                                      report_title=req.get("report_title") or entity_name)
            report2 = str(_r2)
        except Exception as _pe2:
            emit({"type": "text", "text": f"\n(content report render failed: {type(_pe2).__name__})"})

        finalize_content_analysis_run(ca_id, result.run_id, findings.status,
                                      len(findings.findings), report1, report2)
        emit({"type": "phase", "name": "saving", "text": "Done"})
        job["status"] = "done"
        job["result"] = {"ca_id": ca_id, "content_analysis": True,
                         "run_id": result.run_id, "finding_count": len(findings.findings),
                         "findings_status": findings.status}
    except Exception as exc:
        try:
            from perception.db import fail_content_analysis_run
            fail_content_analysis_run(ca_id)
        except Exception:
            pass
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


@app.get("/api/content-analysis/runs")
async def content_analysis_runs_list(_: str = Depends(require_auth)):
    from perception.db import init_db, list_content_analysis_runs
    init_db()
    rows = list_content_analysis_runs()
    for r in rows:
        if r.get("created_at") is not None:
            r["created_at"] = str(r["created_at"])
    return rows


@app.get("/api/content-analysis/{ca_id}")
async def content_analysis_get(ca_id: str, _: str = Depends(require_auth)):
    from perception.db import init_db, get_content_analysis_run, get_content_findings
    init_db()
    rec = get_content_analysis_run(ca_id)
    if not rec:
        raise HTTPException(404, "Run not found")
    rec["created_at"] = str(rec.get("created_at"))
    rec["findings"] = None
    if rec.get("base_run_id"):
        cf = get_content_findings(rec["base_run_id"])
        if cf:
            rec["findings"] = cf.get("findings")
            rec["source_snapshot"] = cf.get("source_snapshot")
    return rec


@app.get("/api/content-analysis/{ca_id}/report{n}")
async def content_analysis_report(ca_id: str, n: int, _: str = Depends(require_auth)):
    from perception.db import init_db, get_content_analysis_run
    init_db()
    rec = get_content_analysis_run(ca_id)
    if not rec:
        raise HTTPException(404, "Run not found")
    path = rec.get("report1_path") if n == 1 else rec.get("report2_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "Report not available")
    import re as _re
    stem = _re.sub(r"[^A-Za-z0-9]+", "-", (rec.get("report_title") or "content")).strip("-")
    label = "Deep-Diagnostic" if n == 1 else "Content-Report"
    return FileResponse(path, media_type="application/pdf",
                        filename=f"{stem}_{label}.pdf")


def _rerender_content_reports(ca_id: str) -> dict:
    """Rebuild a content-analysis run's PDF(s) from cached findings + service-line
    scorecards — no crawl/network/audit/LLM. Runs sync (Playwright); call in a
    thread. Returns {"report1": bool, "report2": bool}."""
    import json as _json
    from perception.db import (get_content_analysis_run, get_content_findings,
                               get_recent_service_line_analysis, get_recent_network_run,
                               _norm_entity_name)
    from perception.models import (ContentFinding, ContentFindings, ServiceLineSummary,
                                   ServiceLineScorecardSet, NetworkResult)
    rec = get_content_analysis_run(ca_id)
    if not rec:
        raise ValueError("Run not found")
    cf = get_content_findings(rec.get("base_run_id"))
    if not cf:
        raise ValueError("No cached findings for this run")
    findings = ContentFindings(
        run_id=rec.get("base_run_id") or "", source_snapshot=cf.get("source_snapshot") or {},
        status=cf.get("status", "verified"),
        findings=[ContentFinding(**f) for f in (cf.get("findings") or [])])
    name = rec.get("entity_name") or ""
    loc = rec.get("location") or ""
    title = rec.get("report_title") or name
    out = {"report1": False, "report2": False}

    sl_payload = None
    _cached = get_recent_service_line_analysis(_norm_entity_name(name), days=3650)
    if _cached:
        _d = _json.loads(_cached)
        sl_payload = (ServiceLineSummary(**_d["summary"]),
                      ServiceLineScorecardSet(**_d["cards"]).scorecards)

    if rec.get("report2_path"):
        from perception.content_report_pdf import render_content_report_pdf
        render_content_report_pdf(name, loc, findings, rec["report2_path"],
                                  report_title=title, service_line=sl_payload)
        out["report2"] = True
    if rec.get("report1_path") and rec.get("entity_type") == "network":
        nr = get_recent_network_run(name, days=3650)
        if nr and nr.get("result_json"):
            from perception.network_pdf import render_content_network
            render_content_network(NetworkResult.model_validate_json(nr["result_json"]),
                                   rec["report1_path"], findings)
            out["report1"] = True
    return out


@app.post("/api/content-analysis/{ca_id}/rerender")
async def content_analysis_rerender(ca_id: str, _: str = Depends(require_auth)):
    """Rebuild this run's report PDF(s) from cached data (no re-analysis)."""
    from perception.db import init_db
    init_db()
    try:
        out = await asyncio.get_event_loop().run_in_executor(_pool, _rerender_content_reports, ca_id)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return out


@app.post("/api/content-analysis/{ca_id}/draft")
async def content_analysis_draft(ca_id: str, payload: dict = Depends(get_current_user_payload)):
    """Phase-3 remediation: draft publication-ready content for each finding and
    regenerate Report 2 with it. Facts-only, [VERIFY:]-placeholder guardrail."""
    from perception.db import init_db, get_content_analysis_run, get_content_findings
    init_db()
    rec = get_content_analysis_run(ca_id)
    if not rec:
        raise HTTPException(404, "Run not found")
    if rec.get("status") != "done" or not rec.get("base_run_id"):
        raise HTTPException(400, "Run isn't complete yet.")
    if not get_content_findings(rec["base_run_id"]):
        raise HTTPException(400, "No findings to draft.")
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"), payload.get("email"))
    _pool.submit(_job_content_draft, job_id, ca_id)
    return {"job_id": job_id, "ca_id": ca_id}


def _job_content_draft(job_id: str, ca_id: str) -> None:
    job = _jobs[job_id]
    _cost.begin(job_id, job.get("kind") or "")
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)
    try:
        from perception.db import (init_db, get_content_analysis_run, get_content_findings,
                                   save_content_findings, set_content_analysis_drafted)
        from perception.content_drafting import draft_findings
        from perception.content_report_pdf import render_content_report_pdf
        from perception.models import ContentFindings, ContentFinding
        init_db()
        rec = get_content_analysis_run(ca_id)
        cf = get_content_findings(rec["base_run_id"])
        findings = cf.get("findings") or []
        snap = cf.get("source_snapshot") or {}

        emit({"type": "phase", "name": "drafting", "text": "Drafting publication-ready content"})
        facts = {"website_urls": snap.get("website_urls"), "specialty": None,
                 "wikidata_qid": snap.get("wikidata_qid"),
                 "wikipedia_article": snap.get("wikipedia_article")}
        drafts = draft_findings(rec["entity_name"], rec.get("location", ""),
                                rec.get("entity_type", "hospital"), facts, findings)
        draftable_ct = sum(1 for f in findings
                           if (f.get("remediation_type") or "") and f.get("status") != "not_assessed")
        for f in findings:
            if f.get("finding_id") in drafts:
                f["draft_content"] = drafts[f["finding_id"]]
                emit({"type": "text", "text": f"✓ drafted {f['finding_id']} ({f.get('platform')})"})

        # If every draftable finding failed to produce content, the run failed
        # (e.g. the drafting call errored or truncated). Don't mark the run
        # "drafted" or regenerate the report — leave the button available to
        # retry, and surface the failure instead of silently reporting success.
        if draftable_ct and not drafts:
            emit({"type": "text", "text": "⚠ Drafting produced no content — the model call failed or was truncated. Nothing was saved; please try again."})
            job["status"] = "error"
            job["error"] = "Drafting produced no content — please retry."
            return
        if draftable_ct and len(drafts) < draftable_ct:
            missing = [f.get("finding_id") for f in findings
                       if (f.get("remediation_type") or "") and f.get("status") != "not_assessed"
                       and f.get("finding_id") not in drafts]
            emit({"type": "text", "text": f"⚠ Partial: {len(drafts)}/{draftable_ct} drafted. Missing: {', '.join(missing)}"})

        # Persist drafts back into the cache (deterministic re-render).
        save_content_findings(rec["base_run_id"], cf.get("norm_entity", ""),
                              snap, findings, cf.get("status", "verified"))

        # Regenerate Report 2 with drafts.
        emit({"type": "phase", "name": "pdf", "text": "Rebuilding the Content Improvement Plan"})
        model = ContentFindings(
            run_id=rec["base_run_id"], source_snapshot=snap,
            status=cf.get("status", "verified"),
            findings=[ContentFinding(**f) for f in findings],
        )
        # Preserve the Service-Line Listing Management section on redraft, if the
        # system has a cached service-line analysis.
        _sl_payload = None
        try:
            import json as _json
            from perception.db import (get_recent_service_line_analysis, _norm_entity_name)
            from perception.models import ServiceLineScorecardSet, ServiceLineSummary
            _cached = get_recent_service_line_analysis(_norm_entity_name(rec["entity_name"]), days=30)
            if _cached:
                _d = _json.loads(_cached)
                _sl_payload = (ServiceLineSummary(**_d["summary"]),
                               ServiceLineScorecardSet(**_d["cards"]).scorecards)
        except Exception:
            _sl_payload = None
        _r2 = REPORTS_DIR / f"content_{ca_id}_report2.pdf"
        render_content_report_pdf(rec["entity_name"], rec.get("location", ""), model,
                                  str(_r2), report_title=rec.get("report_title") or rec["entity_name"],
                                  service_line=_sl_payload)
        set_content_analysis_drafted(ca_id, str(_r2))
        job["status"] = "done"
        job["result"] = {"ca_id": ca_id, "content_analysis": True, "drafted": True,
                         "drafted_count": len(drafts)}
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


# ── Content Analysis for a Hospital Network (all facilities) ──────────────────

class ContentNetworkRequest(BaseModel):
    network_name: str
    hq_location: str = ""
    urls: list[str] = []                     # confirmed system website URL(s)
    report_title: Optional[str] = None
    override_cache: bool = False             # admin only
    service_line_audit: bool = False         # opt-in: deep service-line listing audit


# ══ Public HubSpot webhook — Hospital Network report on request ═══════════════
import hmac as _hmac

_HUBSPOT_SECRET_KEY      = "hubspot_webhook_secret"
_HUBSPOT_SIG_HEADER      = "X-Pulse-Signature"
_PUBLIC_REPORT_DAILY_CAP = 5             # per requester email per calendar day
_PUBLIC_LINK_TTL_DAYS    = 14

_FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com",
    "hotmail.com", "live.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "gmx.com", "mail.com", "zoho.com",
    "yandex.com", "msn.com", "comcast.net", "verizon.net", "att.net",
}
_GENERIC_ORG_TOKENS = {
    "health", "healthcare", "hospital", "hospitals", "medical", "center",
    "centers", "system", "systems", "care", "clinic", "clinics", "group",
    "regional", "memorial", "community", "university", "the", "and", "for",
    "physicians", "associates", "partners", "network", "services", "inc",
    "llc", "corporation", "saint", "childrens", "children", "county", "valley",
    "medicine", "institute", "foundation",
}
_MULTI_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "co.nz", "co.in"}


def _registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 (e.g. 'mail.dukehealth.org' → 'dukehealth.org')."""
    host = (host or "").strip().lower()
    if "//" in host:
        host = host.split("//", 1)[1]
    host = host.split("/", 1)[0].split("?", 1)[0].split("@")[-1].split(":")[0]
    labels = [l for l in host.split(".") if l]
    if len(labels) < 2:
        return host
    last2 = ".".join(labels[-2:])
    if last2 in _MULTI_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last2


def _brand_tokens_ordered(text: str) -> list:
    """Distinctive tokens of a name (order preserved), minus generic healthcare words."""
    import re as _re
    toks = _re.split(r"[^a-z0-9]+", (text or "").lower())
    return [t for t in toks if len(t) >= 3 and t not in _GENERIC_ORG_TOKENS]


def _domain_core(reg_domain: str) -> str:
    """The brand portion of a registrable domain (TLD stripped, dots removed):
    'dukehealth.org' → 'dukehealth', 'bswhealth.org' → 'bswhealth'."""
    reg = (reg_domain or "").lower()
    for mt in _MULTI_TLDS:
        if reg.endswith("." + mt):
            return reg[: -(len(mt) + 1)].replace(".", "")
    return reg.rsplit(".", 1)[0].replace(".", "") if "." in reg else reg


def _email_domain(email: str) -> str:
    return _registrable_domain((email or "").split("@")[-1])


def _affiliated(reg_domain: str, ordered_tokens: list) -> bool:
    """Generous match between a domain and an organization's distinctive name
    tokens: exact, substring (either direction), or initialism (e.g. 'bswhealth'
    ← Baylor Scott White). Anchored on the org name so it works even when the
    email domain differs from the public website (duke.edu ↔ Duke Health)."""
    core = _domain_core(reg_domain)
    tokens = set(ordered_tokens)
    if not core or not tokens:
        return False
    if core in tokens:                                   # exact label (bjc, nyu, duke)
        return True
    for t in tokens:                                     # brand word inside the domain
        if (len(t) >= 3 and t in core) or (len(core) >= 3 and core in t):
            return True
    acronym = "".join(t[0] for t in ordered_tokens)      # initialism (bsw, hca, chi)
    if len(acronym) >= 2 and acronym in core:
        return True
    return False


def _email_matches_org(email: str, org_name: str) -> tuple[bool, str]:
    """Generous affiliation check: does the requester's email plausibly belong to
    the organization the report is about? Anchored on the ORG NAME (the report
    subject), NOT the self-submitted URL, so a made-up matching URL/email pair
    cannot unlock a report for an unrelated hospital. Returns (allowed, reason)."""
    ed = _email_domain(email)
    if "@" not in (email or "") or not ed:
        return False, "invalid email address"
    if ed in _FREE_EMAIL_DOMAINS:
        return False, f"{ed} is a personal email provider — affiliation can't be confirmed"
    if _affiliated(ed, _brand_tokens_ordered(org_name)):
        return True, f"email domain '{ed}' matches the organization"
    return False, f"email domain '{ed}' is not clearly affiliated with '{org_name}'"


def _hubspot_secret() -> str:
    """Env-pinned secret wins (read-only); otherwise a DB-stored, rotatable secret
    generated on first use."""
    import os, secrets as _secrets
    env = os.environ.get("HUBSPOT_WEBHOOK_SECRET", "").strip()
    if env:
        return env
    from perception.db import init_db, get_setting, set_setting
    init_db()
    val = get_setting(_HUBSPOT_SECRET_KEY)
    if not val:
        val = _secrets.token_urlsafe(32)
        set_setting(_HUBSPOT_SECRET_KEY, val)
    return val


def _hubspot_secret_env_pinned() -> bool:
    import os
    return bool(os.environ.get("HUBSPOT_WEBHOOK_SECRET", "").strip())


class HubspotNetworkRequest(BaseModel):
    organization_name: str
    headquarters: str = ""
    website_url: str = ""
    requester_name: str = ""
    requester_email: str
    requester_title: str = ""


@app.post("/api/public/hubspot/network-request")
async def public_hubspot_network_request(req: HubspotNetworkRequest, request: Request):
    """Public webhook (HubSpot Workflow → outbound POST), authenticated by a
    shared-secret header. Persists the request, returns 200 immediately, and
    generates + emails the Hospital Network report in the background."""
    # Secret accepted in the header (preferred) or a ?key= query param (for tools
    # like HubSpot's native webhook action that can't set custom headers).
    supplied = (request.headers.get(_HUBSPOT_SIG_HEADER, "")
                or request.headers.get(_HUBSPOT_SIG_HEADER.lower(), "")
                or request.query_params.get("key", ""))
    if not supplied or not _hmac.compare_digest(supplied, _hubspot_secret()):
        raise HTTPException(401, "Invalid or missing signature")
    org   = (req.organization_name or "").strip()
    email = (req.requester_email or "").strip().lower()
    if not org or not email or "@" not in email:
        raise HTTPException(400, "organization_name and a valid requester_email are required")

    from perception.db import (init_db, create_public_report_request,
                               count_public_requests_today)
    init_db()
    req_id = uuid.uuid4().hex
    create_public_report_request(
        req_id, org, (req.headquarters or "").strip(), (req.website_url or "").strip(),
        (req.requester_name or "").strip(), email, (req.requester_title or "").strip(),
    )
    over_cap = count_public_requests_today(email) > _PUBLIC_REPORT_DAILY_CAP
    _pool.submit(_run_public_report_job, req_id, over_cap)
    return {"status": "received", "request_id": req_id}


def _run_public_report_job(req_id: str, over_cap: bool = False) -> None:
    """Background: verify affiliation → generate the full Hospital Network report
    headlessly (auto-discovered roster, no round-trip) → email a secure link. On a
    failed affiliation check, route to a human follow-up instead."""
    import os, secrets as _secrets
    from perception.db import (init_db, get_public_report_request,
                               update_public_report_request)
    from perception import email_utils
    init_db()
    rec = get_public_report_request(req_id)
    if not rec:
        return
    org, email, name = rec["organization_name"], rec["requester_email"], rec["requester_name"]
    try:
        update_public_report_request(req_id, status="verifying")
        allowed, reason = _email_matches_org(email, org)
        if over_cap:
            allowed, reason = False, "daily request cap reached"
        if not allowed:
            update_public_report_request(req_id, status="follow_up", match_reason=reason)
            try:    email_utils.send_public_report_followup(email, name, org)
            except Exception as _e: print(f"[public] followup email failed: {_e}")
            try:    email_utils.notify_admin_public_request(org, email, "follow-up", reason)
            except Exception: pass
            return

        update_public_report_request(req_id, status="generating", match_reason=reason)
        from perception.network_analyzer import analyze_network, discover_hospitals_by_name
        disc = discover_hospitals_by_name(org, rec["headquarters"] or "")
        result = analyze_network(
            network_name=org,
            hq_location=rec["headquarters"] or "",
            source_url=rec["website_url"] or "",
            facilities=disc.get("facilities", []),
            facility_type="hospital",
            brand="original",
            content_summary=True,   # public report includes the content summary + CTA
        )
        token = _secrets.token_urlsafe(24)
        update_public_report_request(req_id, status="sent", run_id=result.run_id,
                                     download_token=token)
        app_url = os.environ.get("APP_URL", "https://careclimb.com").rstrip("/")
        email_utils.send_public_report_ready(email, name, org,
                                             f"{app_url}/api/public/report/{token}")
        try:    email_utils.notify_admin_public_request(org, email, "sent")
        except Exception: pass
    except Exception as exc:
        update_public_report_request(req_id, status="failed", error_msg=str(exc)[:500])
        try:    email_utils.notify_admin_public_request(org, email, "failed", str(exc)[:200])
        except Exception: pass


@app.get("/api/public/report/{token}")
async def public_report_download(token: str):
    """Serve a requested Hospital Network PDF via its secure, expiring link."""
    import re as _re
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from perception.db import (init_db, get_public_report_request_by_token, get_connection)
    init_db()
    rec = get_public_report_request_by_token(token)
    if not rec or rec.get("status") != "sent" or not rec.get("run_id"):
        raise HTTPException(404, "This link is invalid or has expired.")
    created = rec.get("created_at")
    if isinstance(created, str):
        try: created = _dt.fromisoformat(created.replace("Z", "+00:00"))
        except Exception: created = None
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=_tz.utc)
        if _dt.now(_tz.utc) - created > _td(days=_PUBLIC_LINK_TTL_DAYS):
            raise HTTPException(410, "This link has expired.")

    with get_connection() as con:
        row = con.execute(
            "SELECT pdf_path, result_json FROM network_runs WHERE run_id = ?",
            [rec["run_id"]],
        ).fetchone()
    if not row:
        raise HTTPException(404, "Report not found.")
    pdf_path = Path(row[0]) if row[0] else None
    if not pdf_path or not pdf_path.exists():
        if not row[1]:
            raise HTTPException(404, "Report file is no longer available.")
        from perception.models import NetworkResult, ContentFindings
        from perception.network_pdf import render_network_pdf
        result = NetworkResult.model_validate_json(row[1])
        _findings = None
        if result.content_findings_json:
            try:
                _findings = ContentFindings.model_validate_json(result.content_findings_json)
            except Exception:
                _findings = None
        out_dir = Path("reports"); out_dir.mkdir(parents=True, exist_ok=True)
        slug = _re.sub(r"[^a-z0-9]+", "-", (result.network_name or "network").lower()).strip("-")
        pdf_path = out_dir / f"{slug}-network-pulse-{_dt.utcnow().strftime('%y%m%d-%H%M')}.pdf"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: render_network_pdf(result, str(pdf_path), findings=_findings))
        with get_connection() as con:
            con.execute("UPDATE network_runs SET pdf_path = ? WHERE run_id = ?",
                        [str(pdf_path), rec["run_id"]])
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'})


# ── Admin: Integrations (webhook secret + activity) ───────────────────────────

@app.get("/api/admin/integrations/webhook")
async def admin_integration_webhook(request: Request,
                                    _: dict = Depends(require_integration_admin)):
    """Webhook config for the Admin → Integrations tab: URL, secret, payload contract."""
    base = os.environ.get("APP_URL", str(request.base_url).rstrip("/")).rstrip("/")
    return {
        "webhook_url": f"{base}/api/public/hubspot/network-request",
        "signature_header": _HUBSPOT_SIG_HEADER,
        "secret": _hubspot_secret(),
        "secret_env_pinned": _hubspot_secret_env_pinned(),
        "daily_cap": _PUBLIC_REPORT_DAILY_CAP,
        "link_ttl_days": _PUBLIC_LINK_TTL_DAYS,
        "sample_payload": {
            "organization_name": "Duke Health",
            "headquarters": "Durham, NC",
            "website_url": "https://www.dukehealth.org",
            "requester_name": "Jane Smith",
            "requester_email": "jane.smith@duke.edu",
            "requester_title": "VP Marketing",
        },
    }


@app.post("/api/admin/integrations/webhook/rotate")
async def admin_integration_rotate(_: dict = Depends(require_integration_admin)):
    """Generate a new webhook secret (disabled when the secret is env-pinned)."""
    import secrets as _secrets
    if _hubspot_secret_env_pinned():
        raise HTTPException(400, "Secret is pinned by the HUBSPOT_WEBHOOK_SECRET env var "
                                 "and can't be rotated from here.")
    from perception.db import init_db, set_setting
    init_db()
    new = _secrets.token_urlsafe(32)
    set_setting(_HUBSPOT_SECRET_KEY, new)
    return {"secret": new}


@app.get("/api/admin/integrations/requests")
async def admin_integration_requests(_: dict = Depends(require_integration_admin)):
    """Recent public report requests for the Integrations tab activity log."""
    from perception.db import init_db, list_public_report_requests
    init_db()
    rows = list_public_report_requests(limit=100)
    for r in rows:
        for k in ("created_at", "updated_at"):
            if r.get(k) is not None:
                r[k] = str(r[k])
    return rows


# ── Google OAuth ──────────────────────────────────────────────────────────────

@app.get("/auth/google")
async def google_auth():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth not configured (GOOGLE_CLIENT_ID missing)")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def google_callback(
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    base = APP_URL
    if error or not code:
        return RedirectResponse(f"{base}/?auth_error=cancelled")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": _GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            token_data = token_resp.json()
            print(f"[oauth] token exchange status={token_resp.status_code} keys={list(token_data.keys())}")
            access_token = token_data.get("access_token")
            if not access_token:
                print(f"[oauth] token_failed: {token_data.get('error')} — {token_data.get('error_description')}")
                return RedirectResponse(f"{base}/?auth_error=token_failed")

            info_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo = info_resp.json()
            print(f"[oauth] userinfo email={userinfo.get('email')} name={userinfo.get('name')}")
    except Exception as _exc:
        print(f"[oauth] network error: {type(_exc).__name__}: {_exc}")
        return RedirectResponse(f"{base}/?auth_error=network")

    email = (userinfo.get("email") or "").lower()
    name = userinfo.get("name") or userinfo.get("given_name") or email
    if not email:
        return RedirectResponse(f"{base}/?auth_error=no_email")

    from perception.db import init_db
    from perception.auth import (
        create_user, create_access_request, get_access_request_by_email,
        get_user_by_email, update_last_login,
    )
    from perception.email_utils import notify_admin_access_request

    init_db()
    user = get_user_by_email(email)
    print(f"[oauth] lookup email={email} user_found={bool(user)}")
    if user:
        if not user["is_active"]:
            return RedirectResponse(f"{base}/?auth_error=deactivated")
        update_last_login(user["id"])
        _brand = user.get("brand") or "original"
        tok = _create_token(user["role"], uid=user["id"], email=email,
                            name=user.get("name") or name, brand=_brand)
        return RedirectResponse(f"{base}/?google_token={tok}")

    req = get_access_request_by_email(email)
    print(f"[oauth] access_request={req['status'] if req else 'none'}")
    if req and req["status"] == "approved":
        new_user = create_user(email, name, "user", "google")
        update_last_login(new_user["id"])
        tok = _create_token(new_user["role"], uid=new_user["id"], email=email, name=name, brand="original")
        return RedirectResponse(f"{base}/?google_token={tok}")
    elif req and req["status"] == "pending":
        return RedirectResponse(
            f"{base}/?auth_status=pending&auth_email={urllib.parse.quote(email)}"
        )
    elif req and req["status"] == "denied":
        return RedirectResponse(f"{base}/?auth_error=denied")

    new_req = create_access_request(email, name, "google")
    try:
        notify_admin_access_request(email, name, "google", new_req["id"])
    except Exception:
        pass
    return RedirectResponse(
        f"{base}/?auth_status=requested&auth_email={urllib.parse.quote(email)}"
    )


# ── Native (email+password) login ─────────────────────────────────────────────

@app.post("/api/auth/native/login")
async def native_login(req: NativeLoginRequest):
    from perception.db import init_db
    from perception.auth import get_user_by_email, update_last_login, verify_password
    init_db()
    user = get_user_by_email(req.email.lower())
    if not user or user.get("auth_type") != "native" or not user.get("is_active"):
        raise HTTPException(401, "Invalid email or password")
    if not verify_password(user, req.password):
        raise HTTPException(401, "Invalid email or password")
    update_last_login(user["id"])
    _brand = user.get("brand") or "original"
    tok = _create_token(user["role"], uid=user["id"], email=user["email"],
                        name=user.get("name") or user["email"], brand=_brand)
    return {"token": tok, "role": user["role"],
            "display_name": user.get("name") or user["email"],
            "brand": _brand}


# ── Access request submission ─────────────────────────────────────────────────

# Internal domains whose users get a native (email + password) account. Everyone
# else is routed to Google Sign-In. Derived server-side so the track can't be
# spoofed by a crafted request_type in the client payload.
_NATIVE_LOGIN_DOMAINS = ("rldatix.com", "socialclimb.com")


def _is_native_domain(email: str) -> bool:
    e = (email or "").lower().strip()
    return any(e.endswith("@" + d) for d in _NATIVE_LOGIN_DOMAINS)


@app.post("/api/auth/request")
async def request_access(req: RequestAccessBody):
    from perception.db import init_db
    from perception.auth import (
        create_access_request, get_access_request_by_email, get_user_by_email,
    )
    from perception.email_utils import notify_admin_access_request
    init_db()
    email = req.email.lower().strip()
    if get_user_by_email(email):
        raise HTTPException(400, "An account with this email already exists")
    existing = get_access_request_by_email(email)
    if existing and existing["status"] == "pending":
        return {"status": "pending", "message": "Your request is already being reviewed"}
    # Authoritative: the login track is decided by the email domain, not the client.
    request_type = "native" if _is_native_domain(email) else "google"
    new_req = create_access_request(email, req.name, request_type)
    try:
        notify_admin_access_request(email, req.name, request_type, new_req["id"])
    except Exception as _e:
        print(f"[email] request notify error: {_e}")
    return {"status": "requested"}


# ── Set password from emailed link ────────────────────────────────────────────

@app.post("/api/auth/set-password")
async def set_password_endpoint(req: SetPasswordRequest):
    from perception.db import init_db
    from perception.auth import consume_password_token, get_user_by_id, set_password
    init_db()
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    user_id = consume_password_token(req.token)
    if not user_id:
        raise HTTPException(400, "This link is invalid or has already been used")
    set_password(user_id, req.password)
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(500, "User not found")
    _brand = user.get("brand") or "original"
    tok = _create_token(user["role"], uid=user["id"], email=user["email"],
                        name=user.get("name") or user["email"], brand=_brand)
    return {"token": tok, "role": user["role"],
            "display_name": user.get("name") or user["email"],
            "brand": _brand}


# ── Forgot password ───────────────────────────────────────────────────────────

@app.post("/api/auth/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    from perception.db import init_db
    from perception.auth import create_password_token, get_user_by_email
    from perception.email_utils import send_reset_password_link
    init_db()
    email = req.email.lower().strip()
    user = get_user_by_email(email)
    if user and user.get("auth_type") == "native" and user.get("is_active"):
        tok = create_password_token(user["id"])
        try:
            send_reset_password_link(email, user.get("name") or email, tok)
        except Exception as _e:
            print(f"[email] forgot-password error: {_e}")
    # Always return success to avoid email enumeration
    return {"status": "sent"}


# ── Admin endpoints ───────────────────────────────────────────────────────────

def _fmt_user(u: dict) -> dict:
    for k in ("created_at", "last_login"):
        if u.get(k) is not None:
            u[k] = str(u[k])
    return u


def _fmt_req(r: dict) -> dict:
    for k in ("requested_at", "handled_at"):
        if r.get(k) is not None:
            r[k] = str(r[k])
    return r


@app.get("/api/admin/users")
async def admin_list_users(_: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import list_users
    init_db()
    return [_fmt_user(u) for u in list_users()]


@app.get("/api/admin/requests")
async def admin_list_requests(_: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import list_access_requests
    init_db()
    return [_fmt_req(r) for r in list_access_requests()]


@app.post("/api/admin/requests/{req_id}/approve")
async def admin_approve_request(req_id: str, payload: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import (
        create_password_token, create_user, get_access_request, get_user_by_email,
        handle_access_request,
    )
    from perception.email_utils import send_google_access_approved, send_set_password_link
    init_db()
    req = get_access_request(req_id)
    if not req:
        raise HTTPException(404, "Request not found")
    by = payload.get("email") or payload.get("uid") or payload.get("role", "admin")
    handle_access_request(req_id, "approved", by)
    email_error = None
    if req["request_type"] == "native":
        if not get_user_by_email(req["email"]):
            user = create_user(req["email"], req["name"], "user", "native")
            tok = create_password_token(user["id"])
            try:
                send_set_password_link(req["email"], req["name"], tok)
            except Exception as _e:
                print(f"[email] approve native error: {_e}")
                email_error = str(_e)
    else:
        try:
            send_google_access_approved(req["email"], req["name"])
        except Exception as _e:
            print(f"[email] approve google error: {_e}")
            email_error = str(_e)
    return {"status": "approved", "email_error": email_error}


@app.post("/api/admin/requests/{req_id}/deny")
async def admin_deny_request(req_id: str, payload: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import get_access_request, handle_access_request
    from perception.email_utils import send_access_denied
    init_db()
    req = get_access_request(req_id)
    if not req:
        raise HTTPException(404, "Request not found")
    by = payload.get("email") or payload.get("uid") or payload.get("role", "admin")
    handle_access_request(req_id, "denied", by)
    try:
        send_access_denied(req["email"], req["name"])
    except Exception as _e:
        print(f"[email] deny error: {_e}")
    return {"status": "denied"}


@app.post("/api/admin/requests/{req_id}/resend")
async def admin_resend_request(req_id: str, _: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import (
        create_password_token, get_access_request, get_user_by_email,
    )
    from perception.email_utils import (
        notify_admin_access_request, send_access_denied,
        send_google_access_approved, send_set_password_link,
    )
    init_db()
    req = get_access_request(req_id)
    if not req:
        raise HTTPException(404, "Request not found")
    status = req["status"]
    rtype  = req["request_type"]
    email  = req["email"]
    name   = req["name"]
    try:
        if status == "approved" and rtype == "native":
            user = get_user_by_email(email)
            if not user:
                raise HTTPException(400, "User account not found — approve the request first")
            tok = create_password_token(user["id"])
            send_set_password_link(email, name, tok)
        elif status == "approved" and rtype == "google":
            send_google_access_approved(email, name)
        elif status == "denied":
            send_access_denied(email, name)
        elif status == "pending":
            notify_admin_access_request(email, name, rtype, req_id)
        else:
            raise HTTPException(400, f"Cannot resend for status '{status}'")
    except HTTPException:
        raise
    except Exception as _e:
        print(f"[email] resend error: {_e}")
        raise HTTPException(500, f"Email send failed: {_e}")
    return {"status": "resent"}


@app.put("/api/admin/users/{user_id}/role")
async def admin_update_role(
    user_id: str, req: UpdateRoleRequest, _: dict = Depends(require_admin)
):
    from perception.db import init_db
    from perception.auth import update_user_role
    init_db()
    update_user_role(user_id, req.role)
    return {"status": "updated"}


@app.put("/api/admin/users/{user_id}/brand")
async def admin_update_brand(
    user_id: str, req: UpdateBrandRequest, _: dict = Depends(require_admin)
):
    if req.brand not in ("original", "extension1", "extension2"):
        raise HTTPException(400, "brand must be original, extension1, or extension2")
    from perception.db import init_db
    from perception.auth import update_user_brand
    init_db()
    update_user_brand(user_id, req.brand)
    return {"status": "updated"}


@app.post("/api/admin/users/{user_id}/deactivate")
async def admin_deactivate(user_id: str, _: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import deactivate_user
    init_db()
    deactivate_user(user_id)
    return {"status": "deactivated"}


@app.post("/api/admin/users/{user_id}/reactivate")
async def admin_reactivate(user_id: str, _: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import reactivate_user
    init_db()
    reactivate_user(user_id)
    return {"status": "reactivated"}


@app.post("/api/admin/users/invite")
async def admin_invite_user(req: InviteUserRequest, payload: dict = Depends(require_admin)):
    from perception.db import init_db
    from perception.auth import create_password_token, create_user, get_user_by_email
    from perception.email_utils import send_google_access_approved, send_set_password_link
    init_db()
    email = req.email.lower().strip()
    if get_user_by_email(email):
        raise HTTPException(400, "A user with this email already exists")
    by = payload.get("email") or payload.get("uid") or payload.get("role", "admin")
    user = create_user(email, req.name, req.role, req.auth_type, invited_by=by, brand=req.brand)
    if req.auth_type == "native":
        tok = create_password_token(user["id"])
        try:
            send_set_password_link(email, req.name, tok)
        except Exception as _e:
            print(f"[email] invite native error: {_e}")
    else:
        try:
            send_google_access_approved(email, req.name)
        except Exception as _e:
            print(f"[email] invite google error: {_e}")
    return {"status": "invited", "user_id": user["id"]}


@app.post("/api/admin/test-email")
async def admin_test_email(payload: dict = Depends(require_admin)):
    """Send a test email to the requesting admin to verify SMTP config."""
    import os
    from perception.email_utils import _send, _wrap, ADMIN_EMAIL, APP_URL
    to = payload.get("email") or ADMIN_EMAIL
    if not to or "@" not in to:
        raise HTTPException(400, "Cannot determine destination email — set ADMIN_NOTIFICATION_EMAIL env var")
    api_key = os.environ.get("RESEND_API_KEY", "")
    config_status = {
        "RESEND_API_KEY": "set" if api_key else "(not set)",
        "RESEND_FROM_DOMAIN": os.environ.get("RESEND_FROM_DOMAIN", "careclimb.com"),
        "APP_URL": APP_URL,
    }
    body = (
        "<h2 style='margin:0 0 12px;font-size:20px;'>Email Config Test</h2>"
        "<p>If you received this, SMTP is working correctly.</p>"
        "<table style='font-size:13px;margin-top:12px;border-collapse:collapse'>"
        + "".join(
            f"<tr><td style='padding:4px 12px 4px 0;color:#7a9095'>{k}</td>"
            f"<td style='padding:4px 0'>{v}</td></tr>"
            for k, v in config_status.items()
        )
        + "</table>"
    )
    try:
        _send(to, "SMTP Test", _wrap(body))
        return {"status": "sent", "to": to, "config": config_status}
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "config": config_status}


# ── Feedback ──────────────────────────────────────────────────────────────────
_FEEDBACK_ATTACH_DIR = REPORTS_DIR.parent / "feedback-attachments"
_FEEDBACK_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".docx", ".csv"}

class FeedbackActionRequest(BaseModel):
    action: str

class FeedbackEditRequest(BaseModel):
    title:  Optional[str] = None
    type:   Optional[str] = None
    body:   Optional[str] = None
    action: Optional[str] = None
    notes:  Optional[str] = None

@app.post("/api/feedback")
async def submit_feedback(
    payload: dict = Depends(get_current_user_payload),
    title: str = Form(...),
    type: str = Form(...),
    body: str = Form(...),
    files: List[UploadFile] = File(default=[]),
):
    from perception.db import init_db, create_feedback
    if type not in ("bug", "feature", "socialclimb"):
        raise HTTPException(400, "type must be 'bug', 'feature', or 'socialclimb'")
    if not title.strip():
        raise HTTPException(400, "title is required")
    if not body.strip():
        raise HTTPException(400, "body is required")
    init_db()

    # Save any uploaded attachments
    saved: list[str] = []
    import uuid as _uuid
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in _FEEDBACK_ALLOWED_EXTS:
            continue
        data = await f.read()
        if len(data) > 15 * 1024 * 1024:  # 15 MB cap per file
            continue
        tmp_id = str(_uuid.uuid4())
        attach_dir = _FEEDBACK_ATTACH_DIR / tmp_id
        attach_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(f.filename).name
        (attach_dir / safe_name).write_bytes(data)
        saved.append(f"{tmp_id}/{safe_name}")

    item = create_feedback(title.strip(), type, body.strip(),
                           payload.get("email", "unknown"), attachments=saved)
    # Move attachments into the feedback id folder now that we have it
    import shutil as _shutil
    for att in saved:
        tmp_id, fname = att.split("/", 1)
        src = _FEEDBACK_ATTACH_DIR / tmp_id / fname
        dst_dir = _FEEDBACK_ATTACH_DIR / item["id"]
        dst_dir.mkdir(parents=True, exist_ok=True)
        _shutil.move(str(src), str(dst_dir / fname))
        try:
            (_FEEDBACK_ATTACH_DIR / tmp_id).rmdir()
        except Exception:
            pass

    # Update stored attachment paths to use the real feedback id
    if saved:
        import json as _json
        final_atts = [f"{item['id']}/{att.split('/',1)[1]}" for att in saved]
        item["attachments"] = final_atts
        from perception.db import update_feedback as _uf
        _uf(item["id"], attachments=_json.dumps(final_atts))

    return item

@app.get("/api/feedback/{feedback_id}/attachment/{filename}")
async def get_feedback_attachment(
    feedback_id: str, filename: str, _: str = Depends(require_auth)
):
    """Serve an uploaded feedback attachment."""
    safe = Path(filename).name  # strip any path traversal
    path = _FEEDBACK_ATTACH_DIR / feedback_id / safe
    if not path.exists():
        raise HTTPException(404, "Attachment not found")
    return FileResponse(str(path), filename=safe)

@app.get("/api/feedback")
async def get_feedback(_: str = Depends(require_auth)):
    from perception.db import init_db, list_feedback
    try:
        init_db()
        return list_feedback()
    except Exception as exc:
        raise HTTPException(500, detail=f"{type(exc).__name__}: {exc}")

@app.patch("/api/feedback/{feedback_id}")
async def patch_feedback(feedback_id: str, req: FeedbackEditRequest, _: dict = Depends(require_admin)):
    from perception.db import init_db, update_feedback
    valid_actions = {"pending", "accepted", "fixed", "completed", "rejected"}
    if req.action is not None and req.action not in valid_actions:
        raise HTTPException(400, "invalid action")
    if req.type is not None and req.type not in ("bug", "feature", "socialclimb"):
        raise HTTPException(400, "type must be 'bug', 'feature', or 'socialclimb'")
    init_db()
    updates = {k: v for k, v in req.dict().items() if v is not None}
    update_feedback(feedback_id, **updates)
    return {"ok": True}


# ── Learn / educational content ───────────────────────────────────────────────

class LearnArticleRequest(BaseModel):
    category: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    is_published: Optional[bool] = None
    page: str = "learn"          # "learn" | "methodology"


class LearnMoveRequest(BaseModel):
    direction: str  # "up" | "down"


class LearnPreviewRequest(BaseModel):
    body: str = ""


def _learn_grouped(articles: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group already-ordered articles by category, preserving first-seen order."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for a in articles:
        cat = (a.get("category") or "").strip() or "General"
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
    for a in articles:
        cat = (a.get("category") or "").strip() or "General"
        groups[cat].append(a)
    return [(c, groups[c]) for c in order]


def _render_learn_page(articles: list[dict], *, title: str = "Learn about Pulse — AI Reputation Analysis Platform",
                       heading: str = "Learn about Pulse",
                       lede: str = "What Pulse is, what its reports reveal, who they help, and how to get started.",
                       desc: str = "Learn what Pulse is, what its AI-reputation reports show, who they help, and how to get started.",
                       empty_msg: str = "Content is coming soon. Check back shortly.") -> str:
    """Server-render a standalone, publicly-indexable content page (Learn / Methodology)."""
    from perception.learn import render_markdown
    grouped = _learn_grouped(articles)

    toc_html = ""
    body_html = ""
    if not articles:
        body_html = f'<div class="empty">{_esc_html(empty_msg)}</div>'
    else:
        for cat, arts in grouped:
            cat_anchor = "cat-" + re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-")
            toc_html += f'<div class="toc-cat"><a href="#{cat_anchor}">{_esc_html(cat)}</a></div>'
            body_html += f'<h2 class="cat-h" id="{cat_anchor}">{_esc_html(cat)}</h2>'
            for a in arts:
                anchor = "a-" + a["id"]
                toc_html += f'<div class="toc-item"><a href="#{anchor}">{_esc_html(a["title"])}</a></div>'
                body_html += (
                    f'<article class="learn-article" id="{anchor}">'
                    f'<h3>{_esc_html(a["title"])}</h3>'
                    f'<div class="learn-body">{render_markdown(a["body"])}</div>'
                    f'</article>'
                )

    return (_LEARN_PAGE_TEMPLATE
            .replace("{{TITLE}}", _esc_html(title))
            .replace("{{DESC}}", _esc_html(desc))
            .replace("{{HEADING}}", _esc_html(heading))
            .replace("{{LEDE}}", _esc_html(lede))
            .replace("{{TOC}}", toc_html)
            .replace("{{BODY}}", body_html))


def _esc_html(s: str) -> str:
    import html as _html
    return _html.escape(s or "")


_LEARN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<meta name="description" content="{{DESC}}">
<style>
  :root { --teal:#0f766e; --teal-d:#0b5a54; --ink:#1b2733; --muted:#5b6b7a; --line:#e2e8ec; --bg:#f6f8f9; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); background:var(--bg); line-height:1.6; }
  a { color:var(--teal); }
  header.top { background:linear-gradient(120deg,#0f766e,#0b5a54); color:#fff; padding:28px 32px; }
  header.top .wm { font-weight:800; letter-spacing:.14em; font-size:22px; }
  header.top .sub { opacity:.85; font-size:13px; letter-spacing:.06em; margin-top:2px; }
  header.top .signin { float:right; color:#fff; text-decoration:none; border:1px solid rgba(255,255,255,.5);
                       padding:8px 16px; border-radius:6px; font-size:13px; }
  header.top .signin:hover { background:rgba(255,255,255,.12); }
  .wrap { max-width:1080px; margin:0 auto; padding:0 24px; display:flex; gap:40px; align-items:flex-start; }
  nav.toc { position:sticky; top:24px; flex:0 0 240px; padding:28px 0; font-size:14px; }
  nav.toc .toc-cat { margin-top:16px; font-weight:700; }
  nav.toc .toc-cat:first-child { margin-top:0; }
  nav.toc .toc-cat a { color:var(--ink); text-decoration:none; }
  nav.toc .toc-item { margin:4px 0 4px 12px; }
  nav.toc .toc-item a { color:var(--muted); text-decoration:none; }
  nav.toc a:hover { color:var(--teal); }
  main { flex:1 1 auto; padding:32px 0 80px; min-width:0; }
  h1.page { font-size:28px; margin:0 0 4px; }
  .lede { color:var(--muted); margin:0 0 28px; font-size:15px; }
  h2.cat-h { font-size:13px; text-transform:uppercase; letter-spacing:.1em; color:var(--teal);
             border-bottom:1px solid var(--line); padding-bottom:8px; margin:40px 0 8px; }
  article.learn-article { padding:16px 0; border-bottom:1px solid var(--line); }
  article.learn-article h3 { font-size:20px; margin:8px 0 6px; }
  .learn-body :first-child { margin-top:0; }
  .learn-body img { max-width:100%; }
  .video-embed { position:relative; width:100%; aspect-ratio:16/9; border-radius:12px; overflow:hidden; background:#000; margin:14px 0; }
  .video-embed iframe { position:absolute; inset:0; width:100%; height:100%; border:0; }
  .learn-body pre { background:#0f1720; color:#e6edf3; padding:14px 16px; border-radius:8px; overflow:auto; }
  .learn-body code { background:#eef2f4; padding:2px 5px; border-radius:4px; font-size:.92em; }
  .learn-body pre code { background:none; padding:0; }
  .learn-body blockquote { border-left:3px solid var(--teal); margin:12px 0; padding:2px 16px; color:var(--muted); }
  .empty { color:var(--muted); padding:60px 0; text-align:center; }
  footer { text-align:center; color:var(--muted); font-size:13px; padding:32px; border-top:1px solid var(--line); }
  @media (max-width:820px) { .wrap { flex-direction:column; gap:0; } nav.toc { position:static; flex:none; padding:20px 0 0; } }
</style>
</head>
<body>
  <header class="top">
    <a class="signin" href="/">Sign In &rarr;</a>
    <div class="wm">PULSE</div>
    <div class="sub">AI REPUTATION ANALYSIS PLATFORM</div>
  </header>
  <div class="wrap">
    <nav class="toc">{{TOC}}</nav>
    <main>
      <h1 class="page">{{HEADING}}</h1>
      <p class="lede">{{LEDE}}</p>
      {{BODY}}
    </main>
  </div>
  <footer>Pulse &middot; AI Reputation Analysis Platform &nbsp;|&nbsp; <a href="/">Sign in to run reports</a></footer>
<!-- video embeds: return to poster when a video ends -->
<script>
function _resetVideoEmbed(f){
  // Reload the frame: guaranteed poster + play button, independent of the host's API.
  if(f.dataset.resetting) return; f.dataset.resetting='1';
  var src=f.getAttribute('src'); f.setAttribute('src','about:blank');
  setTimeout(function(){ f.setAttribute('src',src); delete f.dataset.resetting; },60);
}
function _bindVideoEmbeds(root){
  (root||document).querySelectorAll('.video-embed iframe[data-host]').forEach(function(f){
    if(f.dataset.bound) return; f.dataset.bound='1';
    var host=f.dataset.host;
    var arm=function(){
      try{
        if(host==='vimeo'){ f.contentWindow.postMessage(JSON.stringify({method:'addEventListener',value:'ended'}),'*'); f.contentWindow.postMessage(JSON.stringify({method:'addEventListener',value:'finish'}),'*'); }
        else if(host==='youtube') f.contentWindow.postMessage(JSON.stringify({event:'listening',id:f.id||'yt',channel:'widget'}),'*');
      }catch(e){}
    };
    f.addEventListener('load',arm); arm();
  });
}
window.addEventListener('message',function(ev){
  var d=ev.data; if(typeof d==='string'){ try{ d=JSON.parse(d);}catch(e){ return; } }
  if(!d||typeof d!=='object') return;
  document.querySelectorAll('.video-embed iframe[data-host]').forEach(function(f){
    if(f.contentWindow!==ev.source) return;
    var host=f.dataset.host, w=f.contentWindow;
    try{
      if(host==='vimeo'){
        if(d.event==='ready'){ w.postMessage(JSON.stringify({method:'addEventListener',value:'ended'}),'*'); w.postMessage(JSON.stringify({method:'addEventListener',value:'finish'}),'*'); }
        if(d.event==='ended'||d.event==='finish'){ try{ w.postMessage(JSON.stringify({method:'unload'}),'*'); }catch(e){} _resetVideoEmbed(f); }
      } else if(host==='youtube' && d.event==='infoDelivery' && d.info && d.info.playerState===0){
        try{ w.postMessage(JSON.stringify({event:'command',func:'stopVideo',args:[]}),'*'); }catch(e){} _resetVideoEmbed(f);
      }
    }catch(e){}
  });
});
_bindVideoEmbeds(document);
</script>
</body>
</html>"""


@app.get("/learn", response_class=HTMLResponse)
async def learn_public_page():
    """Public, unauthenticated, server-rendered educational page (indexable)."""
    from perception.db import init_db, list_learn_articles
    try:
        init_db()
        articles = list_learn_articles(include_unpublished=False, page="learn")
    except Exception:
        articles = []
    return HTMLResponse(_render_learn_page(articles))


@app.get("/methodology", response_class=HTMLResponse)
async def methodology_public_page():
    """Public, unauthenticated methodology page linked from report appendices."""
    from perception.db import init_db, list_learn_articles
    try:
        init_db()
        articles = list_learn_articles(include_unpublished=False, page="methodology")
    except Exception:
        articles = []
    return HTMLResponse(_render_learn_page(
        articles,
        title="Methodology — Pulse AI Reputation Analysis Platform",
        heading="Pulse AI Reputation Methodology",
        lede="How Pulse measures AI reputation — the pillars, scoring rubric, national quartiles, data sources, and prompt battery behind every report.",
        desc="The full methodology behind Pulse AI Reputation reports: pillars, scoring, quartiles, data sources, and prompt battery.",
        empty_msg="The full methodology is being published. Check back shortly.",
    ))


_CONTENT_PAGES = ("learn", "methodology", "home")


@app.get("/api/learn")
async def learn_public_api(page: str = "learn"):
    """Published content (rendered HTML) for a content page — used by the in-app
    Learn view and the Home page. Public."""
    from perception.db import init_db, list_learn_articles
    from perception.learn import render_markdown
    if page not in _CONTENT_PAGES:
        raise HTTPException(400, "unknown content page")
    init_db()
    arts = list_learn_articles(include_unpublished=False, page=page)
    return [{"id": a["id"], "category": a["category"], "title": a["title"],
             "html": render_markdown(a["body"])} for a in arts]


@app.get("/api/admin/learn")
async def learn_admin_list(page: str = "learn", _: dict = Depends(require_admin)):
    from perception.db import init_db, list_learn_articles
    init_db()
    return list_learn_articles(include_unpublished=True, page=page)


@app.post("/api/admin/learn")
async def learn_admin_create(req: LearnArticleRequest, _: dict = Depends(require_admin)):
    from perception.db import init_db, create_learn_article
    if not (req.title or "").strip():
        raise HTTPException(400, "title is required")
    init_db()
    return create_learn_article(
        category=(req.category or "").strip(),
        title=req.title.strip(),
        body=req.body or "",
        is_published=True if req.is_published is None else req.is_published,
        page=req.page or "learn",
    )


@app.put("/api/admin/learn/{article_id}")
async def learn_admin_update(article_id: str, req: LearnArticleRequest,
                             _: dict = Depends(require_admin)):
    from perception.db import init_db, update_learn_article, get_learn_article
    init_db()
    if get_learn_article(article_id) is None:
        raise HTTPException(404, "Article not found")
    updates = {}
    if req.category is not None:     updates["category"] = req.category.strip()
    if req.title is not None:        updates["title"] = req.title.strip()
    if req.body is not None:         updates["body"] = req.body
    if req.is_published is not None: updates["is_published"] = req.is_published
    update_learn_article(article_id, **updates)
    return get_learn_article(article_id)


@app.delete("/api/admin/learn/{article_id}")
async def learn_admin_delete(article_id: str, _: dict = Depends(require_admin)):
    from perception.db import init_db, delete_learn_article
    init_db()
    delete_learn_article(article_id)
    return {"ok": True}


@app.post("/api/admin/learn/{article_id}/move")
async def learn_admin_move(article_id: str, req: LearnMoveRequest,
                           _: dict = Depends(require_admin)):
    from perception.db import init_db, move_learn_article
    if req.direction not in ("up", "down"):
        raise HTTPException(400, "direction must be 'up' or 'down'")
    init_db()
    move_learn_article(article_id, req.direction)
    return {"ok": True}


@app.post("/api/admin/learn/preview")
async def learn_admin_preview(req: LearnPreviewRequest, _: dict = Depends(require_admin)):
    """Render Markdown exactly as the public page will, for the editor preview."""
    from perception.learn import render_markdown
    return {"html": render_markdown(req.body)}


@app.post("/api/admin/learn/seed")
async def learn_admin_seed(page: str = "learn", _: dict = Depends(require_admin)):
    """Insert the starter articles for a page. Idempotent — skips existing titles."""
    from perception.db import init_db, list_learn_articles, create_learn_article
    from perception.learn_seed import STARTER_ARTICLES, METHODOLOGY_ARTICLES, HOME_ARTICLES
    init_db()
    arts = (METHODOLOGY_ARTICLES if page == "methodology"
            else HOME_ARTICLES if page == "home" else STARTER_ARTICLES)
    existing = {a["title"].strip().lower()
                for a in list_learn_articles(include_unpublished=True, page=page)}
    added = 0
    for art in arts:
        if art["title"].strip().lower() in existing:
            continue
        create_learn_article(category=art["category"], title=art["title"],
                             body=art["body"], is_published=True, page=page)
        added += 1
    return {"added": added, "skipped": len(arts) - added}


# ── Tracked Entities ──────────────────────────────────────────────────────────

class TrackEntityRequest(BaseModel):
    entity_name: str
    city: str
    state: str
    specialty: Optional[str] = None
    aggregate: bool = True
    schedule: str = "monthly"   # "monthly" | "weekly" | "manual"
    notes: str = ""
    entity_type: str = "hospital"          # "hospital" | "practice" | "service_line" | "community_health" | "hospital_network"
    force: bool = False                    # add even when the same organization is already tracked
    service_line: Optional[str] = None     # service_line type: the department (e.g. Orthopedics)
    parent_system: Optional[str] = None    # service_line type: the health system
    confirmed_roster: Optional[List[dict]] = None   # practice types: the fixed Locations list every snapshot uses
    anchor_listing: Optional[dict] = None           # flagship Google listing (place_id, rating, …)
    facility_type: Optional[str] = None             # hospital_network: hospital | asc | urgent_care | imaging | behavioral_health | other
    source_url: Optional[str] = None                # hospital_network: locations page (optional)


def _entity_points(entity: dict) -> list:
    """Snapshot history for any tracked entity type (network runs vs Deep Diagnostic runs)."""
    from perception.db import get_entity_trend, get_network_trend
    if (entity.get("entity_type") or "hospital") == "hospital_network":
        return get_network_trend(entity["entity_name"])
    return get_entity_trend(entity["entity_name"])


def _entity_roster(entity: dict):
    """(confirmed_roster list or None, anchor_listing dict or None) from a tracked entity row."""
    def _load(v):
        if v is None or v == "":
            return None
        if isinstance(v, (list, dict)):
            return v
        try:
            return json.loads(v)
        except Exception:
            return None
    return _load(entity.get("confirmed_roster")), _load(entity.get("anchor_listing"))


def _launch_tracked_run(entity: dict, brand: str = "original", email: Optional[str] = None,
                        owner_key: Optional[str] = None) -> str:
    """Create the snapshot job for a tracked entity, routed by its type: hospitals go through
    the hospital analyzer, practices and service lines through the practice analyzer (practice
    rubric, roster-based reviews pillar) — the same routing every other report uses.
    The run is attributed ("Run by" in History) to the person who launched it, or — for
    scheduled snapshots — to whoever set up the tracking."""
    job_id = _new_job("admin", brand, email or None)
    j = _jobs[job_id]
    j["ran_by"] = email or entity.get("created_by") or None
    if owner_key:
        j["owner"] = owner_key
    j["tracked_entity_id"] = entity["id"]
    j["force_rerun"] = True            # a snapshot must be a fresh analysis, not the 30-day cached result
    j["entity_name"] = entity["entity_name"]
    j["label"] = f"{entity.get('display_name') or entity['entity_name']} — Trends snapshot"
    j["individual_report"] = True
    j["skip_pdf"] = True              # data-only snapshot; the Trend Report is the deliverable
    j["patient_perspective"] = False
    j["teaser_report"] = False
    j["zip_code"] = None
    etype = entity.get("entity_type") or "hospital"
    if etype in ("practice", "service_line"):
        roster, anchor = _entity_roster(entity)
        j["entity_type"] = "practice"
        j["practice_profile"] = None            # auto-classified from the specialty
        j["practice_composite"] = False
        j["practice_roster"] = []
        j["physician_composite"] = False
        j["physician_roster"] = {}
        # A saved roster makes every snapshot measure the SAME locations (no drift);
        # without one the analyzer discovers locations each run (legacy entities).
        j["confirmed_siblings"] = roster if roster is not None else None
        j["anchor_listing"] = anchor
        j["org_name"] = None
        if etype == "service_line":
            j["service_line"] = entity.get("service_line")
            j["parent_system"] = entity.get("parent_system")
    elif etype == "hospital_network":
        j["entity_type"] = "hospital_network"
        j["kind"] = "Hospital Network"
        j["skip_pdf"] = False              # the network analyzer always renders its reports
    _pool.submit(_run_tracked_and_notify, job_id, entity)
    return job_id

class TrackEntityUpdate(BaseModel):
    # Only fields that never change what is being measured are editable. Identity
    # (entity_name, city/state, specialty, aggregate) is locked: changing any of them
    # would make earlier snapshots non-comparable — track a new entity instead.
    active: Optional[bool] = None
    schedule: Optional[str] = None
    notes: Optional[str] = None
    next_run_at: Optional[str] = None      # ISO date or datetime
    email_report: Optional[bool] = None    # email the Trend Report after each run
    report_emails: Optional[List[str]] = None
    display_name: Optional[str] = None     # shown in Trends + Trend Report title; identity (entity_name) stays locked
    alert_on_change: Optional[bool] = None # email when a snapshot moves 5+ points or changes quartile
    confirmed_roster: Optional[List[dict]] = None   # allowed once, to fix an entity tracked before rosters existed
    anchor_listing: Optional[dict] = None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_emails(emails) -> list:
    out = []
    for e in emails or []:
        e = str(e or "").strip().lower()
        if e and _EMAIL_RE.match(e) and e not in out:
            out.append(e)
    return out


def _entity_report_emails(entity: dict) -> list:
    v = entity.get("report_emails")
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except Exception:
        return []


@app.get("/api/track/entities")
async def track_list(_: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, list_tracked_entities, list_trend_acks
    init_db()
    entities = list_tracked_entities()
    acks = list_trend_acks()
    for e in entities:
        for k in ("last_run_at", "next_run_at", "created_at"):
            if e.get(k):
                e[k] = str(e[k])
        e["report_emails"] = _entity_report_emails(e)
        e["acks"] = acks.get(e["id"], [])
        roster, anchor = _entity_roster(e)
        e["confirmed_roster"] = roster
        e["anchor_listing"] = anchor
        e["roster_count"] = (1 + len(roster)) if roster is not None else None
    return entities


@app.post("/api/track/entities")
async def track_create(req: TrackEntityRequest, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, create_tracked_entity, mark_tracked_entity_ran
    if req.schedule not in ("monthly", "weekly", "manual"):
        raise HTTPException(400, "schedule must be monthly, weekly, or manual")
    init_db()
    created_by = payload.get("email") or payload.get("uid") or "admin"
    if req.entity_type not in ("hospital", "practice", "service_line", "community_health", "hospital_network"):
        raise HTTPException(400, "entity_type must be hospital, practice, service_line, community_health, or hospital_network")
    if not req.force:
        # Duplicate guard: the same organization in the same state is already being tracked.
        # Trends match snapshots by name, so a second copy shows the same line twice and
        # doubles the scheduled analysis runs. The client turns this into "open it / add anyway".
        from perception.db import list_tracked_entities as _list_te
        _nm = _normalize_input(req.entity_name).strip().lower()
        _st = req.state.upper().strip()
        _dups = [e for e in _list_te() if e.get("active")
                 and (e.get("entity_name") or "").strip().lower() == _nm
                 and (e.get("state") or "").upper().strip() == _st]
        if _dups:
            _d = sorted(_dups, key=lambda e: str(e.get("created_at") or ""))[0]
            raise HTTPException(409, detail={
                "code": "already_tracked", "entity_id": _d["id"], "entity_name": _d.get("entity_name"),
                "entity_type": _d.get("entity_type") or "hospital", "created_by": _d.get("created_by"),
                "created_at": str(_d.get("created_at") or "")[:10], "count": len(_dups),
                "message": f"{_d.get('entity_name')} is already being tracked."})
    if req.entity_type == "hospital_network" and not req.confirmed_roster:
        raise HTTPException(400, "A Hospital Network needs its facility roster (confirm the hospitals first)")
    if req.entity_type == "service_line" and not (req.service_line and req.parent_system):
        raise HTTPException(400, "service_line and parent_system are required for a service line")
    entity = create_tracked_entity(
        entity_name=_normalize_input(req.entity_name),
        city=_normalize_input(req.city),
        state=req.state.upper().strip(),
        specialty=_normalize_input(req.specialty) if req.specialty else None,
        aggregate=req.aggregate if req.entity_type == "hospital" else True,
        schedule=req.schedule,
        created_by=created_by,
        notes=req.notes,
        entity_type=req.entity_type,
        service_line=_normalize_input(req.service_line) if req.service_line else None,
        parent_system=_normalize_input(req.parent_system) if req.parent_system else None,
        confirmed_roster=json.dumps(req.confirmed_roster) if (req.entity_type != "hospital" and req.confirmed_roster is not None) else None,
        anchor_listing=json.dumps(req.anchor_listing) if req.anchor_listing else None,
    )
    if req.entity_type == "hospital_network":
        from perception.db import update_tracked_entity as _upd
        _upd(entity["id"], facility_type=(req.facility_type or "hospital"), source_url=(req.source_url or None))
        entity["facility_type"] = req.facility_type or "hospital"; entity["source_url"] = req.source_url or None
    if req.confirmed_roster is not None and req.entity_type in ("practice", "service_line", "community_health"):
        from perception.graph import upsert_org as _g_upsert
        _graph_write(_g_upsert, entity["entity_name"], entity["city"], entity["state"], req.entity_type, specialty=entity.get("specialty"),
                     anchor=req.anchor_listing, locations=req.confirmed_roster, source="trends", by=created_by, replace_locations=True)
    # Fire initial collection run immediately so the first data point is captured now.
    job_id = _launch_tracked_run(entity, payload.get("brand", "original"), payload.get("email"),
                                 owner_key=_owner_key(payload.get("email"), payload.get("role")))
    mark_tracked_entity_ran(entity["id"], entity.get("schedule", "monthly"))
    for k in ("last_run_at", "next_run_at", "created_at"):
        if entity and entity.get(k):
            entity[k] = str(entity[k])
    entity["initial_job_id"] = job_id
    return entity


@app.put("/api/track/entities/{entity_id}")
async def track_update(entity_id: str, req: TrackEntityUpdate, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_tracked_entity, update_tracked_entity
    init_db()
    if not get_tracked_entity(entity_id):
        raise HTTPException(404, "tracked entity not found")
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if "schedule" in updates and updates["schedule"] not in ("monthly", "weekly", "manual"):
        raise HTTPException(400, "schedule must be monthly, weekly, or manual")
    if "next_run_at" in updates:
        from datetime import datetime as _dt
        try:
            v = str(updates["next_run_at"]).strip()
            updates["next_run_at"] = _dt.fromisoformat(v + ("T00:00:00" if len(v) == 10 else ""))
        except Exception:
            raise HTTPException(400, "next_run_at must be an ISO date (YYYY-MM-DD)")
    if "report_emails" in updates:
        cleaned = _clean_emails(updates["report_emails"])
        if updates["report_emails"] and not cleaned:
            raise HTTPException(400, "report_emails must contain valid email addresses")
        updates["report_emails"] = json.dumps(cleaned)
    if "display_name" in updates:
        dn = " ".join(str(updates["display_name"]).split())[:120]
        updates["display_name"] = dn or None          # blank → fall back to the tracked name
    if "confirmed_roster" in updates or "anchor_listing" in updates:
        # A roster fixes WHAT is measured, so it is settable only while the entity has none
        # (entities tracked before rosters existed). After that it is locked like the identity.
        cur = get_tracked_entity(entity_id)
        if _entity_roster(cur)[0] is not None:
            raise HTTPException(400, "This entity already has a confirmed roster. Track a new entity to change what is measured.")
        if "confirmed_roster" in updates:
            updates["confirmed_roster"] = json.dumps(list(updates["confirmed_roster"] or []))
        if "anchor_listing" in updates:
            updates["anchor_listing"] = json.dumps(updates["anchor_listing"] or {})
    update_tracked_entity(entity_id, **updates)
    entity = get_tracked_entity(entity_id)
    for k in ("last_run_at", "next_run_at", "created_at"):
        if entity and entity.get(k):
            entity[k] = str(entity[k])
    return entity


@app.delete("/api/track/entities/{entity_id}")
async def track_delete(entity_id: str, purge_runs: int = 0, _: dict = Depends(require_admin)):
    """Admin: delete a tracking instance. purge_runs=1 also deletes its snapshot runs
    (individual runs matched by entity name) and their files — permanent."""
    from perception.db import init_db, delete_tracked_entity, delete_trend_reports_for_entity
    init_db()
    res = delete_tracked_entity(entity_id, purge_runs=bool(purge_runs))
    if res is None:
        raise HTTPException(404, "tracked entity not found")
    files = list(res.get("files") or [])
    safe_id = "".join(ch for ch in entity_id if ch.isalnum() or ch in "-_")
    files += [str(p) for p in (REPORTS_DIR / "trends").glob(f"trend_{safe_id}_*.pdf")]   # download cache
    if purge_runs:
        files += delete_trend_reports_for_entity(entity_id)                               # sent artifacts
    for p in files:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    if res.get("runs_deleted"):
        _DIR_LIST_CACHE.clear()
    return {"ok": True, "runs_deleted": res.get("runs_deleted", 0)}


@app.get("/api/track/entities/{entity_id}/trend")
async def track_trend(entity_id: str, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_tracked_entity, get_entity_trend
    init_db()
    entity = get_tracked_entity(entity_id)
    if not entity:
        raise HTTPException(404, "tracked entity not found")
    from perception.db import list_annotations
    data = _entity_points(entity)
    return {"entity": entity, "data_points": data, "annotations": list_annotations(entity_id)}


class AnnotationRequest(BaseModel):
    note_date: str
    note: str


@app.get("/api/track/entities/{entity_id}/annotations")
async def track_annotations(entity_id: str, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, list_annotations
    init_db()
    return list_annotations(entity_id)


@app.post("/api/track/entities/{entity_id}/annotations")
async def track_annotation_add(entity_id: str, req: AnnotationRequest, payload: dict = Depends(get_current_user_payload)):
    """Add a dated note ("new website launched") to a tracked entity's trend."""
    from datetime import date as _date
    from perception.db import init_db, get_tracked_entity, add_annotation
    init_db()
    if not get_tracked_entity(entity_id):
        raise HTTPException(404, "tracked entity not found")
    note = (req.note or "").strip()
    if not note:
        raise HTTPException(400, "Enter a note")
    if len(note) > 300:
        raise HTTPException(400, "Keep the note under 300 characters")
    try:
        d = _date.fromisoformat(req.note_date[:10])
    except Exception:
        raise HTTPException(400, "Invalid date")
    return add_annotation(entity_id, d, note, payload.get("email") or payload.get("name") or "")


@app.delete("/api/track/annotations/{aid}")
async def track_annotation_delete(aid: str, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_annotation, delete_annotation
    init_db()
    a = get_annotation(aid)
    if not a:
        raise HTTPException(404, "note not found")
    me = (payload.get("email") or "").lower()
    if payload.get("role") != "admin" and (a.get("created_by") or "").lower() != me:
        raise HTTPException(403, "Only the person who added the note (or an admin) can remove it")
    delete_annotation(aid)
    return {"deleted": aid}


@app.get("/api/track/entities/{entity_id}/report.pdf")
async def track_report_pdf(entity_id: str, payload: dict = Depends(get_current_user_payload)):
    """AI Reputation Trend Report for one tracked entity. Rendered on demand and cached
    per latest snapshot (a new run invalidates the cache naturally via the filename)."""
    from perception.db import init_db, get_tracked_entity, get_entity_trend
    from perception.trend_pdf import render_trend_report_pdf
    init_db()
    entity = get_tracked_entity(entity_id)
    if not entity:
        raise HTTPException(404, "tracked entity not found")
    points = _entity_points(entity)
    if not points:
        raise HTTPException(404, "No snapshots yet — run the entity at least once first.")
    pdf_path = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _trend_report_file(entity, points, payload.get("brand", "original")))
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(entity.get("display_name") or entity.get("entity_name") or "entity")).strip("-")[:60]
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{slug}_AI_Reputation_Trend_Report.pdf"'})


def _trend_report_file(entity: dict, points: list, brand: str = "original") -> Path:
    """Render (or reuse) the Trend Report PDF for the entity's latest snapshot. Sync."""
    from perception.trend_pdf import render_trend_report_pdf
    from perception.db import list_annotations
    import hashlib
    latest = points[-1].get("run_id") or "none"
    out_dir = REPORTS_DIR / "trends"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in str(entity.get("id", "")) if ch.isalnum() or ch in "-_")
    annotations = list_annotations(str(entity.get("id", "")))
    # Notes are part of the report: fingerprint them into the cache name so an added or
    # removed note rebuilds the PDF.
    a_fp = hashlib.sha1(("|".join(f"{a['note_date']}:{a['note']}" for a in annotations) + "#" + str(entity.get("display_name") or "")).encode()).hexdigest()[:8]
    pdf_path = out_dir / f"trend_{safe_id}_{latest}_v4_{a_fp}.pdf"   # bump suffix when the layout changes
    if not pdf_path.exists():
        ent = dict(entity)
        for k in ("last_run_at", "next_run_at", "created_at"):
            if ent.get(k):
                ent[k] = str(ent[k])
        render_trend_report_pdf(ent, points, str(pdf_path), brand=brand, annotations=annotations)
        # The download cache holds only the current version; sent copies live in trends/sent/.
        for old in out_dir.glob(f"trend_{safe_id}_*.pdf"):
            if old != pdf_path:
                try:
                    old.unlink()
                except Exception:
                    pass
    return pdf_path


def _email_trend_report(entity_id: str, emails: list, brand: str = "original",
                        sent_by: str = "scheduler", kind: str = "email") -> int:
    """Render the current Trend Report, email it to each address, and keep the exact file
    that went out as a permanent 'sent report' artifact. Returns the number of sends."""
    import shutil
    from datetime import datetime as _dt
    from perception.db import get_tracked_entity, get_entity_trend, record_trend_report
    from perception.email_utils import send_trend_report
    entity = get_tracked_entity(entity_id)
    if not entity:
        return 0
    points = _entity_points(entity)
    if not points:
        return 0
    pdf_path = _trend_report_file(entity, points, brand)
    scored = [p for p in points if p.get("ai_visibility_score") is not None]
    latest = scored[-1]["ai_visibility_score"] if scored else None
    delta = (scored[-1]["ai_visibility_score"] - scored[-2]["ai_visibility_score"]) if len(scored) >= 2 else None
    def _d(v):
        try:
            from datetime import date as _date
            return _date.fromisoformat(str(v)[:10]).strftime("%b %-d, %Y")
        except Exception:
            return str(v)[:10]
    period = (_d(scored[0]["generated_at"]), _d(scored[-1]["generated_at"])) if scored else ("", "")
    # Name a person: the user who clicked Send now, else whoever set up the tracking.
    sender = sent_by if kind == "send_now" else (entity.get("created_by") or "")
    if "@" in str(sender):
        sender = str(sender).split("@")[0].replace(".", " ").title()
    delivered = []
    for addr in _clean_emails(emails):
        try:
            send_trend_report(addr, entity.get("display_name") or entity["entity_name"], str(pdf_path),
                              latest_score=latest, delta=delta, snapshots=len(scored),
                              sender=sender, period=period)
            delivered.append(addr)
        except Exception as exc:
            print(f"[trend-email] FAILED entity={entity_id} to={addr}: {type(exc).__name__}: {exc}")
    if delivered:
        try:
            sent_dir = REPORTS_DIR / "trends" / "sent"
            sent_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(ch for ch in entity_id if ch.isalnum() or ch in "-_")
            stamp = _dt.utcnow().strftime("%Y%m%d-%H%M%S")
            keep = sent_dir / f"{safe_id}_{stamp}.pdf"
            shutil.copyfile(str(pdf_path), str(keep))
            record_trend_report(entity_id, entity["entity_name"], points[-1].get("run_id"), str(keep),
                                sent_by, delivered, kind=kind, snapshots=len(scored), latest_score=latest)
        except Exception as exc:
            print(f"[trend-email] could not archive sent report entity={entity_id}: {type(exc).__name__}: {exc}")
    return len(delivered)


def _run_tracked_and_notify(job_id: str, entity: dict) -> None:
    """Run one tracked-entity snapshot, then (opt-in) email the refreshed Trend Report."""
    if (entity.get("entity_type") or "hospital") == "hospital_network":
        roster, _ = _entity_roster(entity)
        hq = ", ".join(x for x in [entity.get("city"), entity.get("state")] if x)
        # Monthly-style snapshot: same roster every time, teaser/full-detail/scorecard off.
        _job_network_analyze(job_id, entity["entity_name"], hq, entity.get("source_url") or "",
                             list(roster or []), entity.get("facility_type") or "hospital", "original",
                             ignore_cache=True, teaser=False, service_line_audit=False, full_detail=False)
    elif (entity.get("entity_type") or "hospital") in ("practice", "service_line"):
        _job_run_practice(job_id, entity["entity_name"], entity["city"], entity["state"],
                          entity.get("specialty"), True, None)
    elif (entity.get("entity_type") or "hospital") == "community_health":
        # Community Health Edition snapshot: same confirmed site roster every time, no intake.
        roster, _ = _entity_roster(entity)
        j = _jobs[job_id]
        j["site_roster"] = [(r.get("name") if isinstance(r, dict) else str(r)) for r in (roster or []) if r]
        j["fqhc_intake"] = None
        j["entity_name"] = entity["entity_name"]
        _job_run_fqhc(job_id, entity["entity_name"], entity["city"], entity["state"], True)
    else:
        _job_run_single(job_id, entity["city"], entity["state"], entity.get("specialty"),
                        entity.get("aggregate", True), None)
    try:
        if _jobs.get(job_id, {}).get("status") != "done":
            return
        _maybe_send_change_alert(entity)
        if not entity.get("email_report"):
            return
        emails = _entity_report_emails(entity)
        if emails:
            n = _email_trend_report(entity["id"], emails, sent_by="scheduled run", kind="scheduled")
            print(f"[trend-email] entity={entity['id']} sent={n}")
    except Exception as exc:
        print(f"[trend-email] hook error entity={entity.get('id')}: {type(exc).__name__}: {exc}")


_ALERT_MIN_MOVE = 5


def _maybe_send_change_alert(entity: dict) -> int:
    """After a snapshot: if change alerts are on and the score moved 5+ points or changed
    quartile versus the previous day's snapshot, email the report recipients (or the person
    who set up tracking). Returns the number of alerts sent. Fail-soft."""
    try:
        from perception.db import get_tracked_entity, get_entity_trend
        from perception.email_utils import send_trend_alert
        from perception import scoring
        ent = get_tracked_entity(entity["id"]) or entity
        if not ent.get("alert_on_change"):
            return 0
        pts = [p for p in _entity_points(ent) if p.get("ai_visibility_score") is not None]
        # one point per day (last run of the day), so a same-day re-run never triggers an alert
        by_day = {}
        for p in pts:
            by_day[str(p.get("generated_at"))[:10]] = p
        days = sorted(by_day)
        if len(days) < 2:
            return 0
        latest, prev = by_day[days[-1]], by_day[days[-2]]
        delta = int(latest["ai_visibility_score"]) - int(prev["ai_visibility_score"])
        q_prev, l_prev = scoring.grade_from_score(prev["ai_visibility_score"])
        q_now, l_now = scoring.grade_from_score(latest["ai_visibility_score"])
        if abs(delta) < _ALERT_MIN_MOVE and q_prev == q_now:
            return 0
        emails = _entity_report_emails(ent) or ([ent["created_by"]] if "@" in str(ent.get("created_by") or "") else [])
        emails = _clean_emails(emails)
        if not emails:
            return 0
        pdf = None
        try:
            pdf = str(_trend_report_file(ent, _entity_points(ent), "original"))
        except Exception:
            pdf = None
        name = ent.get("display_name") or ent["entity_name"]
        sent = 0
        for addr in emails:
            try:
                send_trend_alert(addr, name, latest=int(latest["ai_visibility_score"]), previous=int(prev["ai_visibility_score"]),
                                 delta=delta, quartile_prev=l_prev, quartile_now=l_now,
                                 snapshot_date=str(latest.get("generated_at"))[:10], pdf_path=pdf)
                sent += 1
            except Exception as exc:
                print(f"[trend-alert] FAILED to={addr}: {type(exc).__name__}: {exc}")
        print(f"[trend-alert] entity={ent.get('id')} delta={delta} sent={sent}")
        return sent
    except Exception as exc:
        print(f"[trend-alert] hook error: {type(exc).__name__}: {exc}")
        return 0


class TrendSendRequest(BaseModel):
    emails: List[str]


@app.post("/api/track/entities/{entity_id}/report/send")
async def track_report_send(entity_id: str, req: TrendSendRequest,
                            payload: dict = Depends(get_current_user_payload)):
    """Send the current Trend Report PDF to the given addresses now."""
    from perception.db import init_db, get_tracked_entity
    init_db()
    if not get_tracked_entity(entity_id):
        raise HTTPException(404, "tracked entity not found")
    emails = _clean_emails(req.emails)
    if not emails:
        raise HTTPException(400, "Provide at least one valid email address")
    who = payload.get("name") or payload.get("email") or payload.get("role") or "user"
    sent = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _email_trend_report(entity_id, emails, payload.get("brand", "original"),
                                          sent_by=str(who), kind="send_now"))
    if sent == 0:
        raise HTTPException(502, "The report could not be sent — check the email service configuration.")
    return {"sent": sent, "emails": emails}


@app.get("/api/track/entities/{entity_id}/reports")
async def track_reports_list(entity_id: str, _: str = Depends(require_auth)):
    """Sent Trend Reports for an entity (newest first) — the exact files that went out."""
    from perception.db import init_db, list_trend_reports
    init_db()
    rows = list_trend_reports(entity_id)
    for r in rows:
        r["has_pdf"] = bool(r.get("pdf_path") and Path(r["pdf_path"]).exists())
        r.pop("pdf_path", None)
    return rows


@app.get("/api/track/reports/{report_id}/pdf")
async def track_report_download(report_id: str, _: str = Depends(require_auth)):
    from perception.db import init_db, get_trend_report
    init_db()
    r = get_trend_report("".join(ch for ch in report_id if ch.isalnum()))
    if not r or not Path(r["pdf_path"]).exists():
        raise HTTPException(404, "Sent report file not found")
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(r.get("entity_name") or "entity")).strip("-")[:60]
    stamp = str(r.get("sent_at") or "")[:10]
    return FileResponse(r["pdf_path"], media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{slug}_AI_Reputation_Trend_Report_{stamp}.pdf"'})


@app.post("/api/track/entities/{entity_id}/run")
async def track_run_now(entity_id: str, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_tracked_entity, mark_tracked_entity_ran
    init_db()
    entity = get_tracked_entity(entity_id)
    if not entity:
        raise HTTPException(404, "tracked entity not found")

    running = next((jid for jid, j in _jobs.items()
                    if j.get("status") == "running" and j.get("tracked_entity_id") == entity_id), None)
    if running:
        raise HTTPException(409, "An analysis run for this entity is already in progress — it will appear as a snapshot when it finishes.")
    etype = entity.get("entity_type") or "hospital"
    if etype in ("hospital", "practice", "service_line"):
        from perception.db import get_recent_run
        today = get_recent_run(entity["entity_name"], f"{entity.get('city')}, {entity.get('state')}", days=0,
                               entity_type="practice" if etype != "hospital" else "hospital")
        if today:
            return {"already_today": True, "run_id": today.get("run_id"),
                    "message": "A snapshot was already taken today; the next analysis run will add a new point tomorrow or on schedule."}
    job_id = _launch_tracked_run(entity, payload.get("brand", "original"), payload.get("email"),
                                 owner_key=_owner_key(payload.get("email"), payload.get("role")))
    mark_tracked_entity_ran(entity_id, entity.get("schedule", "monthly"))
    return {"job_id": job_id, "label": _jobs[job_id].get("label")}


class AckRequest(BaseModel):
    reason: str
    fingerprint: str = ""


@app.post("/api/track/entities/{entity_id}/ack")
async def track_ack(entity_id: str, req: AckRequest, payload: dict = Depends(get_current_user_payload)):
    """Mark a 'needs attention' reason as reviewed for the data that raised it."""
    from perception.db import init_db, get_tracked_entity, set_trend_ack
    init_db()
    if not get_tracked_entity(entity_id):
        raise HTTPException(404, "tracked entity not found")
    reason = (req.reason or "").strip()[:40]
    if not reason:
        raise HTTPException(400, "reason required")
    set_trend_ack(entity_id, reason, (req.fingerprint or "")[:300], payload.get("email") or payload.get("name") or "")
    return {"ok": True}


@app.delete("/api/track/entities/{entity_id}/ack/{reason}")
async def track_unack(entity_id: str, reason: str, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, clear_trend_ack
    init_db()
    clear_trend_ack(entity_id, reason[:40])
    return {"ok": True}


@app.post("/api/track/run-due")
async def track_run_due(payload: dict = Depends(require_admin)):
    """Admin: launch every active entity whose next run is due (same as the scheduler)."""
    from perception.db import init_db, get_due_tracked_entities, mark_tracked_entity_ran
    init_db()
    due = get_due_tracked_entities()
    launched = []
    for entity in due:
        job_id = _launch_tracked_run(entity, payload.get("brand", "original"))
        mark_tracked_entity_ran(entity["id"], entity.get("schedule", "monthly"))
        launched.append({"entity_id": entity["id"], "entity_name": entity["entity_name"], "job_id": job_id})
    return {"launched": launched}


@app.post("/api/track/scheduled")
async def track_scheduled(request: Request):
    """Called by Cloud Scheduler. Runs all entities due for a collection."""
    from perception.db import init_db, get_due_tracked_entities, mark_tracked_entity_ran
    # Simple shared-secret auth — set SCHEDULER_SECRET env var, pass in header.
    secret = request.headers.get("X-Scheduler-Secret", "")
    import os
    expected = os.environ.get("SCHEDULER_SECRET", "")
    if expected and secret != expected:
        raise HTTPException(403, "invalid scheduler secret")
    init_db()
    due = get_due_tracked_entities()
    launched = []
    for entity in due:
        job_id = _launch_tracked_run(entity, "original")
        mark_tracked_entity_ran(entity["id"], entity.get("schedule", "monthly"))
        launched.append({"entity_id": entity["id"], "entity_name": entity["entity_name"], "job_id": job_id})
    return {"launched": launched}


# ── Practice Composite discovery endpoint ────────────────────────────────────

def _graph_write(fn, *a, **kw):
    """Entity-graph writes never block a request or a run."""
    try:
        return fn(*a, **kw)
    except Exception as exc:
        print(f"[graph] {getattr(fn, '__name__', 'write')} failed: {type(exc).__name__}: {exc}", flush=True)
        return None


@app.get("/api/org/roster")
async def org_roster(name: str, city: str = "", state: str = "", payload: dict = Depends(get_current_user_payload)):
    """The organization's stored, confirmed roster (entity graph) — served before any discovery."""
    from perception.graph import get_org
    org = await asyncio.get_running_loop().run_in_executor(None, lambda: get_org(_normalize_input(name), _normalize_input(city), (state or "").upper().strip()))
    if not org or not org.get("locations"):
        return {"found": False}
    return {"found": True, **org}


class PracticeDiscoverRequest(BaseModel):
    entity_name: str
    city: str
    state: str
    service_line: Optional[str] = None   # set → scope sibling discovery to this service line
    parent_system: Optional[str] = None  # the larger hospital/health system that operates it


@app.post("/api/practice/detect-service-line")
async def practice_detect_service_line(
    req: PracticeDiscoverRequest,
    _: str = Depends(require_auth),
):
    """Detect whether a selected listing is a specialty department / service line
    of a larger hospital or academic health system."""
    try:
        from perception.db import init_db
        from perception.practice_discovery import detect_service_line
        init_db()
        city  = _normalize_input(req.city)
        state = req.state.strip().upper()
        return detect_service_line(req.entity_name, city, state)
    except Exception as exc:
        raise HTTPException(500, f"Service-line detection error: {exc}")


@app.post("/api/practice/discover")
async def practice_discover(
    req: PracticeDiscoverRequest,
    _: str = Depends(require_auth),
):
    """Discover practices associated with a hospital or specialty practice."""
    try:
        from perception.db import init_db
        from perception.practice_discovery import discover_practices
        init_db()
        city  = _normalize_input(req.city)
        state = req.state.strip().upper()
        practices = discover_practices(req.entity_name, city, state)
        return {"practices": practices, "count": len(practices)}
    except Exception as exc:
        raise HTTPException(500, f"Practice discovery error: {exc}")


class FindMoreRequest(BaseModel):
    brand: str
    city: str
    state: str
    exclude_place_ids: List[str] = []


@app.post("/api/practice/find-more")
async def practice_find_more(req: FindMoreRequest, _: str = Depends(require_auth)):
    """Widen the Google search for a multi-office practice: several query variants
    (brand alone, brand + state, brand near the market city) merged by place_id and
    kept only when the listing name matches the brand."""
    from perception.data.places import search_entity_candidates, _tokens
    brand = _normalize_input(req.brand)
    btoks = _tokens(brand)
    # Acronym brands: "Illinois Bone and Joint Institute" lists many offices as "IBJI Doctors' Office - …"
    _stop = {"the", "of", "and", "at", "for", "&"}
    _words = [w for w in re.findall(r"[A-Za-z]+", brand) if w.lower() not in _stop]
    acronym = "".join(w[0] for w in _words).lower() if len(_words) >= 3 else ""
    seen = set(req.exclude_place_ids or [])
    out = []
    def _city_of(addr: str) -> str:
        parts = [p.strip() for p in str(addr or "").split(",")]
        return parts[-3] if len(parts) >= 3 else ""

    def _run(name, city, state):
        try:
            return search_entity_candidates(name, city, state, max_results=20) or []
        except TypeError:
            return search_entity_candidates(name, city, state) or []
        except Exception:
            return []

    def _take(cands):
        added = 0
        for c in cands:
            pid = c.get("place_id")
            if not pid or pid in seen:
                continue
            cname = (c.get("name") or "").lower()
            ctoks = _tokens(cname)
            by_tokens = bool(btoks) and len(btoks & ctoks) / len(btoks) >= 0.5
            by_acronym = bool(acronym) and re.search(r"\b" + re.escape(acronym) + r"\b", cname) is not None
            if not (by_tokens or by_acronym):
                continue
            seen.add(pid)
            out.append(c)
            added += 1
        return added

    # Round 1: brand-level variants around the market
    variants = [(brand, None, req.state), (brand, req.city, req.state),
                (f"{brand} clinic", None, req.state), (f"{brand} near {req.city}", None, req.state)]
    if acronym:
        variants += [(acronym.upper(), None, req.state), (acronym.upper(), req.city, req.state),
                     (f"{acronym.upper()} doctors office", None, req.state)]
    for name, city, state in variants:
        _take(_run(name, city, state))
    # Round 2+: snowball — every city seen in a found address becomes its own query, so a
    # suburban multi-office group is built up from its own footprint (capped).
    queried = {str(req.city).strip().lower()}
    max_queries, n = 14, 0
    frontier = [c for c in out]
    while frontier and n < max_queries:
        nxt = []
        for c in frontier:
            city = _city_of(c.get("address"))
            key = city.lower()
            if not city or key in queried:
                continue
            queried.add(key)
            n += 1
            if n > max_queries:
                break
            before = len(out)
            _take(_run(brand, city, req.state))
            if acronym:
                _take(_run(acronym.upper(), city, req.state))
            nxt.extend(out[before:])
        frontier = nxt
    return {"candidates": out, "queries": n + len(variants)}


@app.post("/api/practice/siblings")
async def practice_siblings(
    req: PracticeDiscoverRequest,
    _: str = Depends(require_auth),
):
    """Discover sibling locations for the initiation-screen roster step. When
    service_line + parent_system are provided, scope discovery to that service
    line only (e.g. Duke Health's orthopedic clinics)."""
    try:
        from perception.db import init_db
        init_db()
        city  = _normalize_input(req.city)
        state = req.state.strip().upper()
        if req.service_line and req.parent_system:
            from perception.practice_discovery import discover_service_line_siblings
            siblings, brand = discover_service_line_siblings(
                req.entity_name, req.parent_system, req.service_line, city, state)
            return {"siblings": siblings, "parent_org_name": brand, "count": len(siblings)}
        from perception.practice_discovery import discover_practice_siblings
        siblings, parent_org_name = discover_practice_siblings(req.entity_name, city, state)
        return {"siblings": siblings, "parent_org_name": parent_org_name, "count": len(siblings)}
    except Exception as exc:
        raise HTTPException(500, f"Sibling discovery error: {exc}")


class PhysicianDiscoverRequest(BaseModel):
    entity_name: str
    city: str
    state: str


@app.post("/api/physician/discover")
async def physician_discover(
    req: PhysicianDiscoverRequest,
    _: str = Depends(require_auth),
):
    """Discover physicians at the organization level (single call for the entire entity)."""
    try:
        from perception.db import init_db
        from perception.physician_discovery import discover_physicians
        init_db()
        name  = req.entity_name.strip()
        city  = _normalize_input(req.city)
        state = req.state.strip().upper()
        physicians = discover_physicians(name, city, state)
        return {"physicians": {name: physicians}}
    except Exception as exc:
        raise HTTPException(500, f"Physician discovery error: {exc}")


# ── Frontend (catch-all — must be last) ───────────────────────────────────────
# ── Events Pulse ──────────────────────────────────────────────────────────────

class EventRunRequest(BaseModel):
    event_name: str
    event_date: Optional[str] = None
    entity_type: str                       # "hospital" | "practice" | "fqhc"
    csv_filename: Optional[str] = None
    include_teaser: bool = False
    override_cache: bool = False           # bypass same-day lock + 90-day score cache
    auto_practice_composite: bool = False  # FQHC only: discover all sites & build aggregate
    practice_content: bool = False         # Practice only: content analysis + prescription (combined report)
    entities: List[dict]                   # confirmed list: {input_name,input_city,input_state,resolved_name,resolved_addr}


_event_job_map: dict[str, str] = {}   # event_id -> job_id


@app.post("/api/event/upload")
async def event_upload(file: UploadFile = File(...), _: str = Depends(require_auth)):
    """Parse an event CSV and resolve each row via Google Places (batches of 5)."""
    import csv
    import io as _io

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(_io.StringIO(text))
    rows = []
    for i, row in enumerate(reader, start=1):
        name     = (row.get("name") or row.get("Name") or "").strip()
        city     = (row.get("city") or row.get("City") or "").strip()
        state    = (row.get("state") or row.get("State") or "").strip().upper()
        url      = (row.get("url") or row.get("URL") or row.get("Url") or "").strip()
        customer = (row.get("customer") or row.get("Customer") or "").strip()
        specialty = (row.get("specialty") or row.get("Specialty")
                     or row.get("service_line") or row.get("Service Line") or "").strip()
        if not name:
            continue
        rows.append({"row_num": i, "input_name": name, "input_city": city, "input_state": state,
                     "input_url": url, "input_customer": customer, "input_specialty": specialty})

    if not rows:
        raise HTTPException(400, "No valid rows found. Ensure the CSV has name, city, state columns.")

    from perception.data.places import search_entity_candidates
    loop = asyncio.get_running_loop()

    async def resolve_row(row: dict) -> dict:
        candidates = await loop.run_in_executor(
            None,
            lambda: search_entity_candidates(row["input_name"], row["input_city"], row["input_state"])
        )
        n = len(candidates)
        status = "resolved" if n == 1 else ("ambiguous" if n > 1 else "not_found")
        return {**row, "candidates": candidates, "status": status}

    results = []
    for i in range(0, len(rows), 5):
        batch = rows[i:i+5]
        resolved = await asyncio.gather(*[resolve_row(r) for r in batch])
        results.extend(resolved)

    return {"rows": results, "original_filename": file.filename or "upload.csv"}


@app.post("/api/event/run")
async def event_run(req: EventRunRequest, payload: dict = Depends(get_current_user_payload)):
    """Create an event run record and kick off batch analysis."""
    from perception.db import init_db, create_event_run, create_event_entities
    role  = payload["role"]
    brand = payload.get("brand", "original")

    init_db()
    event_id = str(uuid.uuid4())
    entities_db = [
        {
            "id":           str(uuid.uuid4()),
            "event_id":     event_id,
            "row_num":      i,
            "input_name":     _normalize_input(e.get("input_name", "")),
            "input_city":     _normalize_input(e.get("input_city", "")),
            "input_state":    (e.get("input_state") or "").strip().upper(),
            "input_url":      (e.get("input_url") or "").strip(),
            "input_customer": (e.get("input_customer") or "").strip(),
            "input_specialty": (e.get("input_specialty") or "").strip(),
            "resolved_name":  _normalize_input(e.get("resolved_name") or e.get("input_name", "")),
            "resolved_addr":  e.get("resolved_addr", ""),
        }
        for i, e in enumerate(req.entities, start=1)
    ]

    create_event_run(
        event_id=event_id,
        event_name=req.event_name.strip(),
        event_date=req.event_date,
        entity_type=req.entity_type,
        csv_filename=req.csv_filename,
        total_count=len(entities_db),
        role=role,
        include_teaser=req.include_teaser,
        override_cache=req.override_cache,
        auto_practice_composite=req.auto_practice_composite,
        practice_content=req.practice_content,
    )
    create_event_entities(entities_db)

    job_id = _new_job(role, brand, payload.get("email"))
    _event_job_map[event_id] = job_id
    _pool.submit(_run_event_job, job_id, event_id, entities_db, req.entity_type, req.include_teaser,
                 req.override_cache, req.auto_practice_composite, req.practice_content)
    return {"event_id": event_id, "job_id": job_id}


@app.post("/api/event/{event_id}/resume")
async def event_resume(event_id: str, payload: dict = Depends(get_current_user_payload)):
    """Re-run only the entities that never reached 'done' (failed, skipped, or left
    pending when an instance was recycled mid-run). Entities already scored keep
    their result; the CSV/ZIP are rebuilt from the full checkpoint at the end."""
    from perception.db import init_db, get_event_run, get_event_entities
    init_db()
    run = get_event_run(event_id)
    if not run:
        raise HTTPException(404, "Event not found")
    ents = get_event_entities(event_id)
    pending = [e for e in ents if (e.get("status") or "") != "done"]
    if not pending:
        raise HTTPException(400, "All entities already completed — nothing to resume.")
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"), payload.get("email"))
    _event_job_map[event_id] = job_id
    # Repeat the original run's settings so resumed entities are analyzed the same way.
    _pool.submit(_run_event_job, job_id, event_id, pending,
                 run.get("entity_type", "hospital"),
                 bool(run.get("include_teaser")),
                 bool(run.get("override_cache")),
                 bool(run.get("auto_practice_composite")),
                 bool(run.get("practice_content")))
    return {"event_id": event_id, "job_id": job_id, "pending": len(pending)}


def _run_event_job(
    job_id: str, event_id: str, entities: list, entity_type: str,
    include_teaser: bool = False,
    override_cache: bool = False,
    auto_practice_composite: bool = False,
    practice_content: bool = False,
) -> None:
    """Background: analyze all entities in the event, 5 at a time."""
    import re as _re
    import threading
    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
    from datetime import datetime as _dt

    job   = _jobs[job_id]
    loop  = job["loop"]
    queue = job["queue"]
    emit  = lambda e: _put(loop, queue, e)
    brand = job.get("brand", "original")
    role  = job["role"]

    try:
        from perception.db import (
            init_db, set_run_role, update_event_entity, increment_event_progress,
            finalize_event_run, get_event_run, get_event_entities, get_connection,
        )
        from perception.scoring import grade_from_score
        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        # Dedicated subfolder for this event — all files (PDFs, CSV, ZIP) go here
        event_dir = REPORTS_DIR / "events" / event_id
        event_dir.mkdir(parents=True, exist_ok=True)

        import time as _time

        semaphore = threading.Semaphore(5)

        def _analyze_with_retry(fn, kwargs, name, max_attempts=3, base_wait=1):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return fn(**kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        wait = base_wait * (2 ** attempt)
                        emit({"type": "log", "text":
                              f"↻ {name}: attempt {attempt + 1} failed — retrying in {wait}s…"})
                        _time.sleep(wait)
            raise last_exc

        def _run_one(entity: dict, pass_num: int = 1) -> bool:
            """Returns True on success, False on failure."""
            entity_id     = entity["id"]
            resolved_name = entity["resolved_name"]
            city          = entity["input_city"]
            state         = entity["input_state"]
            is_retry      = pass_num > 1
            # Longer per-entity backoff on retry passes (5s, 10s vs 1s, 2s)
            base_wait     = 5 if is_retry else 1

            with semaphore:
                emit({"type": "entity_start", "entity_id": entity_id,
                      "name": resolved_name, "retry": is_retry})
                try:
                    if entity_type == "fqhc":
                        from perception.fqhc_analyzer import analyze_fqhc
                        result = _analyze_with_retry(analyze_fqhc, dict(
                            entity_name=resolved_name,
                            city=city, state=state,
                            aggregate=auto_practice_composite,
                            output_dir=event_dir,
                            on_event=lambda _e: None,
                            brand=brand,
                            force_rerun=override_cache,
                            override_today_lock=override_cache,
                        ), resolved_name, base_wait=base_wait)
                    elif entity_type == "practice":
                        from perception.practice_analyzer import analyze_practice
                        # Auto-detect (no confirm — batch) whether this attendee is a
                        # hospital service line; if so, aggregate that service line.
                        _sl = {}
                        try:
                            from perception.practice_discovery import detect_service_line
                            _sl = detect_service_line(
                                resolved_name, city, state,
                                specialty_hint=(entity.get("input_specialty") or ""))
                        except Exception:
                            _sl = {}
                        _pkwargs = dict(
                            entity_name=resolved_name,
                            city=city, state=state,
                            aggregate=True,
                            output_dir=event_dir,
                            on_event=lambda _e: None,
                            brand=brand,
                            force_rerun=override_cache,
                            override_today_lock=override_cache,
                        )
                        if _sl.get("is_service_line"):
                            _pkwargs["service_line"]  = _sl["service_line"]
                            _pkwargs["parent_system"] = _sl["parent_system"]
                        else:
                            _pkwargs["confirmed_siblings"] = []   # single-location, unchanged
                        result = _analyze_with_retry(analyze_practice, _pkwargs,
                                                     resolved_name, base_wait=base_wait)
                    else:
                        from perception.analyzer import analyze_location
                        result = _analyze_with_retry(analyze_location, dict(
                            city=city, state=state,
                            entity_name=resolved_name,
                            aggregate=True,
                            individual_report=True,
                            output_dir=event_dir,
                            on_event=lambda _e: None,
                            brand=brand,
                            force_rerun=override_cache,
                            override_today_lock=override_cache,
                        ), resolved_name, base_wait=base_wait)

                    set_run_role(result.run_id, role)

                    # Practice combined report (opt-in per event): content analysis +
                    # drafted prescription + findings-citing Assessment, replacing the
                    # base four-pillar PDF in place (same path → zip picks it up).
                    # Fail-soft: on error the base report stands.
                    combined_ok = False
                    if practice_content and entity_type == "practice":
                        _ca_job = {
                            "teaser_report": include_teaser,
                            "content_urls": [entity.get("input_url")] if (entity.get("input_url") or "").strip() else [],
                        }
                        def _ca_emit(ev, _n=resolved_name):
                            if ev.get("type") == "phase":
                                emit({"type": "log", "text": f"  {_n}: {ev.get('text', ev.get('name', ''))}"})
                        emit({"type": "log", "text": f"Content analysis for {resolved_name} (combined practice report)"})
                        try:
                            _finalize_practice_combined(result, resolved_name, city, state,
                                                        brand, _ca_job, _ca_emit)
                            combined_ok = True
                        except Exception as _ce:
                            emit({"type": "log", "text":
                                  f"⚠ Content analysis failed for {resolved_name} ({type(_ce).__name__}); base report kept"})

                    # Tag the run with this event; make it visible to all users
                    with get_connection() as _con:
                        _con.execute(
                            "UPDATE analysis_runs SET event_id=?, user_role='admin' WHERE run_id=?",
                            [event_id, result.run_id],
                        )

                    # Extract score
                    pulse_score = None
                    letter = "—"
                    band   = "Unscored"
                    if result.rankings:
                        pulse_score = result.rankings[0].ai_visibility_score
                        letter, band = grade_from_score(pulse_score)

                    # Rename PDF: replace "Pulse-Diagnostic" with "EventReport"
                    new_pdf_path = result.pdf_path
                    if result.pdf_path:
                        old = Path(result.pdf_path)
                        new_stem = old.stem.replace("Pulse-Diagnostic", "EventReport")
                        if new_stem == old.stem:
                            new_stem = old.stem + "_EventReport"
                        new_p = old.parent / f"{new_stem}{old.suffix}"
                        try:
                            old.rename(new_p)
                            new_pdf_path = str(new_p)
                            with get_connection() as _con2:
                                _con2.execute(
                                    "UPDATE analysis_runs SET pdf_path=? WHERE run_id=?",
                                    [new_pdf_path, result.run_id],
                                )
                        except Exception:
                            pass

                    # Combined-report teaser (already rendered): rename to the event convention.
                    if combined_ok and getattr(result, "teaser_pdf_path", None):
                        try:
                            _tp = Path(result.teaser_pdf_path)
                            _tn = _tp.parent / f"{Path(new_pdf_path).stem}_Teaser.pdf"
                            _tp.rename(_tn)
                            result.teaser_pdf_path = str(_tn)
                            with get_connection() as _con3:
                                _con3.execute("UPDATE analysis_runs SET teaser_pdf_path=? WHERE run_id=?",
                                              [str(_tn), result.run_id])
                        except Exception:
                            pass

                    # Teaser PDF: re-render the already-collected result with teaser_report=True.
                    # No API calls — just a second Playwright PDF render from the same data.
                    # (Skipped when the combined practice report already produced its own teaser.)
                    if include_teaser and not (combined_ok and getattr(result, "teaser_pdf_path", None)):
                        try:
                            import copy as _copy
                            t_result = _copy.copy(result)
                            t_result.teaser_report  = True
                            t_result.individual_report = True
                            base_stem = Path(new_pdf_path).stem if new_pdf_path else resolved_name
                            t_pdf_path = event_dir / f"{base_stem}_Teaser.pdf"
                            if result.entity_type == "community_health":
                                from perception.fqhc_pdf import render_fqhc_pdf as _render_teaser
                                _render_teaser(t_result, str(t_pdf_path), brand=brand)
                            else:
                                from perception.pdf import render_pdf as _render_teaser
                                _render_teaser(t_result, t_pdf_path, brand=brand)
                        except Exception as _te:
                            emit({"type": "log", "text":
                                  f"⚠ Teaser PDF failed for {resolved_name}: {_te}"})

                    update_event_entity(entity_id, result.run_id, pulse_score, letter, band, "done")
                    if is_retry:
                        # Flip the previously-counted skip to a done
                        increment_event_progress(event_id, done=1, skipped=-1)
                    else:
                        increment_event_progress(event_id, done=1)
                    emit({
                        "type": "entity_done", "entity_id": entity_id,
                        "name": resolved_name, "score": pulse_score,
                        "grade": letter, "band": band, "run_id": result.run_id,
                        "retry": is_retry,
                    })
                    return True

                except Exception as exc:
                    err = str(exc)[:200]
                    update_event_entity(entity_id, None, None, "—", "Unscored", "skipped", err)
                    if not is_retry:
                        # Only count as skipped on the first pass; retry passes don't double-count
                        increment_event_progress(event_id, skipped=1)
                    emit({"type": "entity_skip", "entity_id": entity_id,
                          "name": resolved_name, "error": err, "retry": is_retry})
                    return False

        def _run_pass(pending: list, pass_num: int) -> list:
            """Run one wave; return entities that still failed."""
            still_failed = []
            with _TPE(max_workers=10) as pool:
                futures = {pool.submit(_run_one, e, pass_num): e for e in pending}
                for fut in _ac(futures):
                    e = futures[fut]
                    try:
                        if not fut.result():
                            still_failed.append(e)
                    except Exception:
                        still_failed.append(e)
            return still_failed

        # ── Pass 1 ────────────────────────────────────────────────────────────
        skipped = _run_pass(entities, pass_num=1)

        # ── Pass 2 (30 s later) ───────────────────────────────────────────────
        if skipped:
            emit({"type": "log", "text":
                  f"⟳ Pass 2 — {len(skipped)} entit{'y' if len(skipped)==1 else 'ies'} skipped, "
                  f"retrying in 30 s…"})
            _time.sleep(30)
            emit({"type": "log", "text": "⟳ Pass 2 starting…"})
            skipped = _run_pass(skipped, pass_num=2)

        # ── Pass 3 (another 30 s later) ───────────────────────────────────────
        if skipped:
            emit({"type": "log", "text":
                  f"⟳ Pass 3 — {len(skipped)} entit{'y' if len(skipped)==1 else 'ies'} still skipped, "
                  f"retrying in 30 s…"})
            _time.sleep(30)
            emit({"type": "log", "text": "⟳ Pass 3 starting…"})
            _run_pass(skipped, pass_num=3)

        # ── Build enriched CSV ────────────────────────────────────────────────
        import csv as _csv
        import io as _io2
        import zipfile as _zf
        ev_entities = get_event_entities(event_id)
        ev          = get_event_run(event_id)

        def _ascii_grade(g: str) -> str:
            return (g or "").replace("−", "-").replace("—", "N/A").replace("–", "-")

        # Letter grade derived from the quartile: Q1→A, Q2→B, Q3→C, Q4→D.
        _Q_TO_LETTER = {"Q1": "A", "Q2": "B", "Q3": "C", "Q4": "D"}
        def _letter_grade(quartile: str) -> str:
            return _Q_TO_LETTER.get((quartile or "").strip().upper(), "")

        out = _io2.StringIO()
        writer = _csv.writer(out)
        writer.writerow(["name", "city", "state", "url", "customer", "pulse_score",
                         "letter_grade", "quartile", "quartile_label", "notes"])
        for e in ev_entities:
            notes = "scored" if e["status"] == "done" else f"skipped - {e['error_msg'] or 'not found'}"
            _quartile = e["letter_grade"]   # DB field 'letter_grade' actually holds the quartile code (Q1–Q4)
            writer.writerow([
                e["input_name"], e["input_city"], e["input_state"], e.get("input_url") or "",
                e.get("input_customer") or "",
                e["pulse_score"] if e["pulse_score"] is not None else "",
                _letter_grade(_quartile),
                _ascii_grade(_quartile), e["band_label"] or "", notes,
            ])

        ts        = _dt.utcnow().strftime("%y%m%d-%H%M")
        safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "-", (ev["event_name"] or "event"))[:40]
        csv_name  = f"{safe_name}_EventReport-{ts}.csv"
        csv_path  = event_dir / csv_name
        csv_path.write_bytes(b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"))

        # ── Build ZIP of all PDFs in the event folder ─────────────────────────
        zip_name = f"{safe_name}_EventReport-{ts}.zip"
        zip_path = event_dir / zip_name
        pdfs = sorted(event_dir.glob("*.pdf"))
        with _zf.ZipFile(zip_path, "w", _zf.ZIP_DEFLATED) as zf:
            for pdf in pdfs:
                zf.write(pdf, pdf.name)
            zf.write(csv_path, csv_name)   # include the CSV in the ZIP too

        finalize_event_run(event_id, str(csv_path), str(zip_path))
        job["status"] = "done"
        job["result"] = {"event_id": event_id, "csv_filename": csv_name, "zip_filename": zip_name}
        _notify_run_complete(job, "Event Preparation batch", job.get("label") or f"Event {event_id[:8]}", [])

    except Exception as exc:
        job["status"] = "error"
        job["error"]  = str(exc)
    finally:
        _finish_cost(job_id)
        _put(loop, queue, None)


@app.get("/api/event/{event_id}/stream")
async def event_stream(event_id: str, _: str = Depends(require_auth)):
    """SSE stream for an event run — delegates to the underlying job queue."""
    job_id = _event_job_map.get(event_id)
    if not job_id or job_id not in _jobs:
        raise HTTPException(404, "Event job not found or already expired")
    queue: asyncio.Queue = _jobs[job_id]["queue"]

    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                yield 'data: {"type":"ping"}\n\n'
                continue
            if event is None:
                job = _jobs[job_id]
                if job["status"] == "done":
                    payload = {"type": "done", **job.get("result", {})}
                else:
                    payload = {"type": "error", "message": job.get("error", "Unknown error")}
                yield f"data: {json.dumps(payload)}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/event/{event_id}/status")
async def event_status(event_id: str, _: str = Depends(require_auth)):
    """Poll-based status for an event run."""
    from perception.db import init_db, get_event_run, get_event_entities
    init_db()
    ev = get_event_run(event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    entities = get_event_entities(event_id)
    return {
        **{k: str(v) if k == "created_at" else v for k, v in ev.items()},
        "entities": entities,
    }


@app.get("/api/event/{event_id}/csv")
async def event_csv_download(event_id: str, _: str = Depends(require_auth)):
    """Download the enriched CSV for a completed event run."""
    from perception.db import init_db, get_event_run
    init_db()
    ev = get_event_run(event_id)
    if not ev or not ev.get("enriched_csv_path"):
        raise HTTPException(404, "Enriched CSV not ready")
    csv_path = Path(ev["enriched_csv_path"])
    if not csv_path.exists():
        raise HTTPException(404, "CSV file not found on disk")
    return FileResponse(str(csv_path), media_type="text/csv", filename=csv_path.name)


@app.get("/api/event/{event_id}/zip")
async def event_zip_download(event_id: str, _: str = Depends(require_auth)):
    """Download a ZIP of all PDFs + CSV for a completed event run."""
    from perception.db import init_db, get_event_run
    init_db()
    ev = get_event_run(event_id)
    if not ev or not ev.get("zip_path"):
        raise HTTPException(404, "ZIP not ready")
    zip_path = Path(ev["zip_path"])
    if not zip_path.exists():
        raise HTTPException(404, "ZIP file not found on disk")

    def _iter_zip():
        with open(str(zip_path), "rb") as fh:
            while chunk := fh.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_path.name}"'},
    )


@app.get("/api/events")
async def list_events(_: str = Depends(require_auth)):
    """List all event runs — visible to every logged-in user."""
    from perception.db import init_db, list_event_runs
    init_db()
    events = list_event_runs("admin")   # always fetch all; visibility is open
    return [
        {
            **{k: str(v) if k == "created_at" else v for k, v in e.items()},
            "has_csv": bool(e.get("enriched_csv_path") and Path(e["enriched_csv_path"]).exists()),
            "has_zip": bool(e.get("zip_path") and Path(e["zip_path"]).exists()),
        }
        for e in events
    ]


# upload_id -> {"tmp_path": str, "fd": int, "filename": str, "file_type": str}
_chunk_sessions: dict[str, dict] = {}


@app.post("/api/event/{event_id}/upload-chunk")
async def event_upload_chunk(
    event_id: str,
    _: dict = Depends(require_admin),
    upload_id: str = Form(...),
    filename: str = Form(...),
    file_type: str = Form(...),
    is_last: str = Form("false"),
    chunk: UploadFile = File(...),
):
    """Chunked admin upload — sends one ≤10 MB slice at a time; assembles in /tmp."""
    import shutil as _shutil
    import os as _os

    chunk_data = await chunk.read()
    done = is_last.lower() == "true"

    if upload_id not in _chunk_sessions:
        suffix = Path(filename).suffix
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        _chunk_sessions[upload_id] = {
            "tmp_path": tmp_path, "fd": fd,
            "filename": filename, "file_type": file_type,
        }

    state = _chunk_sessions[upload_id]
    _os.write(state["fd"], chunk_data)

    if not done:
        return {"ok": True, "done": False}

    _os.close(state["fd"])
    del _chunk_sessions[upload_id]

    from perception.db import init_db, get_event_run, update_event_files
    init_db()
    ev = get_event_run(event_id)
    if not ev:
        _os.unlink(state["tmp_path"])
        raise HTTPException(404, "Event not found")

    event_dir = REPORTS_DIR / "events" / event_id
    event_dir.mkdir(parents=True, exist_ok=True)
    dest = event_dir / state["filename"]
    _shutil.move(state["tmp_path"], str(dest))

    ft = state["file_type"]
    from perception.db import update_event_files as _uef
    _uef(
        event_id,
        enriched_csv_path=str(dest) if ft == "csv" else None,
        zip_path=str(dest) if ft == "zip" else None,
    )
    return {"ok": True, "done": True}


@app.post("/api/event/{event_id}/upload-files")
async def event_upload_files(
    event_id: str,
    _: dict = Depends(require_admin),
    csv: Optional[UploadFile] = File(None),
    zip: Optional[UploadFile] = File(None),
):
    """Admin-only: replace the enriched CSV and/or ZIP for an existing event run."""
    from perception.db import init_db, get_event_run, update_event_files
    init_db()
    ev = get_event_run(event_id)
    if not ev:
        raise HTTPException(404, "Event not found")

    event_dir = REPORTS_DIR / "events" / event_id
    event_dir.mkdir(parents=True, exist_ok=True)

    new_csv_path: Optional[str] = None
    new_zip_path: Optional[str] = None

    if csv is not None:
        csv_dest = event_dir / (csv.filename or f"{event_id}_enriched.csv")
        csv_dest.write_bytes(await csv.read())
        new_csv_path = str(csv_dest)

    if zip is not None:
        zip_dest = event_dir / (zip.filename or f"{event_id}_reports.zip")
        zip_dest.write_bytes(await zip.read())
        new_zip_path = str(zip_dest)

    if new_csv_path is None and new_zip_path is None:
        raise HTTPException(400, "No files provided")

    update_event_files(event_id, enriched_csv_path=new_csv_path, zip_path=new_zip_path)
    return {"ok": True}


class EventRunMetaRequest(BaseModel):
    event_name: Optional[str] = None
    event_date: Optional[str] = None

@app.patch("/api/event/{event_id}/meta")
async def patch_event_meta(event_id: str, req: EventRunMetaRequest, _: dict = Depends(require_admin)):
    """Admin: update event name and/or event date."""
    from perception.db import init_db, get_event_run, update_event_run_meta
    init_db()
    if not get_event_run(event_id):
        raise HTTPException(404, "Event not found")
    if req.event_name is not None and not req.event_name.strip():
        raise HTTPException(400, "Event name cannot be blank")
    update_event_run_meta(
        event_id,
        event_name=req.event_name.strip() if req.event_name else None,
        event_date=req.event_date,
    )
    return Response(status_code=204)


@app.delete("/api/event/{event_id}")
async def delete_event(event_id: str, _: dict = Depends(require_admin)):
    """Delete an event run, its analysis runs, and all files on disk."""
    import shutil
    from perception.db import init_db, delete_event_run
    init_db()
    delete_event_run(event_id)
    event_dir = REPORTS_DIR / "events" / event_id
    if event_dir.exists():
        shutil.rmtree(event_dir, ignore_errors=True)
    return Response(status_code=204)


_EVENTS_DISPLAY_URL = (
    "https://storage.googleapis.com/rank2-public-downloads"
    "/EventsDisplay-1.0.0.1-arm64.dmg"
)

@app.get("/api/downloads/events-display")
async def download_events_display(_: str = Depends(require_auth)):
    # File is served directly from GCS (Cloud Run has a 32 MB response-size limit).
    # The downloads/ prefix in the bucket is made public via an IAM condition.
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=_EVENTS_DISPLAY_URL)


_ASSETS_DIR = Path(__file__).parent / "web" / "assets"

@app.get("/assets/{name}")
async def static_asset(name: str):
    """Serve brand assets (logos etc.) from web/assets — must precede the SPA catch-all."""
    path = _ASSETS_DIR / Path(name).name          # basename only; no traversal
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(str(path), headers={"Cache-Control": "public, max-age=86400"})


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def frontend(full_path: str):
    html_path = Path(__file__).parent / "web" / "index.html"
    try:
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HTMLResponse("<h1>Pulse</h1><p>Frontend not built — web/index.html missing.</p>")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    from perception.config import settings as _settings
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    # Show the database backend so it's obvious this is the Postgres build,
    # not the legacy DuckDB one (creds masked).
    _dburl = _settings.database_url
    if "://" in _dburl and "@" in _dburl:
        _scheme, _rest = _dburl.split("://", 1)
        _dbdisplay = f"{_scheme}://***@{_rest.split('@', 1)[1]}"
    else:
        _dbdisplay = _dburl

    print(f"\n  Rank2  →  http://localhost:{port}")
    print(f"  DB     →  Postgres: {_dbdisplay}\n")
    if not ACCESS_PASSWORDS:
        print("  ⚠  WARNING: ACCESS_PASSWORD not set in .env\n")
    uvicorn.run(app, host=host, port=port)
