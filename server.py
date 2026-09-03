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


_APP_VERSION = "1.07"
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
        _backfill_teaser_pdf(result, job)
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

        _backfill_teaser_pdf(result, job)
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


def _job_run_fqhc(
    job_id: str, entity_name: str, city: str, state: str,
    aggregate: bool = False,
) -> None:
    job = _jobs[job_id]
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, set_run_role
        from perception.fqhc_analyzer import analyze_fqhc

        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        result = analyze_fqhc(
            entity_name=entity_name,
            city=city,
            state=state,
            fqhc_intake=job.get("fqhc_intake"),
            aggregate=aggregate,
            site_roster=job.get("site_roster") or [],
            teaser_report=job.get("teaser_report", False),
            output_dir=REPORTS_DIR,
            on_event=emit,
            brand=job.get("brand", "original"),
            skip_pdf=job.get("skip_pdf", False),
            force_rerun=job.get("force_rerun", False),
            override_today_lock=job.get("override_today_lock", False),
            briefing_variant=job.get("briefing_variant"),
            report_title=job.get("report_title"),
        )
        _backfill_teaser_pdf(result, job)
        set_run_role(result.run_id, job["role"])
        job["status"] = "done"
        job["result"] = {
            "run_id": result.run_id,
            "location": result.location,
            "specialty": result.specialty,
            "provider_count": 1,  # FQHC is always a single-entity report
            "entity_type": "community_health",
            "mqcr": result.fqhc_mqcr,
            "pdf_path": result.pdf_path,
            "briefing_pdf_path": result.briefing_pdf_path,
            "briefing_skipped_reason": result.briefing_skipped_reason,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
        _put(loop, queue, None)


def _job_run_battery(job_id: str, fqhc_run_id: str, entity_name: str, city: str, state: str) -> None:
    job = _jobs[job_id]
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
    simplified_patient: bool = False            # Simplified Patient Pulse (compact 2-block cards)
    obscure_competitors: bool = True            # True=Enticement (obscure non-targets); False=Market Summary (all clear)
    target_entity: Optional[str] = None         # prospect/target entity name (enticement mode)
    service_line: Optional[str] = None          # enticement target is a hospital service line
    parent_system: Optional[str] = None
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

    if req.entity_type == "community_health" and entity_name:
        _jobs[job_id]["fqhc_intake"] = req.fqhc_intake
        _jobs[job_id]["site_roster"] = req.fqhc_site_roster or []
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
            service_line_a=req_dict.get("service_line_a"),
            parent_system_a=req_dict.get("parent_system_a"),
            service_line_b=req_dict.get("service_line_b"),
            parent_system_b=req_dict.get("parent_system_b"),
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


@app.get("/api/history")
async def get_history(role: str = Depends(require_auth)):
    from perception.db import init_db, query_history
    init_db()
    result = []
    for r in query_history(role):
        pdf_path = r.get("pdf_path")
        if r.get("report_type") == "network":
            # Network PDFs are regenerated on-demand from result_json if missing.
            # Show the download button whenever a pdf_path was recorded — the file
            # existing on the current container's disk is not required.
            has_pdf = bool(pdf_path)
        else:
            has_pdf = bool(pdf_path and Path(pdf_path).exists())
        result.append({
            **r,
            "generated_at": str(r["generated_at"]),
            "created_at": str(r["created_at"]) if r.get("created_at") else None,
            "has_pdf": has_pdf,
            "has_briefing_pdf": bool(
                r.get("briefing_pdf_path") and Path(r["briefing_pdf_path"]).exists()
            ),
        })
    return result


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
    """Ask Claude + Gemini to enumerate all facilities owned by a named healthcare network."""
    try:
        loop = asyncio.get_event_loop()
        from perception.network_analyzer import discover_hospitals_by_name
        data = await loop.run_in_executor(
            None, discover_hospitals_by_name, network_name, hq_location, facility_type
        )
        return data
    except Exception as exc:
        raise HTTPException(500, f"Network discover error: {type(exc).__name__}: {exc}")


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


@app.post("/api/network/analyze")
async def network_analyze(req: NetworkAnalyzeRequest, payload: dict = Depends(get_current_user_payload)):
    """Start a Network Pulse analysis job. Returns job_id for SSE streaming."""
    role  = payload["role"]
    brand = payload.get("brand", req.brand)
    ignore_cache = req.ignore_cache and (role == "admin")
    job_id = _new_job(role, brand)
    _pool.submit(_job_network_analyze, job_id, req.network_name, req.hq_location,
                 req.source_url, req.facilities, req.facility_type, brand, ignore_cache,
                 req.teaser)
    return {"job_id": job_id}


def _job_network_analyze(job_id: str, network_name: str, hq_location: str,
                          source_url: str, facilities: list[dict],
                          facility_type: str = "hospital",
                          brand: str = "original",
                          ignore_cache: bool = False,
                          teaser: bool = False) -> None:
    job = _jobs[job_id]
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
        )
        job["status"] = "done"
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
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = _job_error(exc)
    finally:
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
        _put(loop, queue, None)


