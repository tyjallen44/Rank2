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

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
try:
    from spellchecker import SpellChecker
    _spell = SpellChecker()
except Exception:
    _spell = None


def _normalize_input(text: str | None) -> str | None:
    """Title-case and spell-correct a free-text field received in ALL CAPS from the UI."""
    if not text:
        return text
    try:
        words = text.strip().lower().split()
        if _spell:
            words = [(_spell.correction(w) or w) for w in words]
        return " ".join(words).title()
    except Exception:
        return text.strip().title()

app = FastAPI(title="Rank2", docs_url=None, redoc_url=None)

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

def _password_role(pw: str) -> tuple[str, str]:
    return _ROLE_MAP.get(pw, ("admin", "Admin"))

# ── Auth ──────────────────────────────────────────────────────────────────────
_SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))


def _signing_key() -> bytes:
    """Derive a consistent HMAC signing key from the access password."""
    pw = next(iter(ACCESS_PASSWORDS), "rank2")
    return _hmac.new(pw.encode(), b"rank2-session-v1", hashlib.sha256).digest()


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


@app.get("/api/auth/me")
async def me(payload: dict = Depends(get_current_user_payload)):
    role = payload.get("role", "")
    name = payload.get("name") or _ROLE_DISPLAY.get(role, "Admin")
    return {"role": role, "display_name": name, "email": payload.get("email")}


_SERVER_START = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())


@app.get("/api/version")
async def version():
    try:
        build = Path(__file__).parent.joinpath("VERSION").read_text().strip()
    except Exception:
        build = "dev"
    return {"build": build, "deployed": _SERVER_START}


# ── Job management ────────────────────────────────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_pool = ThreadPoolExecutor(max_workers=2)


def _put(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event: Any) -> None:
    asyncio.run_coroutine_threadsafe(queue.put(event), loop)


def _job_run_single(
    job_id: str, city: str, state: str, specialty: Optional[str],
    aggregate: bool = False, radius_miles: Optional[int] = None,
) -> None:
    job = _jobs[job_id]
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
            teaser_report=job.get("teaser_report", False),
            entity_name=job.get("entity_name"),
            individual_report=job.get("individual_report", False),
            output_dir=REPORTS_DIR, on_event=emit,
        )
        set_run_role(result.run_id, job["role"])
        job["status"] = "done"
        job["result"] = {
            "run_id": result.run_id,
            "location": result.location,
            "specialty": result.specialty,
            "provider_count": len(result.rankings),
            "pdf_path": result.pdf_path,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    finally:
        _put(loop, queue, None)  # sentinel → closes SSE stream


def _job_run_batch(job_id: str, groups: List[dict]) -> None:
    job = _jobs[job_id]
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
            )
            set_run_role(result.run_id, job["role"])
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
        job["error"] = str(exc)
    finally:
        _put(loop, queue, None)


def _new_job(role: str) -> str:
    job_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = {"status": "running", "loop": loop, "queue": queue, "role": role}
    return job_id


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
    entity_name: Optional[str] = None
    individual_report: bool = False


class BatchRequest(BaseModel):
    groups: List[AnalyzeRequest]


@app.post("/api/analyze")
async def start_analysis(req: AnalyzeRequest, role: str = Depends(require_auth)):
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

    job_id = _new_job(role)
    _jobs[job_id]["zip_code"] = req.zip_code if req.zip_code else None
    _jobs[job_id]["patient_perspective"] = req.patient_perspective
    _jobs[job_id]["teaser_report"] = req.teaser_report
    _jobs[job_id]["entity_name"] = entity_name
    _jobs[job_id]["individual_report"] = req.individual_report
    _pool.submit(_job_run_single, job_id, city, state, specialty, req.aggregate, radius)
    return {"job_id": job_id}


@app.post("/api/analyze/batch")
async def start_batch(req: BatchRequest, role: str = Depends(require_auth)):
    job_id = _new_job(role)
    _pool.submit(_job_run_batch, job_id, [g.dict() for g in req.groups])
    return {"job_id": job_id}


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


@app.get("/api/history")
async def get_history(role: str = Depends(require_auth)):
    from perception.db import init_db, query_history
    init_db()
    return [
        {**r, "generated_at": str(r["generated_at"]),
         "has_pdf": bool(r.get("pdf_path") and Path(r["pdf_path"]).exists())}
        for r in query_history(role)
    ]


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


class UpdateRoleRequest(BaseModel):
    role: str


class InviteUserRequest(BaseModel):
    email: str
    name: Optional[str] = None
    auth_type: str = "native"
    role: str = "user"


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
            access_token = token_data.get("access_token")
            if not access_token:
                return RedirectResponse(f"{base}/?auth_error=token_failed")

            info_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo = info_resp.json()
    except Exception:
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
    if user:
        if not user["is_active"]:
            return RedirectResponse(f"{base}/?auth_error=deactivated")
        update_last_login(user["id"])
        tok = _create_token(user["role"], uid=user["id"], email=email,
                            name=user.get("name") or name)
        return RedirectResponse(f"{base}/?google_token={tok}")

    req = get_access_request_by_email(email)
    if req and req["status"] == "approved":
        new_user = create_user(email, name, "user", "google")
        update_last_login(new_user["id"])
        tok = _create_token(new_user["role"], uid=new_user["id"], email=email, name=name)
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
    tok = _create_token(user["role"], uid=user["id"], email=user["email"],
                        name=user.get("name") or user["email"])
    return {"token": tok, "role": user["role"],
            "display_name": user.get("name") or user["email"]}


# ── Access request submission ─────────────────────────────────────────────────

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
    new_req = create_access_request(email, req.name, req.request_type)
    try:
        notify_admin_access_request(email, req.name, req.request_type, new_req["id"])
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
    tok = _create_token(user["role"], uid=user["id"], email=user["email"],
                        name=user.get("name") or user["email"])
    return {"token": tok, "role": user["role"],
            "display_name": user.get("name") or user["email"]}


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
    if req["request_type"] == "native":
        if not get_user_by_email(req["email"]):
            user = create_user(req["email"], req["name"], "user", "native")
            tok = create_password_token(user["id"])
            try:
                send_set_password_link(req["email"], req["name"], tok)
            except Exception as _e:
                print(f"[email] approve native error: {_e}")
    else:
        try:
            send_google_access_approved(req["email"], req["name"])
        except Exception as _e:
            print(f"[email] approve google error: {_e}")
    return {"status": "approved"}


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


@app.put("/api/admin/users/{user_id}/role")
async def admin_update_role(
    user_id: str, req: UpdateRoleRequest, _: dict = Depends(require_admin)
):
    from perception.db import init_db
    from perception.auth import update_user_role
    init_db()
    update_user_role(user_id, req.role)
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
    user = create_user(email, req.name, req.role, req.auth_type, invited_by=by)
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


# ── Frontend (catch-all — must be last) ───────────────────────────────────────
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def frontend(full_path: str):
    html_path = Path(__file__).parent / "web" / "index.html"
    try:
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HTMLResponse("<h1>Rank2</h1><p>Frontend not built — web/index.html missing.</p>")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"\n  Rank2  →  http://localhost:{port}\n")
    if not ACCESS_PASSWORDS:
        print("  ⚠  WARNING: ACCESS_PASSWORD not set in .env\n")
    uvicorn.run(app, host=host, port=port)
