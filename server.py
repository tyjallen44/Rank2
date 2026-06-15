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
import json
import os
import secrets
import shutil
import tempfile
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
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Rank2", docs_url=None, redoc_url=None)

# ── Config ────────────────────────────────────────────────────────────────────
_raw_pw = os.environ.get("ACCESS_PASSWORD", "")
ACCESS_PASSWORDS: set[str] = {p.strip() for p in _raw_pw.split(",") if p.strip()}
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
_tokens: dict[str, str] = {}  # token → role_id


class LoginRequest(BaseModel):
    password: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if not ACCESS_PASSWORDS:
        raise HTTPException(500, "ACCESS_PASSWORD not configured in .env")
    if req.password not in ACCESS_PASSWORDS:
        raise HTTPException(401, "Invalid password")
    role_id, display_name = _password_role(req.password)
    token = secrets.token_urlsafe(32)
    _tokens[token] = role_id
    return {"token": token, "role": role_id, "display_name": display_name}


def require_auth(request: Request, token: Optional[str] = Query(None)) -> str:
    """Accepts Bearer header or ?token= query param. Returns the user's role_id."""
    hdr = request.headers.get("Authorization", "")
    t = hdr[7:] if hdr.startswith("Bearer ") else token
    role = _tokens.get(t) if t else None
    if role is None:
        raise HTTPException(401, "Unauthorized")
    return role


@app.get("/api/auth/me")
async def me(role: str = Depends(require_auth)):
    return {"role": role, "display_name": _ROLE_DISPLAY.get(role, "Admin")}


# ── Job management ────────────────────────────────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_pool = ThreadPoolExecutor(max_workers=2)


def _put(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event: Any) -> None:
    asyncio.run_coroutine_threadsafe(queue.put(event), loop)


def _job_run_single(job_id: str, city: str, state: str, specialty: Optional[str]) -> None:
    job = _jobs[job_id]
    loop, queue = job["loop"], job["queue"]
    emit = lambda e: _put(loop, queue, e)

    try:
        from perception.db import init_db, set_run_role
        from perception.analyzer import analyze_location

        init_db()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        result = analyze_location(
            city=city, state=state, specialty=specialty,
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

class AnalyzeRequest(BaseModel):
    city: str
    state: str
    specialty: Optional[str] = None


class BatchRequest(BaseModel):
    groups: List[AnalyzeRequest]


@app.post("/api/analyze")
async def start_analysis(req: AnalyzeRequest, role: str = Depends(require_auth)):
    job_id = _new_job(role)
    _pool.submit(_job_run_single, job_id, req.city, req.state, req.specialty)
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