@app.post("/api/network/bulk/run")
async def network_bulk_run(file: UploadFile = File(...),
                           payload: dict = Depends(get_current_user_payload)):
    """Headless bulk scoring of an uploaded list of hospital systems. The upload is
    saved so the run can be resumed. Returns a job_id (SSE) + bulk_id (CSV)."""
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
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"))
    _pool.submit(_run_network_bulk_job, job_id, bulk_id, str(input_path),
                 payload.get("brand", "original"))
    return {"job_id": job_id, "bulk_id": bulk_id, "total": len(rows)}


@app.post("/api/network/bulk/{bulk_id}/resume")
async def network_bulk_resume(bulk_id: str,
                              payload: dict = Depends(get_current_user_payload)):
    """Re-run a bulk list from its saved upload. Already-scored systems return
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
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"))
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
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"))
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
        from perception.models import NetworkResult
        from perception.network_pdf import render_network_pdf
        result = NetworkResult.model_validate_json(row[1])
        out_dir = Path("reports"); out_dir.mkdir(parents=True, exist_ok=True)
        slug = _re.sub(r"[^a-z0-9]+", "-", (result.network_name or "network").lower()).strip("-")
        pdf_path = out_dir / f"{slug}-network-pulse-{_dt.utcnow().strftime('%y%m%d-%H%M')}.pdf"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, render_network_pdf, result, str(pdf_path))
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


def _render_learn_page(articles: list[dict], *, title: str = "Learn about Pulse — AI Visibility Intelligence",
                       heading: str = "Learn about Pulse",
                       lede: str = "What Pulse is, what its reports reveal, who they help, and how to get started.",
                       desc: str = "Learn what Pulse is, what its AI-visibility reports show, who they help, and how to get started.",
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
    <div class="sub">AI VISIBILITY INTELLIGENCE</div>
  </header>
  <div class="wrap">
    <nav class="toc">{{TOC}}</nav>
    <main>
      <h1 class="page">{{HEADING}}</h1>
      <p class="lede">{{LEDE}}</p>
      {{BODY}}
    </main>
  </div>
  <footer>Pulse &middot; AI Visibility Intelligence &nbsp;|&nbsp; <a href="/">Sign in to run reports</a></footer>
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
        title="Methodology — Pulse AI Visibility Intelligence",
        heading="Pulse AI Visibility Methodology",
        lede="How Pulse measures AI visibility — the pillars, scoring rubric, national quartiles, data sources, and prompt battery behind every report.",
        desc="The full methodology behind Pulse AI Visibility reports: pillars, scoring, quartiles, data sources, and prompt battery.",
        empty_msg="The full methodology is being published. Check back shortly.",
    ))


@app.get("/api/learn")
async def learn_public_api():
    """Published Learn content (rendered HTML) — used by the in-app Learn view. Public."""
    from perception.db import init_db, list_learn_articles
    from perception.learn import render_markdown
    init_db()
    arts = list_learn_articles(include_unpublished=False, page="learn")
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
    from perception.learn_seed import STARTER_ARTICLES, METHODOLOGY_ARTICLES
    init_db()
    arts = METHODOLOGY_ARTICLES if page == "methodology" else STARTER_ARTICLES
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
    )
    create_event_entities(entities_db)

    job_id = _new_job(role, brand)
    _event_job_map[event_id] = job_id
    _pool.submit(_run_event_job, job_id, event_id, entities_db, req.entity_type, req.include_teaser, req.override_cache, req.auto_practice_composite)
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
    job_id = _new_job(payload.get("role", ""), payload.get("brand", "original"))
    _event_job_map[event_id] = job_id
    # Repeat the original run's settings so resumed entities are analyzed the same way.
    _pool.submit(_run_event_job, job_id, event_id, pending,
                 run.get("entity_type", "hospital"),
                 bool(run.get("include_teaser")),
                 bool(run.get("override_cache")),
                 bool(run.get("auto_practice_composite")))
    return {"event_id": event_id, "job_id": job_id, "pending": len(pending)}


def _run_event_job(
    job_id: str, event_id: str, entities: list, entity_type: str,
    include_teaser: bool = False,
    override_cache: bool = False,
    auto_practice_composite: bool = False,
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

                    # Teaser PDF: re-render the already-collected result with teaser_report=True.
                    # No API calls — just a second Playwright PDF render from the same data.
                    if include_teaser:
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
