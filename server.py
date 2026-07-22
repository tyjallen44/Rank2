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
    """Title-case a free-text field received in ALL CAPS from the UI."""
    if not text:
        return text
    return _smart_title(text.strip())

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


_APP_VERSION = "1.5"
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


def _job_run_single(
    job_id: str, city: str, state: str, specialty: Optional[str],
    aggregate: bool = False, radius_miles: Optional[int] = None,
    entity_type: Optional[str] = None,
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
        )
        set_run_role(result.run_id, job["role"])
        job["status"] = "done"
        job["result"] = {
            "run_id": result.run_id,
            "location": result.location,
            "specialty": result.specialty,
            "provider_count": len(result.rankings),
            "pdf_path": result.pdf_path,
            "briefing_pdf_path": result.briefing_pdf_path,
            "briefing_skipped_reason": result.briefing_skipped_reason,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _put(loop, queue, None)  # sentinel → closes SSE stream


def _job_run_practice(
    job_id: str, entity_name: str, city: str, state: str,
    specialty: Optional[str] = None, aggregate: bool = False,
    radius_miles: Optional[int] = None,
) -> None:
    job = _jobs[job_id]
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, set_run_role
        from perception.practice_analyzer import analyze_practice

        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        result = analyze_practice(
            entity_name=entity_name,
            city=city,
            state=state,
            specialty=specialty,
            aggregate=aggregate,
            practice_profile=job.get("practice_profile"),
            teaser_report=job.get("teaser_report", False),
            output_dir=REPORTS_DIR,
            on_event=emit,
            brand=job.get("brand", "original"),
            skip_pdf=job.get("skip_pdf", False),
            practice_composite=job.get("practice_composite", False),
            practice_roster=job.get("practice_roster") or [],
            physician_composite=job.get("physician_composite", False),
            physician_roster=job.get("physician_roster") or {},
            force_rerun=job.get("force_rerun", False),
            override_today_lock=job.get("override_today_lock", False),
            briefing_variant=job.get("briefing_variant"),
            report_title=job.get("report_title"),
            confirmed_siblings=job.get("confirmed_siblings"),
            org_name=job.get("org_name"),
        )
        set_run_role(result.run_id, job["role"])
        job["status"] = "done"
        job["result"] = {
            "run_id": result.run_id,
            "location": result.location,
            "specialty": result.specialty,
            "provider_count": len(result.rankings),
            "pdf_path": result.pdf_path,
            "briefing_pdf_path": result.briefing_pdf_path,
            "briefing_skipped_reason": result.briefing_skipped_reason,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _put(loop, queue, None)


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
                brand=job.get("brand", "original"),
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
        job["error"] = _job_error(exc)
    finally:
        _put(loop, queue, None)


def _new_job(role: str, brand: str = "original") -> str:
    job_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = {"status": "running", "loop": loop, "queue": queue, "role": role, "brand": brand}
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
    report_title: Optional[str] = None          # display override for PDF title
    org_name: Optional[str] = None              # parent org brand name; drives prompt subject in org mode
    confirmed_siblings: Optional[List[dict]] = None  # pre-confirmed location list from initiation flow; None = run discovery inside analyzer
    individual_report: bool = False
    skip_pdf: bool = False
    entity_type: Optional[str] = None       # "practice" routes to practice_analyzer
    practice_profile: Optional[str] = None  # override auto-classified profile
    practice_composite: bool = False        # append practice reputation table
    practice_roster: List[dict] = []        # confirmed practice list for reputation collection
    physician_composite: bool = False       # include physician sub-rows in practice composite
    physician_roster: dict = {}             # {practice_name: [{name, npi, specialty, credential}]}
    force_rerun: bool = False               # bypass 90-day score cache
    override_today_lock: bool = False       # admin only: bypass same-day cache lock and regenerate
    briefing_variant: Optional[str] = None  # "sales" | "cs" | None — generates Pulse Briefing companion


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
    force_rerun_a: bool = False
    force_rerun_b: bool = False
    override_today_lock: bool = False       # admin only: bypass same-day cache lock and regenerate


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

    job_id = _new_job(role, brand)
    _jobs[job_id]["zip_code"] = req.zip_code if req.zip_code else None
    _jobs[job_id]["patient_perspective"] = req.patient_perspective
    _jobs[job_id]["teaser_report"] = req.teaser_report
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

    if req.entity_type == "practice" and entity_name:
        _pool.submit(_job_run_practice, job_id, entity_name, city, state, specialty, req.aggregate, radius)
    else:
        _pool.submit(_job_run_single, job_id, city, state, specialty, req.aggregate, radius, req.entity_type)
    return {"job_id": job_id}


@app.post("/api/analyze/batch")
async def start_batch(req: BatchRequest, payload: dict = Depends(get_current_user_payload)):
    role  = payload["role"]
    brand = payload.get("brand", "original")
    job_id = _new_job(role, brand)
    _pool.submit(_job_run_batch, job_id, [g.dict() for g in req.groups])
    return {"job_id": job_id}


def _job_run_comparison(job_id: str, req_dict: dict) -> None:
    job = _jobs[job_id]
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
            force_rerun_a=req_dict.get("force_rerun_a", False),
            force_rerun_b=req_dict.get("force_rerun_b", False),
            override_today_lock=req_dict.get("override_today_lock", False),
        )
        job["status"] = "done"
        job["result"] = {
            "run_id": job_id,          # use job_id so download URL is /api/compare/{job_id}/pdf
            "location": f"{result_a.entity_name} vs {result_b.entity_name}",
            "specialty": result_a.specialty,
            "provider_count": 2,
            "pdf_path": pdf_path,
            "comparison": True,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _put(loop, queue, None)


@app.post("/api/compare")
async def start_comparison(req: CompareRequest, payload: dict = Depends(get_current_user_payload)):
    brand = payload.get("brand", "original")
    role  = payload.get("role", "user")
    job_id = _new_job(role, brand)
    req_dict = req.dict()
    # Gate override_today_lock to admin users only
    req_dict["override_today_lock"] = req.override_today_lock and (role == "admin")
    _pool.submit(_job_run_comparison, job_id, req_dict)
    return {"job_id": job_id}


@app.get("/api/compare/{job_id}/pdf")
async def download_comparison_pdf(job_id: str, _: str = Depends(require_auth)):
    job = _jobs.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "Comparison report not found")
    pdf_path = job.get("result", {}).get("pdf_path")
    if not pdf_path:
        raise HTTPException(404, "PDF not available")
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


@app.get("/api/history")
async def get_history(role: str = Depends(require_auth)):
    from perception.db import init_db, query_history
    init_db()
    return [
        {**r, "generated_at": str(r["generated_at"]),
         "has_pdf": bool(r.get("pdf_path") and Path(r["pdf_path"]).exists()),
         "has_briefing_pdf": bool(r.get("briefing_pdf_path") and Path(r["briefing_pdf_path"]).exists())}
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


# ── Feedback ──────────────────────────────────────────────────────────────────
class FeedbackSubmitRequest(BaseModel):
    title: str
    type: str
    body: str

class FeedbackActionRequest(BaseModel):
    action: str

class FeedbackEditRequest(BaseModel):
    title:  Optional[str] = None
    type:   Optional[str] = None
    body:   Optional[str] = None
    action: Optional[str] = None
    notes:  Optional[str] = None

@app.post("/api/feedback")
async def submit_feedback(req: FeedbackSubmitRequest, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, create_feedback
    if req.type not in ("bug", "feature"):
        raise HTTPException(400, "type must be 'bug' or 'feature'")
    init_db()
    return create_feedback(req.title.strip(), req.type, req.body.strip(), payload.get("email", "unknown"))

@app.get("/api/feedback")
async def get_feedback(_: str = Depends(require_auth)):
    from perception.db import init_db, list_feedback
    init_db()
    return list_feedback()

@app.patch("/api/feedback/{feedback_id}")
async def patch_feedback(feedback_id: str, req: FeedbackEditRequest, _: dict = Depends(require_admin)):
    from perception.db import init_db, update_feedback
    valid_actions = {"pending", "accepted", "fixed", "completed", "rejected"}
    if req.action is not None and req.action not in valid_actions:
        raise HTTPException(400, "invalid action")
    if req.type is not None and req.type not in ("bug", "feature"):
        raise HTTPException(400, "type must be 'bug' or 'feature'")
    init_db()
    updates = {k: v for k, v in req.dict().items() if v is not None}
    update_feedback(feedback_id, **updates)
    return {"ok": True}


# ── Tracked Entities ──────────────────────────────────────────────────────────

class TrackEntityRequest(BaseModel):
    entity_name: str
    city: str
    state: str
    specialty: Optional[str] = None
    aggregate: bool = True
    schedule: str = "monthly"   # "monthly" | "weekly" | "manual"
    notes: str = ""

class TrackEntityUpdate(BaseModel):
    active: Optional[bool] = None
    schedule: Optional[str] = None
    notes: Optional[str] = None
    aggregate: Optional[bool] = None


@app.get("/api/track/entities")
async def track_list(_: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, list_tracked_entities
    init_db()
    entities = list_tracked_entities()
    for e in entities:
        for k in ("last_run_at", "next_run_at", "created_at"):
            if e.get(k):
                e[k] = str(e[k])
    return entities


@app.post("/api/track/entities")
async def track_create(req: TrackEntityRequest, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, create_tracked_entity, mark_tracked_entity_ran
    if req.schedule not in ("monthly", "weekly", "manual"):
        raise HTTPException(400, "schedule must be monthly, weekly, or manual")
    init_db()
    created_by = payload.get("email") or payload.get("uid") or "admin"
    entity = create_tracked_entity(
        entity_name=_normalize_input(req.entity_name),
        city=_normalize_input(req.city),
        state=req.state.upper().strip(),
        specialty=_normalize_input(req.specialty) if req.specialty else None,
        aggregate=req.aggregate,
        schedule=req.schedule,
        created_by=created_by,
        notes=req.notes,
    )
    # Fire initial collection run immediately so the first data point is captured now.
    brand = payload.get("brand", "original")
    job_id = _new_job("admin", brand)
    _jobs[job_id]["entity_name"]        = entity["entity_name"]
    _jobs[job_id]["individual_report"]  = True
    _jobs[job_id]["skip_pdf"]           = True
    _jobs[job_id]["patient_perspective"] = False
    _jobs[job_id]["teaser_report"]      = False
    _jobs[job_id]["zip_code"]           = None
    _pool.submit(
        _job_run_single, job_id,
        entity["city"], entity["state"], entity.get("specialty"),
        entity.get("aggregate", True), None,
    )
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
    update_tracked_entity(entity_id, **updates)
    entity = get_tracked_entity(entity_id)
    for k in ("last_run_at", "next_run_at", "created_at"):
        if entity and entity.get(k):
            entity[k] = str(entity[k])
    return entity


@app.delete("/api/track/entities/{entity_id}")
async def track_delete(entity_id: str, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, update_tracked_entity
    init_db()
    update_tracked_entity(entity_id, active=False)
    return {"ok": True}


@app.get("/api/track/entities/{entity_id}/trend")
async def track_trend(entity_id: str, _: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_tracked_entity, get_entity_trend
    init_db()
    entity = get_tracked_entity(entity_id)
    if not entity:
        raise HTTPException(404, "tracked entity not found")
    data = get_entity_trend(entity["entity_name"])
    return {"entity": entity, "data_points": data}


@app.post("/api/track/entities/{entity_id}/run")
async def track_run_now(entity_id: str, payload: dict = Depends(get_current_user_payload)):
    from perception.db import init_db, get_tracked_entity, mark_tracked_entity_ran
    init_db()
    entity = get_tracked_entity(entity_id)
    if not entity:
        raise HTTPException(404, "tracked entity not found")

    brand = payload.get("brand", "original")
    job_id = _new_job("admin", brand)
    _jobs[job_id]["entity_name"]      = entity["entity_name"]
    _jobs[job_id]["individual_report"] = True
    _jobs[job_id]["skip_pdf"]          = True
    _jobs[job_id]["patient_perspective"] = False
    _jobs[job_id]["teaser_report"]     = False
    _jobs[job_id]["zip_code"]          = None
    _pool.submit(
        _job_run_single, job_id,
        entity["city"], entity["state"], entity.get("specialty"),
        entity.get("aggregate", True), None,
    )
    mark_tracked_entity_ran(entity_id, entity.get("schedule", "monthly"))
    return {"job_id": job_id}


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
        job_id = _new_job("admin", "original")
        _jobs[job_id]["entity_name"]       = entity["entity_name"]
        _jobs[job_id]["individual_report"]  = True
        _jobs[job_id]["skip_pdf"]           = True
        _jobs[job_id]["patient_perspective"] = False
        _jobs[job_id]["teaser_report"]      = False
        _jobs[job_id]["zip_code"]           = None
        _pool.submit(
            _job_run_single, job_id,
            entity["city"], entity["state"], entity.get("specialty"),
            entity.get("aggregate", True), None,
        )
        mark_tracked_entity_ran(entity["id"], entity.get("schedule", "monthly"))
        launched.append({"entity_id": entity["id"], "entity_name": entity["entity_name"], "job_id": job_id})
    return {"launched": launched}


# ── Practice Composite discovery endpoint ────────────────────────────────────

class PracticeDiscoverRequest(BaseModel):
    entity_name: str
    city: str
    state: str


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


@app.post("/api/practice/siblings")
async def practice_siblings(
    req: PracticeDiscoverRequest,
    _: str = Depends(require_auth),
):
    """Discover sibling locations of a specialty practice for the initiation-screen roster step."""
    try:
        from perception.db import init_db
        from perception.practice_discovery import discover_practice_siblings
        init_db()
        city  = _normalize_input(req.city)
        state = req.state.strip().upper()
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
    entity_type: str                   # "hospital" or "practice"
    csv_filename: Optional[str] = None
    include_teaser: bool = False
    override_cache: bool = False       # bypass same-day lock + 90-day score cache
    entities: List[dict]               # confirmed list: {input_name,input_city,input_state,resolved_name,resolved_addr}


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
        name  = (row.get("name") or row.get("Name") or "").strip()
        city  = (row.get("city") or row.get("City") or "").strip()
        state = (row.get("state") or row.get("State") or "").strip().upper()
        url   = (row.get("url") or row.get("URL") or row.get("Url") or "").strip()
        if not name:
            continue
        rows.append({"row_num": i, "input_name": name, "input_city": city, "input_state": state, "input_url": url})

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
            "input_name":   _normalize_input(e.get("input_name", "")),
            "input_city":   _normalize_input(e.get("input_city", "")),
            "input_state":  (e.get("input_state") or "").strip().upper(),
            "input_url":    (e.get("input_url") or "").strip(),
            "resolved_name": _normalize_input(e.get("resolved_name") or e.get("input_name", "")),
            "resolved_addr": e.get("resolved_addr", ""),
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
    )
    create_event_entities(entities_db)

    job_id = _new_job(role, brand)
    _event_job_map[event_id] = job_id
    _pool.submit(_run_event_job, job_id, event_id, entities_db, req.entity_type, req.include_teaser, req.override_cache)
    return {"event_id": event_id, "job_id": job_id}


def _run_event_job(
    job_id: str, event_id: str, entities: list, entity_type: str,
    include_teaser: bool = False,
    override_cache: bool = False,
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
                    if entity_type == "practice":
                        from perception.practice_analyzer import analyze_practice
                        result = _analyze_with_retry(analyze_practice, dict(
                            entity_name=resolved_name,
                            city=city, state=state,
                            aggregate=True,
                            confirmed_siblings=[],
                            output_dir=event_dir,
                            on_event=lambda _e: None,
                            brand=brand,
                            force_rerun=override_cache,
                            override_today_lock=override_cache,
                        ), resolved_name, base_wait=base_wait)
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

                    # Tag the run with this event; make it visible to all users
                    _con = get_connection()
                    _con.execute(
                        "UPDATE analysis_runs SET event_id=?, user_role='admin' WHERE run_id=?",
                        [event_id, result.run_id],
                    )
                    _con.close()

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
                            _con2 = get_connection()
                            _con2.execute(
                                "UPDATE analysis_runs SET pdf_path=? WHERE run_id=?",
                                [new_pdf_path, result.run_id],
                            )
                            _con2.close()
                        except Exception:
                            pass

                    # Teaser PDF: re-render the already-collected result with teaser_report=True.
                    # No API calls — just a second Playwright PDF render from the same data.
                    if include_teaser:
                        try:
                            import copy as _copy
                            from perception.pdf import render_pdf as _render_pdf
                            t_result = _copy.copy(result)
                            t_result.teaser_report  = True
                            t_result.individual_report = True
                            base_stem = Path(new_pdf_path).stem if new_pdf_path else resolved_name
                            t_pdf_path = event_dir / f"{base_stem}_Teaser.pdf"
                            _render_pdf(t_result, t_pdf_path, brand=brand)
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

        out = _io2.StringIO()
        writer = _csv.writer(out)
        writer.writerow(["name", "city", "state", "url", "pulse_score", "letter_grade",
                         "band_label", "notes"])
        for e in ev_entities:
            notes = "scored" if e["status"] == "done" else f"skipped - {e['error_msg'] or 'not found'}"
            writer.writerow([
                e["input_name"], e["input_city"], e["input_state"], e.get("input_url") or "",
                e["pulse_score"] if e["pulse_score"] is not None else "",
                _ascii_grade(e["letter_grade"]), e["band_label"] or "", notes,
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

    except Exception as exc:
        job["status"] = "error"
        job["error"]  = str(exc)
    finally:
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
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"\n  Rank2  →  http://localhost:{port}\n")
    if not ACCESS_PASSWORDS:
        print("  ⚠  WARNING: ACCESS_PASSWORD not set in .env\n")
    uvicorn.run(app, host=host, port=port)
