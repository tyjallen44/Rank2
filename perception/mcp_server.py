"""The MCP endpoint — Pulse driven by an AI assistant instead of a browser.

Mounted onto the existing FastAPI app by ``server.py`` when PULSE_MCP_ENABLED=1,
so it shares this service's database, keys and renderers. Nothing here changes
an existing route: with the flag unset, ``server.py`` never imports this module.

THREE DECISIONS WORTH KNOWING BEFORE READING THE CODE.

**The server module is injected, not imported.** The container runs
``CMD ["python", "server.py"]``, so ``server.py`` is ``__main__`` and
``sys.modules["server"]`` does not exist. A top-level ``import server`` here
would execute that file a SECOND time and produce a second FastAPI app, a second
``_jobs`` dict and a second ThreadPoolExecutor. ``mount_mcp(app, srv)`` takes the
module object instead, which also removes the import cycle and lets the tool
tests run against a fake.

**The report tools reuse the job functions rather than the analyzers.**
``pulse_run_report`` builds a job record the same way ``POST /api/analyze`` does
and calls ``_job_run_single`` / ``_job_run_practice`` / ``_job_run_fqhc`` on a
thread. That inherits, permanently, ``_backfill_teaser_pdf``, ``set_run_role``,
``_run_confidence``, ``_finalize_hospital_combined``,
``_finalize_practice_combined``, ``_notify_run_complete`` and every cache rule —
and it keeps inheriting them when those functions change. Calling
``analyze_location`` directly would silently drop the content-analysis fold-in
and the confidence row.

**A run blocks to completion inside the tool call.** ``_jobs`` is a process-local
dict on a service that runs up to ten instances, so a job id handed to a
non-browser client is worthless the moment a follow-up request lands elsewhere;
``--session-affinity`` is a browser-cookie mechanism an MCP client does not
carry. Cloud Run's ``--timeout=3600`` makes a blocking call fine. If the tool's
own timeout fires first the run keeps going on its thread and still writes its
``analysis_runs`` row; the caller is told to pick it up with ``pulse_history``
then ``pulse_get_report``.

MCP runs land in ``srv._jobs``, so they show up in Admin -> Operations and in a
user's own "my jobs" list, tagged ``source="mcp"``. They use their own small
thread pool, never ``server._pool``, so a burst of MCP runs cannot make the
browser queue slower.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import ipaddress
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import anyio.to_thread
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .mcp_auth import PulseAuthError, PulseIdentity, authenticate, oauth_config, verifier
from . import mcp_usage

# ── What the model is told ────────────────────────────────────────────────────
_INSTRUCTIONS = """\
Pulse is RLDatix's AI reputation analysis engine for healthcare organizations.
Send an organization name and a city and state. Never send patient information,
CRM notes, deal context, or anything about a named individual.
Start with pulse_find_entity to confirm the right organization, then
pulse_content_check (free, code checks only) before spending a report run.
pulse_run_report, pulse_compare and pulse_network_report each spend real analysis
budget and count against a daily cap; they block for several minutes.
Reports, history and trends are scoped to the person calling: you can only read
runs this caller started."""

_TOOL_NAMES = (
    "pulse_find_entity", "pulse_content_check", "pulse_run_report", "pulse_get_report",
    "pulse_compare", "pulse_network_report", "pulse_history", "pulse_trend",
    "pulse_content_draft",
)

#: Entries in `srv._jobs` that this module created are swept after this long, so
#: our contribution to his already-unbounded dict stays bounded. Matches the
#: window `/api/jobs/mine` already uses to hide finished jobs.
_JOB_SWEEP_SECONDS = 6 * 3600
_MAX_NETWORK_FACILITIES = 200
#: Length ceilings on the two remaining free-text arguments. Neither is a field
#: anybody fills with prose in the web UI; the cap is what stops a model using
#: one as a channel for text that has no business leaving the caller's context.
_MAX_SPECIALTY_CHARS = 80
_MAX_HQ_LOCATION_CHARS = 120
#: The only keys a caller-supplied facility entry may carry. His discovery route
#: also emits `beds`, but that arrives from Pulse's own model call and never
#: from the MCP caller, so the tool's own input stays name/city/state.
_FACILITY_KEYS = ("name", "city", "state")

_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})

# `individual_report` is not decoration. The join below takes the rank-1
# ranked_providers row, which for a Deep Diagnostic is the requested
# organization and for a MARKET report is the top-ranked COMPETITOR — and
# analyzer.py writes analysis_runs.entity_name = NULL for those runs. Without
# this column the two are indistinguishable here and a market run renders as
# "Unknown — Salt Lake City, UT / Hospital, Deep Diagnostic" over somebody
# else's score.
_RUN_COLUMNS = (
    "entity_name", "location", "generated_at", "entity_type", "specialty", "service_line",
    "parent_system", "confidence", "confidence_note", "mqcr", "ran_by", "pdf_path",
    "teaser_pdf_path", "briefing_pdf_path", "individual_report", "ai_visibility_score",
    "tier_scores", "overall_rating", "weighting_profile",
)

_RUN_SQL = """
    SELECT a.entity_name, a.location, a.generated_at, a.entity_type, a.specialty,
           a.service_line, a.parent_system, a.confidence, a.confidence_note, a.mqcr,
           a.ran_by, a.pdf_path, a.teaser_pdf_path, a.briefing_pdf_path,
           a.individual_report,
           p.ai_visibility_score, p.tier_scores, p.overall_rating, p.weighting_profile
      FROM analysis_runs a
      LEFT JOIN ranked_providers p ON p.run_id = a.run_id AND p.rank = 1
     WHERE a.run_id = ?
"""

# The caller's own runs only, in all three tables. See the ownership note in
# _pulse_history: the history helper in perception/db.py filters on user_role, and
# our role mapping puts
# every AE, BDR and sales lead into the single string "salesteam", so a role-wide
# read would show one rep every other rep's runs.
#
# The analysis filter is his own History filter verbatim: a comparison's side
# runs have no PDF of their own and are hidden, while a run that carries a PDF
# stays visible whatever else is tagged on it.
#
# `individual_report = TRUE` is ours, and it is here so the two tools agree:
# pulse_get_report cannot render a market run as one organization's score (see
# _RUN_COLUMNS), so pulse_history must not hand the model those run ids. His own
# single-entity reads use the same predicate. A market report a rep started in
# the browser is still in Pulse History where it renders as what it is.
_HISTORY_ANALYSIS_SQL = """
    SELECT run_id, entity_name, location, generated_at, entity_type, specialty, pdf_path
      FROM analysis_runs
     WHERE LOWER(ran_by) = LOWER(?) AND generated_at >= ?
       AND individual_report = TRUE
       AND NOT (comparison_id IS NOT NULL AND pdf_path IS NULL)
"""

_HISTORY_NETWORK_SQL = """
    SELECT run_id, network_name, generated_at, facility_type, ai_visibility_score
      FROM network_runs
     WHERE LOWER(ran_by) = LOWER(?) AND generated_at >= ?
"""

_HISTORY_COMPARISON_SQL = """
    SELECT id, entity_a, entity_b, generated_at
      FROM comparison_runs
     WHERE LOWER(ran_by) = LOWER(?) AND generated_at >= ?
"""

_TREND_SQL = """
    SELECT a.run_id, a.generated_at, p.ai_visibility_score, p.tier_scores,
           p.google_footprint, a.location, a.weighting_profile
      FROM analysis_runs a
      JOIN ranked_providers p ON p.run_id = a.run_id AND p.rank = 1
     WHERE LOWER(a.entity_name) = LOWER(?)
       AND a.individual_report = TRUE
       AND LOWER(a.ran_by) = LOWER(?)
"""

_COMPARISON_SQL = """
    SELECT run_id_a, run_id_b, entity_a, entity_b, pdf_path, ran_by, generated_at
      FROM comparison_runs WHERE id = ?
"""

_NETWORK_SQL = """
    SELECT network_name, ai_visibility_score, grade, total_hospitals, generated_at,
           ran_by, pdf_path, teaser_pdf_path, full_detail_pdf_path
      FROM network_runs WHERE run_id = ?
"""

# ── Per-request identity ──────────────────────────────────────────────────────
# Set by the auth middleware and reset in a finally. A tool reads it through
# _current_identity(), which raises rather than defaulting: defence in depth, so
# a tool can never run without a verified caller even if the wiring changes.
_IDENTITY: contextvars.ContextVar[PulseIdentity] = contextvars.ContextVar("pulse_mcp_identity")

_POOL: Optional[ThreadPoolExecutor] = None


def _current_identity() -> PulseIdentity:
    """The verified caller behind the request being served, or a refusal."""
    try:
        return _IDENTITY.get()
    except LookupError:
        raise PulseAuthError("No authenticated Pulse caller for this request.") from None


def _env_int(name: str, default: int) -> int:
    """An integer environment setting, falling back loudly on nonsense."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[pulse-mcp] {name}={raw!r} is not an integer; using {default}")
        return default
    if value < 1:
        print(f"[pulse-mcp] {name}={value} is below 1; using {default}")
        return default
    return value


def _pool() -> ThreadPoolExecutor:
    """The MCP's own thread pool, built on first use.

    Deliberately NOT ``server._pool``: that pool has two workers and is what the
    web UI's runs queue behind. A burst of MCP runs must not make the browser
    experience slower."""
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(
            max_workers=_env_int("PULSE_MCP_MAX_WORKERS", 2),
            thread_name_prefix="pulse-mcp",
        )
    return _POOL


def _run_timeout() -> int:
    """How long a run tool blocks before returning the "still running" message.
    Must be set below the MCP client's own tool timeout."""
    return _env_int("PULSE_MCP_RUN_TIMEOUT_SECONDS", 900)


def _pdf_ttl() -> int:
    """Lifetime of a minted PDF download token.

    Ten minutes, not an hour: the minted token is a full Pulse session
    credential (see ``_pdf_links``), and it is printed as plain text into a chat
    transcript. The window only has to outlive somebody clicking the link."""
    return _env_int("PULSE_MCP_PDF_LINK_TTL_SECONDS", 600)


def _connect() -> Any:
    """The one database seam in this module — tests monkeypatch
    perception.db.get_connection. Imported inside the function like every other
    database call in this codebase, so nothing connects at import time."""
    from perception.db import get_connection
    return get_connection()


# ── Input cleaning ────────────────────────────────────────────────────────────
# The mechanical half of "nothing but an organization name and a location
# crosses to Pulse": no tool takes free text beyond a name, a city, a state, a
# specialty and a website URL, and every one of those is cleaned here.

class _Refusal(Exception):
    """A bad tool argument, carrying the exact text to hand back to the model."""


def _tool(func):
    """Turn a bad-argument refusal into the string the tool returns.

    Applied to the implementation rather than only to the registered wrapper, so
    a refusal is a returned string for EVERY caller — the tests call these
    functions directly, and a tool that raises past its own boundary would reach
    an MCP client as a protocol error instead of an answer it can act on."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except _Refusal as refusal:
            return str(refusal)
    return wrapper


def _clean_org(srv: Any, name: str) -> str:
    """An organization name, title-cased the way his routes do. Refuses empty
    and absurd lengths rather than passing them to Google Places."""
    text = (name or "").strip()
    if len(text) < 2 or len(text) > 200:
        raise _Refusal("Refused: organization must be between 2 and 200 characters.")
    return srv._normalize_input(text) or text


def _clean_state(state: str, *, required: bool = True) -> str:
    """A two-letter US state abbreviation, upper-cased."""
    text = (state or "").strip().upper()
    if not text:
        if required:
            raise _Refusal('Refused: state must be a two-letter US abbreviation, for example "UT".')
        return ""
    if text not in _US_STATES:
        raise _Refusal('Refused: state must be a two-letter US abbreviation, for example "UT".')
    return text


def _clean_city(srv: Any, city: str) -> str:
    """A city name, title-cased the way his routes do."""
    return srv._normalize_input((city or "").strip()) or ""


def _clean_specialty(srv: Any, specialty: str) -> Optional[str]:
    """A specialty or service line, or None. Bounded because it is free text."""
    text = (specialty or "").strip()
    if not text:
        return None
    if len(text) > _MAX_SPECIALTY_CHARS:
        raise _Refusal(f"Refused: specialty must be {_MAX_SPECIALTY_CHARS} characters or "
                       f"fewer. It names a service line, not a description.")
    return srv._normalize_input(text)


def _resolve_addresses(host: str) -> list[str]:
    """Every IP `host` resolves to. Separated out so the private-address guard
    below is testable without either mocking the stdlib or reaching DNS."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _clean_url(value: str, *, field: str = "website_url") -> str:
    """A public http(s) front door, or a refusal. Empty in, empty out.

    Everything this rejects is rejected because the fetch happens INSIDE Ty's
    Cloud Run service, on behalf of a language model that can be steered by the
    pages it reads. Without this, the URL parameters are a working internal
    host-and-port scanner (the findings echo `fetch_status`, `pages_crawled` and
    the origin back to the caller) and a query string on them is an egress
    channel for arbitrary text out of the model's context.

    ``content_analyzer._norm_url`` only prepends https:// when there is no
    ``//``, and ``_client()`` follows redirects, so ``file:///etc/passwd``,
    ``http://169.254.169.254/`` and ``http://127.0.0.1:8000/api/...`` all reach
    ``client.get`` and then Playwright. Every address behind the hostname is
    checked, not just the first: a name that resolves to one public and one
    private address is the rebinding case."""
    text = (value or "").strip()
    if not text:
        return ""

    refusal = f"Refused: {field} must be a public http(s) address."
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https"):
        raise _Refusal(refusal)
    if "@" in parsed.netloc:
        # Credentials in a URL, and the half before the @ is also the classic
        # way to make a hostile host look like a familiar one.
        raise _Refusal(refusal)
    if parsed.query or parsed.fragment:
        raise _Refusal(f"Refused: {field} must be a plain front-door address with no query "
                       f"string or fragment.")
    host = parsed.hostname
    if not host:
        raise _Refusal(refusal)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = _resolve_addresses(host)
        except OSError:
            raise _Refusal(f"Refused: {field} names a host that does not resolve "
                           f"({host}).") from None
        if not addresses:
            raise _Refusal(f"Refused: {field} names a host that does not resolve "
                           f"({host}).")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:                                # pragma: no cover - getaddrinfo
            raise _Refusal(refusal) from None             # cannot produce a non-address
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise _Refusal(f"Refused: {field} resolves to {ip}, which is not a public "
                           f"address. Pulse only reads public websites.")
    return text


def _clean_facilities(srv: Any, facilities: Optional[list]) -> list[dict]:
    """A caller-supplied facility roster, reduced to name, city and state.

    The entries are dicts with no schema of their own, so without this the list
    is the one place in the whole surface a model could put a contact name, a
    deal note or anything else it happens to be holding, and it would travel
    straight to Places and Anthropic. Unknown keys are a refusal rather than a
    silent drop: a caller who passed one meant something by it and should be
    told it is not carried."""
    roster: list[dict] = []
    for index, entry in enumerate(facilities or [], 1):
        if not isinstance(entry, dict):
            raise _Refusal(f"Refused: facilities entry {index} is not an object. Each entry "
                           f"is {{name, city, state}}.")
        extra = sorted(set(entry) - set(_FACILITY_KEYS))
        if extra:
            raise _Refusal(f"Refused: facilities entry {index} carries {', '.join(extra)}. "
                           f"Each entry takes name, city and state and nothing else.")
        try:
            cleaned = {
                "name": _clean_org(srv, str(entry.get("name") or "")),
                "city": _clean_city(srv, str(entry.get("city") or "")),
                "state": _clean_state(str(entry.get("state") or ""), required=False),
            }
        except _Refusal as refusal:
            # The shared cleaners speak about "organization" and "state" with no
            # idea which of two hundred entries they were given. Say which.
            raise _Refusal(f"Refused: facilities entry {index} — "
                           f"{str(refusal).removeprefix('Refused: ')}") from None
        roster.append(cleaned)
    return roster


def _clean_int(value: Any, default: int, low: int, high: int, *, field: str) -> int:
    """A bounded numeric tool argument, clamped to [low, high].

    A model can hand a string-typed MCP argument anything JSON allows through
    it, including a word, empty text or a list. The bare ``int(value or
    default)`` this replaces raises ValueError on a non-numeric value, past
    the ``_tool`` boundary, which reaches the caller as a protocol error
    rather than an answer it can act on. Every other bad tool argument in
    this module is a _Refusal; this makes a bad number one too. A falsy
    value (0, None, "") means "unset" and takes the default, same as the
    code this replaces."""
    raw = value or default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise _Refusal(f"Refused: {field} must be a whole number.") from None
    return max(low, min(parsed, high))


# ── Jobs ──────────────────────────────────────────────────────────────────────

def _sweep_mcp_jobs(srv: Any) -> None:
    """Drop finished MCP jobs older than the sweep window.

    Only entries this module created (``source == "mcp"``) are ever touched: his
    dict is his, and a running job is never removed."""
    now = time.time()
    for job_id, job in list(getattr(srv, "_jobs", {}).items()):
        if job.get("source") != "mcp":
            continue
        if job.get("status") == "running":
            continue
        if now - (job.get("started_at") or now) > _JOB_SWEEP_SECONDS:
            srv._jobs.pop(job_id, None)


class _Progress:
    """The most recent phase line from a running job.

    The job's queue exists because ``_put`` pushes cross-thread onto it for the
    SSE stream; nothing reads it on an MCP call, so draining it turns a leak into
    the progress line the timeout message needs."""

    def __init__(self) -> None:
        self.last_phase = ""

    async def drain(self, queue: "asyncio.Queue") -> None:
        while True:
            event = await queue.get()
            if event is None:
                return
            if isinstance(event, dict) and event.get("type") == "phase":
                self.last_phase = str(event.get("text") or "").strip()


async def _run_job(srv: Any, job_id: str, fn: Callable[..., Any], *args: Any) -> tuple[str, str]:
    """Run one of his job functions on the MCP pool and wait for it.

    Returns ``(outcome, last_phase)`` where outcome is "done", "error" or
    "timeout". On timeout the thread is NOT cancelled — ``asyncio.wait_for``
    cancels the wait, not the work — so the run continues and still persists its
    row when it finishes. That is the recovery path, and it is real. Leaving the
    shielded future unawaited is safe because his job functions catch every
    exception themselves and record it on the job as ``status = "error"``."""
    job = srv._jobs[job_id]
    progress = _Progress()
    drainer = asyncio.create_task(progress.drain(job["queue"]))
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_pool(), fn, *args)
    try:
        await asyncio.wait_for(asyncio.shield(future), timeout=_run_timeout())
    except asyncio.TimeoutError:
        return "timeout", progress.last_phase
    finally:
        if not drainer.done():
            drainer.cancel()
    return str(job.get("status") or "error"), progress.last_phase


def _timeout_message(what: str, last_phase: str, seconds: float) -> str:
    """The honest "it is still going" answer, naming the last step reached."""
    minutes = max(1, int(round(seconds / 60.0)))
    unit = "minute" if minutes == 1 else "minutes"
    lines = [f"Still running after {minutes} {unit}: {what}."]
    if last_phase:
        lines.append(f"Last step: {last_phase}")
    lines += [
        "The run is still going on the server and will save itself when it finishes.",
        "Ask again in a few minutes with pulse_history, then pulse_get_report on the run id.",
        "This attempt has already counted against your daily cap.",
    ]
    return "\n".join(lines)


def _error_message(job_id: str, job: dict) -> str:
    """A failed job rendered as a refusal, with his overload marker spelled out.

    The raw error is LOGGED, never returned. His job functions record exception
    strings, which routinely carry connection strings, absolute paths under
    REPORTS_DIR, upstream API bodies and SQL fragments — and everything this
    function returns lands in the model's context and in the chat transcript of
    whoever ran the tool. ``__OVERLOADED__`` is the exception because it is a
    marker his code writes deliberately, not an exception message."""
    error = str(job.get("error") or "unknown error")
    if error == "__OVERLOADED__":
        return ("Refused: the analysis service is overloaded right now. Try again in a few "
                "minutes. This attempt still counted against your daily cap.")
    print(f"[pulse-mcp] job {job_id} failed: {error}")
    return ("Refused: the run failed on the server. A Pulse admin can find it in "
            "Admin -> Operations.")


def _cap_refusal(cap: int) -> str:
    """The daily-cap refusal. Names the caller's own limit and nobody else's."""
    return (f"Refused: you have used your daily Pulse limit of {cap} report runs. Each run "
            f"spends real analysis budget on the shared RLDatix key. The limit resets at "
            f"midnight UTC.\npulse_history, pulse_get_report and pulse_trend still work, and "
            f"so does pulse_content_check — it spends nothing.")


# ── Reading a run ─────────────────────────────────────────────────────────────

def _tier_labels(profile: Optional[str]) -> dict:
    """Return the correct tier-label dict for a given weighting profile."""
    from perception.scoring import TIER_LABELS, PRACTICE_TIER_LABELS
    if profile and profile.startswith("practice_"):
        return PRACTICE_TIER_LABELS.get(profile, PRACTICE_TIER_LABELS["practice_procedural"])
    return TIER_LABELS.get(profile or "procedural", TIER_LABELS["procedural"])


def _load_run(run_id: str) -> Optional[dict]:
    """One run's durable row — analysis_runs joined to its rank-1 provider.

    The score is NOT in ``job["result"]`` for hospital, practice or FQHC runs, so
    it is read here from ``ranked_providers``; that is why this query exists at
    all rather than the tool reading the job dict."""
    con = _connect()
    try:
        row = con.execute(_RUN_SQL, [run_id]).fetchone()
    finally:
        con.close()
    return dict(zip(_RUN_COLUMNS, row)) if row else None


def _owned(run: Optional[dict], identity: PulseIdentity) -> bool:
    """Is this run the caller's own? Ownership is ``ran_by``, case-insensitively,
    with no role-based widening anywhere."""
    if not run:
        return False
    return str(run.get("ran_by") or "").strip().lower() == identity.email.strip().lower()


def _not_yours(run_id: str) -> str:
    """The single answer for "not yours" and "does not exist" alike — telling the
    two apart would leak which run ids are real."""
    return (f"Not found: no run {run_id} of yours. pulse_history lists the runs you have "
            f"started.")


def _pdf_links(srv: Any, identity: PulseIdentity, run: dict, run_id: str) -> list[str]:
    """Download lines for whichever PDFs this run actually has.

    READ THIS BEFORE CHANGING THE MINT. What goes in the link is not a
    PDF-scoped capability — it is a full Pulse session token. ``_create_token``
    is the same function his login uses, and ``require_auth`` accepts its output
    on every REST route, header or ``?token=``. Two consequences the code cannot
    avoid and the caller must therefore be given in the smallest possible size:

    * the role it carries is ``identity.account_role``, the caller's own Pulse
      ``users.role``, NEVER the mapped RLDatix group. ``download_pdf`` resolves
      the run through the role-scoped history helper in ``perception/db.py``,
      which filters on ``user_role``, and the run was stamped with the same
      ``account_role`` (see the ``_new_job`` call sites) — so the two agree
      and neither exceeds what that person's own Pulse login already grants.
      Minting the mapped group handed a Google-approved account whose Pulse role
      is ``user`` a working ``salesteam`` credential, and with it every
      salesteam run, its ``ran_by`` emails and its PDFs.
    * ``exp`` is overridden to ``_pdf_ttl()`` (ten minutes) because
      ``_create_token`` builds ``{"role": ..., "exp": ..., **extra}`` and an
      ``exp`` in extra wins, so the link does not inherit the thirty-day session
      default. A link leaked from a chat transcript is ten minutes of that
      person's Pulse access, not a month of it."""
    ttl = _pdf_ttl()
    token = srv._create_token(identity.account_role, email=identity.email, name=identity.name,
                              brand=identity.brand, exp=int(time.time()) + ttl)
    base = getattr(srv, "APP_URL", "")
    out = []
    for label, column, path in (("Full report", "pdf_path", "pdf"),
                                ("Teaser", "teaser_pdf_path", "teaser-pdf"),
                                ("Briefing", "briefing_pdf_path", "briefing-pdf")):
        if run.get(column):
            out.append(f"  {label + ':':<14}{base}/api/reports/{run_id}/{path}?token={token}")
    if out:
        out.insert(0, f"Download (links expire in {ttl // 60} minutes):")
    return out


def _score_block(run: dict) -> list[str]:
    """Score, grade, quartile, evidence and the four pillars for one run."""
    from perception.scoring import grade_from_score

    lines: list[str] = []
    score = run.get("ai_visibility_score")
    code, band = grade_from_score(score if score is None else int(score))
    grade = run.get("overall_rating") or "—"
    if score is None:
        lines.append("AI Visibility Score: not scored yet")
    else:
        lines.append(f"AI Visibility Score: {int(score)}  (Grade {grade}, {code} {band})")

    confidence = run.get("confidence")
    if confidence:
        note = run.get("confidence_note")
        lines.append(f"Evidence: {confidence}" + (f" — {note}" if note else ""))
    if run.get("mqcr") is not None:
        lines.append(f"Mission Query Capture Rate: {run['mqcr']}")

    try:
        tiers = json.loads(run.get("tier_scores") or "{}")
    except (TypeError, ValueError):
        tiers = {}
    if tiers:
        profile = run.get("weighting_profile")
        labels = _tier_labels(profile)
        from perception.db import rubric_for_profile
        lines.append("")
        lines.append(f"Pillars ({rubric_for_profile(profile)} rubric)")
        for key, label in labels.items():
            value = tiers.get(key)
            lines.append(f"  {label:<34}{'—' if value is None else round(float(value))}")
    return lines


def _canonical_note(run: dict) -> list[str]:
    """Cross-check the run's score against the canonical entity_scores row.

    Not a substitute for it: ``entity_scores`` is what makes a market report and
    a network report agree on one entity, so a disagreement is a fact worth
    printing rather than a tie to break silently."""
    from perception.db import get_entity_score
    try:
        canonical = get_entity_score(run.get("entity_name") or "", run.get("location") or "")
    except Exception as exc:
        print(f"[pulse-mcp] canonical score lookup failed: {type(exc).__name__}: {exc}")
        return []
    if not canonical or canonical.get("pulse_score") is None:
        return []
    score = run.get("ai_visibility_score")
    canonical_score = int(canonical["pulse_score"])
    day = canonical.get("generated_at") or ""
    if score is not None and int(score) == canonical_score:
        return ["", f"Canonical score for this entity and location on {day}: "
                    f"{canonical_score} (matches)."]
    return ["", f"Canonical score for this entity and location on {day}: {canonical_score} — "
                f"this run reports {'nothing' if score is None else int(score)}. "
                f"Both are shown because the two rows disagree."]


def _render_run(srv: Any, identity: PulseIdentity, run_id: str, run: dict) -> str:
    """The block both pulse_run_report and pulse_get_report print, so the two
    tools never describe the same run differently."""
    entity_type = run.get("entity_type") or "hospital"
    # His own names for the two report kinds, so a rep reads the same words here
    # as in History.
    header_two = ("Community Health report" if entity_type == "community_health"
                  else f"{entity_type.replace('_', ' ').title()}, Deep Diagnostic")
    parts = [p for p in (run.get("specialty"), run.get("service_line")) if p]
    if parts:
        header_two += " (" + ", ".join(str(p) for p in parts) + ")"
    lines = [
        f"{run.get('entity_name') or 'Unknown'} — {run.get('location') or ''}",
        f"{header_two}  ·  run {run_id}  ·  {run.get('generated_at')}  ·  "
        f"started by {run.get('ran_by') or 'unknown'}",
        "",
    ]
    lines += _score_block(run)
    lines += _canonical_note(run)
    links = _pdf_links(srv, identity, run, run_id)
    if links:
        lines += [""] + links
    return "\n".join(lines)


# ── Tools ─────────────────────────────────────────────────────────────────────
# Each is a plain module-level function taking `srv` and `identity` explicitly,
# so the tests call them directly and never touch the SDK's call_tool signature,
# which changes between SDK versions. build_mcp registers thin wrappers.

@_tool
async def _pulse_find_entity(srv: Any, identity: PulseIdentity, organization: str,
                             city: str = "", state: str = "", zip_code: str = "") -> str:
    """Confirm the right organization before spending a run. One Places search;
    does not count against the daily cap."""
    name = _clean_org(srv, organization)
    state = _clean_state(state, required=False)
    city = _clean_city(srv, city)

    if zip_code.strip() and not (city and state):
        try:
            city, state = srv._zip_to_city_state(zip_code.strip())
            city = _clean_city(srv, city)
            state = _clean_state(state, required=False)
        except Exception as exc:
            print(f"[pulse-mcp] ZIP lookup failed for {zip_code!r}: {type(exc).__name__}: {exc}")

    from perception.data.places import search_entity_candidates
    loop = asyncio.get_running_loop()
    candidates = await loop.run_in_executor(
        _pool(), lambda: search_entity_candidates(name, city, state))

    where = ", ".join(p for p in (city, state) if p) or "the United States"
    if not candidates:
        # search_entity_candidates returns [] for a missing API key AND for no
        # matches; claiming to know which would be a guess.
        return (f'Not found: no Google listing matched "{name}" near {where}. Check the '
                f"spelling, or try the organization's legal name.")

    lines = [f'Candidates for "{name}" near {where}:', ""]
    for i, candidate in enumerate(candidates, 1):
        lines.append(f"{i}. {candidate.get('name') or '(unnamed)'}")
        if candidate.get("address"):
            lines.append(f"   {candidate['address']}")
        rating, count = candidate.get("rating"), candidate.get("review_count")
        if rating is not None:
            lines.append(f"   Google {rating} ({count or 0:,} reviews)")
    lines += ["", "Use the exact name and the city and state of the one you want in "
                  "pulse_run_report."]
    return "\n".join(lines)


@_tool
async def _pulse_content_check(srv: Any, identity: PulseIdentity, organization: str,
                               city: str = "", state: str = "", entity_kind: str = "hospital",
                               website_url: str = "", attach_to_run_id: str = "") -> str:
    """Code-only content checks: website crawl, JSON-LD, llms.txt, robots.txt,
    Wikidata, Wikipedia. No model call, so it does not count against the cap —
    which is why it is the tool the model is told to reach for first."""
    name = _clean_org(srv, organization)
    state = _clean_state(state, required=False)
    city = _clean_city(srv, city)
    kind = (entity_kind or "hospital").strip().lower()
    if kind not in ("hospital", "practice"):
        return 'Refused: entity_kind must be "hospital" or "practice".'

    loop = asyncio.get_running_loop()
    front_door = _clean_url(website_url)
    urls = [front_door] if front_door else []
    if not urls:
        # Resolve the front door the same way _finalize_hospital_combined does,
        # rather than crawling a guessed domain.
        from perception.data.places import fetch_provider
        from urllib.parse import urlsplit, urlunsplit
        try:
            read, _footprint = await loop.run_in_executor(
                _pool(), lambda: fetch_provider(name, city, state))
            if read and read.website:
                split = urlsplit(read.website)
                urls = [urlunsplit((split.scheme, split.netloc, split.path, "", "")).rstrip("/")]
        except Exception as exc:
            print(f"[pulse-mcp] website lookup failed for {name!r}: {type(exc).__name__}: {exc}")
    if not urls:
        return (f'Refused: no website could be resolved for "{name}" in '
                f"{city or '?'}, {state or '?'}. Pass website_url.")

    # Deliberately NOT _job_content_analysis: that job runs a full base Deep
    # Diagnostic first, which spends Anthropic and Places budget. Calling
    # analyze_content directly is what makes this tool cheap enough to be the
    # default first call. reputation and safety are None because they come from
    # a full report run's verified data; _check_reputation/_check_safety return
    # [] for a falsy argument, so those groups come back absent, not broken.
    from perception import content_analyzer
    findings = await loop.run_in_executor(
        _pool(),
        lambda: content_analyzer.analyze_content(name, urls, city, state, entity_kind=kind,
                                                 reputation=None, safety=None))

    snapshot = findings.source_snapshot or {}
    header = [f"Content check — {name}" + (f" ({city}, {state})" if city and state else ""),
              "  ·  ".join(filter(None, [
                  f"Status: {findings.status}",
                  f"{snapshot.get('pages_crawled', 0)} pages crawled",
                  f"Wikidata {snapshot['wikidata_qid']}" if snapshot.get("wikidata_qid") else "",
                  f"Wikipedia: {snapshot['wikipedia_article']}" if snapshot.get("wikipedia_article") else "",
              ])),
              ""]

    body: list[str] = []
    for severity in ("high", "medium", "low"):
        group = [f for f in findings.findings if (f.severity or "").lower() == severity]
        if not group:
            continue
        body.append(severity.upper())
        for finding in group:
            body.append(f"- [{finding.finding_id}] {finding.teaser_summary} ({finding.platform})")
            if finding.current_state:
                body.append(f"  Now: {finding.current_state}")
            if finding.expected_state:
                body.append(f"  Expected: {finding.expected_state}")
    if not body:
        body = ["No findings were produced — nothing on this site could be checked."]

    tail = ["", "Not checked here: reputation and safety findings need a full report run",
            "(pulse_run_report), which supplies the verified Google and CMS data they read."]

    saved = ""
    if attach_to_run_id.strip():
        run_id = attach_to_run_id.strip()
        run = _load_run(run_id)
        if not _owned(run, identity):
            saved = _not_yours(run_id)
        else:
            from perception.db import save_content_findings, _norm_entity_name
            save_content_findings(run_id, _norm_entity_name(name), snapshot,
                                  [f.model_dump() for f in findings.findings], findings.status)
            saved = (f"Findings were saved against run {run_id}, so pulse_content_draft can "
                     f"write remediation copy for them.")
    if not saved:
        saved = ("Findings were not saved. Pass attach_to_run_id=<run id of one of your runs> "
                 "to save them so pulse_content_draft can write remediation copy.")
    return "\n".join(header + body + tail + ["", saved])


@_tool
async def _pulse_run_report(srv: Any, identity: PulseIdentity, organization: str, city: str,
                            state: str, report_type: str = "hospital", specialty: str = "",
                            aggregate: bool = True, briefing: str = "") -> str:
    """Start and finish one organization's report inside this call. Counts 1
    against the daily cap, reserved before the run starts."""
    kinds = {"hospital": "hospital", "practice": "practice", "fqhc": "community_health"}
    kind = (report_type or "hospital").strip().lower()
    if kind not in kinds:
        return 'Refused: report_type must be "hospital", "practice" or "fqhc".'
    variant = (briefing or "").strip().lower()
    if variant not in ("", "sales", "cs"):
        return 'Refused: briefing must be empty, "sales" or "cs".'

    name = _clean_org(srv, organization)
    state = _clean_state(state)
    city = _clean_city(srv, city)
    if not city:
        return "Refused: city is required."
    entity_type = kinds[kind]
    specialty_value = _clean_specialty(srv, specialty)

    _sweep_mcp_jobs(srv)
    cap = mcp_usage.daily_cap()
    try:
        used = mcp_usage.reserve_run(identity.email, cap)
    except mcp_usage.DailyCapExceeded:
        return _cap_refusal(cap)

    # account_role, not the mapped group: _new_job's role is what set_run_role
    # writes to analysis_runs.user_role, and that has to be the same value the
    # download token carries (see _pdf_links) and no wider than the caller's own
    # Pulse login.
    job_id = srv._new_job(identity.account_role, identity.brand, identity.email)
    job = srv._jobs[job_id]
    # The same fields POST /api/analyze sets. force_rerun and override_today_lock
    # are False unconditionally: his same-day and 90-day caches are the main
    # brake on repeat spend, and no MCP caller gets to bypass them
    # (override_today_lock is admin-only on his routes and stays unreachable).
    job.update({
        "entity_name": name,
        "individual_report": True,
        "entity_type": entity_type,
        "specialty": specialty_value,
        "briefing_variant": variant or None,
        "force_rerun": False,
        "override_today_lock": False,
        "skip_pdf": False,
        "content_urls": [],
        "source": "mcp",
        "label": name,
    })
    if entity_type == "community_health":
        job["fqhc_intake"] = None
        job["site_roster"] = []
        runner, args = srv._job_run_fqhc, (job_id, name, city, state, bool(aggregate))
    elif entity_type == "practice":
        runner = srv._job_run_practice
        args = (job_id, name, city, state, specialty_value, bool(aggregate), None)
    else:
        runner = srv._job_run_single
        args = (job_id, city, state, specialty_value, bool(aggregate), None, entity_type)

    outcome, last_phase = await _run_job(srv, job_id, runner, *args)
    if outcome == "timeout":
        # The _jobs entry is deliberately left in place: the run is still on its
        # thread and the sweeper collects the entry later.
        return _timeout_message(f"{name} ({city}, {state})", last_phase, _run_timeout())
    if outcome != "done":
        return _error_message(job_id, job)

    run_id = (job.get("result") or {}).get("run_id")
    if not run_id:
        return "Refused: the run finished without saving a report. Nothing to read."
    run = _load_run(str(run_id))
    if not run:
        return (f"The run finished as {run_id} but its row is not readable yet. Try "
                f"pulse_get_report on that id in a moment.")
    header = f"Report complete — {name} ({city}, {state})\nRuns used today: {used} of {cap}.\n"
    return header + "\n" + _render_run(srv, identity, str(run_id), run)


@_tool
async def _pulse_get_report(srv: Any, identity: PulseIdentity, run_id: str) -> str:
    """Score, pillar breakdown and download links for one of the caller's own
    finished runs. Free."""
    run_id = (run_id or "").strip()
    if not run_id:
        return "Refused: run_id is required."
    run = _load_run(run_id)
    if not _owned(run, identity):
        return _not_yours(run_id)
    assert run is not None
    if not run.get("individual_report"):
        # A market run's rank-1 provider is the top COMPETITOR and its
        # entity_name is NULL, so _render_run would print a stranger's score
        # under the header "Unknown". Say what the run is instead.
        return (f"Not found: run {run_id} is a market report, not a single-organization "
                f"report — it scores a whole market and has no one score for an "
                f"organization. Open it in Pulse History for the ranking.")
    if run.get("ai_visibility_score") is None and not run.get("tier_scores"):
        return (f"Not found: run {run_id} has no score yet — it may still be finishing.")
    return _render_run(srv, identity, run_id, run)


@_tool
async def _pulse_compare(srv: Any, identity: PulseIdentity, organization_a: str, city_a: str,
                         state_a: str, organization_b: str, city_b: str, state_b: str,
                         specialty_a: str = "", specialty_b: str = "",
                         entity_type_a: str = "hospital", entity_type_b: str = "hospital") -> str:
    """Head-to-head report on two organizations. Counts 1 against the cap, not 2:
    the cap is a per-caller ceiling on tool calls, and splitting hairs here buys
    nothing."""
    name_a = _clean_org(srv, organization_a)
    name_b = _clean_org(srv, organization_b)
    state_a, state_b = _clean_state(state_a), _clean_state(state_b)
    city_a, city_b = _clean_city(srv, city_a), _clean_city(srv, city_b)
    if not city_a or not city_b:
        return "Refused: a city is required on both sides."
    for value in (entity_type_a, entity_type_b):
        if (value or "hospital").strip().lower() not in ("hospital", "practice"):
            return 'Refused: entity_type_a and entity_type_b must be "hospital" or "practice".'
    # Cleaned before the reservation: a refused argument must not cost a run.
    clean_specialty_a = _clean_specialty(srv, specialty_a)
    clean_specialty_b = _clean_specialty(srv, specialty_b)

    _sweep_mcp_jobs(srv)
    cap = mcp_usage.daily_cap()
    try:
        used = mcp_usage.reserve_run(identity.email, cap)
    except mcp_usage.DailyCapExceeded:
        return _cap_refusal(cap)

    job_id = srv._new_job(identity.account_role, identity.brand, identity.email)
    job = srv._jobs[job_id]
    job.update({"source": "mcp", "label": f"{name_a} vs {name_b}"})
    request = {
        "entity_a_name": name_a, "city_a": city_a, "state_a": state_a,
        "entity_b_name": name_b, "city_b": city_b, "state_b": state_b,
        "specialty_a": clean_specialty_a,
        "specialty_b": clean_specialty_b,
        "aggregate_a": True, "aggregate_b": True,
        "entity_type_a": (entity_type_a or "hospital").strip().lower(),
        "entity_type_b": (entity_type_b or "hospital").strip().lower(),
        "teaser_report": False,
        "force_rerun_a": False, "force_rerun_b": False, "override_today_lock": False,
    }

    outcome, last_phase = await _run_job(srv, job_id, srv._job_run_comparison, job_id, request)
    if outcome == "timeout":
        return _timeout_message(f"{name_a} vs {name_b}", last_phase, _run_timeout())
    if outcome != "done":
        return _error_message(job_id, job)

    comparison_id = (job.get("result") or {}).get("run_id")
    if not comparison_id:
        return "Refused: the comparison finished without saving a report. Nothing to read."
    return _render_comparison(srv, identity, str(comparison_id), used, cap)


def _render_comparison(srv: Any, identity: PulseIdentity, comparison_id: str,
                       used: int, cap: int) -> str:
    """Both sides of a comparison, read from comparison_runs and then from each
    side's own run row — ``job["result"]`` carries no scores for a comparison.

    ``_COMPARISON_SQL`` filters on ``id`` alone, unlike ``_TREND_SQL`` and
    ``_HISTORY_COMPARISON_SQL``, which both also filter on ``ran_by``. That
    makes this ownership check the only wall this row has, and it runs
    before either side is rendered: neither entity name nor score is the
    caller's to see if the comparison is not theirs."""
    from perception.scoring import grade_from_score

    con = _connect()
    try:
        row = con.execute(_COMPARISON_SQL, [comparison_id]).fetchone()
    finally:
        con.close()
    if not row:
        return (f"The comparison finished as {comparison_id} but its row is not readable yet.")
    run_id_a, run_id_b, entity_a, entity_b, pdf_path, ran_by, generated_at = row
    if not _owned({"ran_by": ran_by}, identity):
        return _not_yours(comparison_id)

    lines = [f"Comparison complete — {entity_a} vs {entity_b}",
             f"Runs used today: {used} of {cap}.",
             f"comparison {comparison_id}  ·  {generated_at}", ""]
    for label, side_id in ((entity_a, run_id_a), (entity_b, run_id_b)):
        lines.append(str(label))
        # A side run id can be null: create_comparison_run passes
        # getattr(result_x, "run_id", None). Print the name without a score
        # rather than failing the whole tool.
        run = _load_run(str(side_id)) if side_id else None
        if not run:
            lines += ["  no separate run row was saved for this side", ""]
            continue
        score = run.get("ai_visibility_score")
        code, band = grade_from_score(score if score is None else int(score))
        grade = run.get("overall_rating") or "—"
        lines.append(f"  Score {'—' if score is None else int(score)}  "
                     f"(Grade {grade}, {code} {band})")
        try:
            tiers = json.loads(run.get("tier_scores") or "{}")
        except (TypeError, ValueError):
            tiers = {}
        for key, tier_label in _tier_labels(run.get("weighting_profile")).items():
            value = tiers.get(key)
            lines.append(f"  {tier_label:<34}{'—' if value is None else round(float(value))}")
        lines.append("")

    if pdf_path:
        # account_role, never the mapped group — see _pdf_links for why.
        ttl = _pdf_ttl()
        token = srv._create_token(identity.account_role, email=identity.email,
                                  name=identity.name, brand=identity.brand,
                                  exp=int(time.time()) + ttl)
        lines += [f"Download (link expires in {ttl // 60} minutes):",
                  f"  {getattr(srv, 'APP_URL', '')}/api/compare/{comparison_id}/pdf?token={token}"]
    return "\n".join(lines)


@_tool
async def _pulse_network_report(srv: Any, identity: PulseIdentity, network_name: str,
                                hq_location: str = "", facility_type: str = "hospital",
                                facilities: Optional[list] = None, source_url: str = "") -> str:
    """Score a whole hospital network. Counts 1 against the cap. The longest of
    the run tools, so the timeout message names the facility count."""
    name = _clean_org(srv, network_name)
    if len(hq_location.strip()) > _MAX_HQ_LOCATION_CHARS:
        return (f"Refused: hq_location must be {_MAX_HQ_LOCATION_CHARS} characters or fewer. "
                f'It names a city and state, for example "Charlotte, NC".')
    hq = _clean_city(srv, hq_location)
    source = _clean_url(source_url, field="source_url")
    roster = _clean_facilities(srv, facilities)
    discovered_note = ""

    # Discovery is a real Claude call and, when GEMINI_API_KEY is set, a real
    # Gemini call as well (network_analyzer.discover_hospitals_by_name). Both
    # bill the shared keys, and both used to happen BEFORE the cap was consulted,
    # so a caller who had already spent their allowance could fire them an
    # unbounded number of times and get the cap refusal after the money was
    # gone. reserve_run stays where it is, below, so an oversized-roster refusal
    # still costs nothing.
    cap = mcp_usage.daily_cap()
    if not roster and mcp_usage.usage_today(identity.email) >= cap:
        return _cap_refusal(cap)

    loop = asyncio.get_running_loop()
    if not roster:
        # POST /api/network/analyze requires a roster and does not discover one;
        # discovery is the separate /api/network-discover route. Do it here so a
        # rep can name a network and see what was actually scored.
        from perception.network_analyzer import discover_hospitals_by_name
        try:
            data = await loop.run_in_executor(
                _pool(),
                lambda: discover_hospitals_by_name(name, hq,
                                                   (facility_type or "hospital").strip().lower()))
        except Exception as exc:
            return (f"Refused: the facility roster for \"{name}\" could not be discovered "
                    f"({type(exc).__name__}). Pass facilities explicitly.")
        # NOT run through _clean_facilities: this roster is Pulse's own output,
        # not caller input, and it legitimately carries `beds`.
        roster = list(data.get("facilities") or [])
        discovered_note = (f"Discovered {len(roster)} facilities for "
                           f"{data.get('network_canonical_name') or name} from Pulse's network "
                           f"discovery. {data.get('confidence_note') or ''}".strip())
    if not roster:
        return (f'Refused: no facilities could be found for "{name}". Pass facilities as a '
                f"list of {{name, city, state}} entries.")
    if len(roster) > _MAX_NETWORK_FACILITIES:
        return (f"Refused: {len(roster)} facilities is more than this tool will score in one "
                f"run (limit {_MAX_NETWORK_FACILITIES}). Split the network or pass a shorter "
                f"facilities list.")

    _sweep_mcp_jobs(srv)
    try:
        used = mcp_usage.reserve_run(identity.email, cap)
    except mcp_usage.DailyCapExceeded:
        return _cap_refusal(cap)

    job_id = srv._new_job(identity.account_role, identity.brand, identity.email)
    job = srv._jobs[job_id]
    job.update({"source": "mcp", "label": name})
    # ignore_cache is admin-only on his route; teaser, service_line_audit and
    # full_detail are opt-ins the MCP does not expose.
    outcome, last_phase = await _run_job(
        srv, job_id, srv._job_network_analyze, job_id, name, hq,
        source, roster, (facility_type or "hospital").strip().lower(),
        identity.brand, False, False, False, False)
    if outcome == "timeout":
        return _timeout_message(f"{name} ({len(roster)} facilities)", last_phase,
                                _run_timeout())
    if outcome != "done":
        return _error_message(job_id, job)

    result = job.get("result") or {}
    run_id = result.get("run_id")
    if not run_id:
        return "Refused: the network run finished without saving a report. Nothing to read."
    if result.get("ai_visibility_score") is None:
        result = _network_row(str(run_id)) or result

    lines = [f"Network report complete — {result.get('network_canonical_name') or name}",
             f"Runs used today: {used} of {cap}.",
             f"run {run_id}  ·  {result.get('total_hospitals') or len(roster)} facilities"
             + (f"  ·  {result.get('states_covered')} states" if result.get("states_covered") else ""),
             ""]
    if discovered_note:
        lines += [discovered_note, ""]
    score = result.get("ai_visibility_score")
    if score is None:
        lines.append("AI Visibility Score: not scored")
    else:
        lines.append(f"AI Visibility Score: {int(score)}  (Grade {result.get('grade') or '—'}"
                     + (f", {result['grade_band']}" if result.get("grade_band") else "") + ")")

    # account_role, never the mapped group — see _pdf_links for why.
    ttl = _pdf_ttl()
    token = srv._create_token(identity.account_role, email=identity.email, name=identity.name,
                              brand=identity.brand, exp=int(time.time()) + ttl)
    base = getattr(srv, "APP_URL", "")
    links = [f"  {label + ':':<14}{base}/api/network/{run_id}/{path}?token={token}"
             for label, key, path in (("Full report", "pdf_path", "pdf"),
                                      ("Teaser", "teaser_pdf_path", "teaser-pdf"),
                                      ("Full detail", "full_detail_pdf_path", "full-detail-pdf"))
             if result.get(key)]
    if links:
        lines += ["", f"Download (links expire in {ttl // 60} minutes):"] + links
    return "\n".join(lines)


def _network_row(run_id: str) -> Optional[dict]:
    """The persisted network run, for the fields ``job["result"]`` is missing."""
    columns = ("network_name", "ai_visibility_score", "grade", "total_hospitals",
               "generated_at", "ran_by", "pdf_path", "teaser_pdf_path", "full_detail_pdf_path")
    con = _connect()
    try:
        row = con.execute(_NETWORK_SQL, [run_id]).fetchone()
    finally:
        con.close()
    if not row:
        return None
    out = dict(zip(columns, row))
    out["run_id"] = run_id
    return out


@_tool
async def _pulse_history(srv: Any, identity: PulseIdentity, days: int = 45,
                         organization: str = "", limit: int = 25) -> str:
    """The caller's own runs, newest first. Free.

    Scoped on ``ran_by``, never role-wide. perception/db.py's own history helper
    filters on ``user_role`` instead, and our role mapping puts every AE, BDR and
    sales lead into the single string "salesteam", so a role-wide read would show
    one rep every other rep's runs — precisely the leak this endpoint exists to
    avoid. That is why this SQL is written here rather than reusing it; see
    docs/mcp.md section 6, which names both helpers."""
    from datetime import date, timedelta

    days = _clean_int(days, 45, 1, 365, field="days")
    limit = _clean_int(limit, 25, 1, 100, field="limit")
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    email = identity.email

    analysis_sql = _HISTORY_ANALYSIS_SQL
    params: list = [email, cutoff]
    if organization.strip():
        analysis_sql += " AND LOWER(entity_name) LIKE LOWER(?)"
        # The wildcard lives in the PARAMETER, never in the SQL string: _translate
        # doubles a literal '%' when params are present.
        params.append(f"%{organization.strip()}%")
    analysis_sql += " ORDER BY generated_at DESC, run_id DESC LIMIT ?"
    params.append(limit)

    con = _connect()
    try:
        analysis_rows = con.execute(analysis_sql, params).fetchall()
        network_rows = con.execute(
            _HISTORY_NETWORK_SQL + " ORDER BY generated_at DESC, run_id DESC LIMIT ?",
            [email, cutoff, limit]).fetchall()
        comparison_rows = con.execute(
            _HISTORY_COMPARISON_SQL + " ORDER BY generated_at DESC, id DESC LIMIT ?",
            [email, cutoff, limit]).fetchall()
    finally:
        con.close()

    entries: list[tuple[str, str]] = []
    for run_id, entity_name, location, generated_at, entity_type, specialty, pdf_path in analysis_rows:
        label = entity_name or location or "(unnamed)"
        detail = ", ".join(filter(None, [entity_type or "hospital", specialty or ""]))
        entries.append((str(generated_at), f"{generated_at}  {run_id}  {label} — {location}  "
                                           f"({detail}, {'has PDF' if pdf_path else 'no PDF'})"))
    for run_id, network_name, generated_at, facility_type, score in network_rows:
        entries.append((str(generated_at), f"{generated_at}  {run_id}  {network_name}  "
                                           f"(network of {facility_type or 'hospital'}, "
                                           f"score {score if score is not None else '—'})"))
    for cid, entity_a, entity_b, generated_at in comparison_rows:
        entries.append((str(generated_at), f"{generated_at}  {cid}  {entity_a} vs {entity_b}  "
                                           f"(comparison)"))

    if not entries:
        return f"No runs of yours in the last {days} days. pulse_run_report starts one."
    entries.sort(key=lambda e: e[0], reverse=True)
    header = f"Your last {min(len(entries), limit)} Pulse runs (of {days} days):"
    return "\n".join([header, ""] + [line for _key, line in entries[:limit]])


@_tool
async def _pulse_trend(srv: Any, identity: PulseIdentity, organization: str, city: str = "",
                       state: str = "") -> str:
    """How one organization's score has moved across the caller's own snapshots.

    Scoped the same way. perception/db.py's own entity-trend helper has no
    per-user filter at all — it returns every snapshot of that entity by anyone —
    so this SQL is written here instead."""
    name = _clean_org(srv, organization)
    state = _clean_state(state, required=False)
    city = _clean_city(srv, city)

    sql = _TREND_SQL
    params: list = [name, identity.email]
    if city:
        sql += " AND LOWER(a.location) LIKE LOWER(?)"
        params.append(f"%{city}%")
    sql += " ORDER BY a.generated_at ASC, a.run_id ASC"

    con = _connect()
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    if not rows:
        return (f'No snapshots of yours for "{name}". pulse_run_report starts one.')

    lines = [f"Score trend — {name}" + (f" ({city}, {state})" if city and state else ""), ""]
    scores: list[int] = []
    first_day = last_day = ""
    for run_id, generated_at, score, tier_scores, footprint, location, profile in rows:
        try:
            tiers = json.loads(tier_scores or "{}")
        except (TypeError, ValueError):
            tiers = {}
        try:
            front_door = (json.loads(footprint or "{}") or {}).get("front_door") or {}
        except (TypeError, ValueError):
            front_door = {}
        if score is not None:
            scores.append(int(score))
        first_day = first_day or str(generated_at)
        last_day = str(generated_at)
        lines.append(f"{generated_at}  run {run_id}  {location}")
        lines.append(f"  Score {'—' if score is None else int(score)}")
        for key, label in _tier_labels(profile).items():
            value = tiers.get(key)
            lines.append(f"    {label:<34}{'—' if value is None else round(float(value))}")
        rating, count = front_door.get("rating"), front_door.get("count")
        if rating is not None:
            lines.append(f"    {'Google front door':<34}{rating} ({count or 0:,} reviews)")
        lines.append("")

    if len(rows) < 2:
        lines.append("Only one snapshot of yours for this organization, so there is no trend yet.")
    elif len(scores) >= 2:
        delta = scores[-1] - scores[0]
        lines.append(f"Score moved {delta:+d} across {len(rows)} snapshots since {first_day}.")
    else:
        lines.append(f"{len(rows)} snapshots since {first_day}, but too few scored to show a move.")
    return "\n".join(lines)


@_tool
async def _pulse_content_draft(srv: Any, identity: PulseIdentity, run_id: str) -> str:
    """Write publication-ready remediation copy for a run's saved content
    findings and save it back. Costs one or more model calls; counts 1 against
    the daily cap."""
    run_id = (run_id or "").strip()
    if not run_id:
        return "Refused: run_id is required."
    run = _load_run(run_id)
    if not _owned(run, identity):
        return _not_yours(run_id)
    assert run is not None

    from perception.db import get_content_findings, save_content_findings
    saved = get_content_findings(run_id)
    findings = (saved or {}).get("findings") or []
    if not findings:
        return (f"Refused: run {run_id} has no saved content findings. Run pulse_run_report "
                f"for this organization (a Deep Diagnostic saves them), or call "
                f"pulse_content_check with attach_to_run_id={run_id}, then retry.")

    # The same set draft_findings itself computes (content_drafting.py) and the
    # same one his own _job_content_draft calls draftable_ct. With none of them
    # draftable, draft_findings returns {} having made ZERO model calls — so
    # reporting "the model call failed, try again" would be false and would send
    # the rep back to a call that fails identically forever, one cap unit at a
    # time. Checked before the reservation for exactly that reason.
    draftable = [f for f in findings
                 if (f.get("remediation_type") or "") and f.get("status") != "not_assessed"]
    if not draftable:
        return (f"Refused: run {run_id} has {len(findings)} saved content findings but none of "
                f"them are draftable — they are informational or not_assessed. Nothing to "
                f"write.")

    cap = mcp_usage.daily_cap()
    try:
        used = mcp_usage.reserve_run(identity.email, cap)
    except mcp_usage.DailyCapExceeded:
        return _cap_refusal(cap)

    snapshot = saved.get("source_snapshot") or {}
    facts = {"website_urls": snapshot.get("website_urls"), "specialty": None,
             "wikidata_qid": snapshot.get("wikidata_qid"),
             "wikipedia_article": snapshot.get("wikipedia_article")}
    from perception.content_drafting import draft_findings
    loop = asyncio.get_running_loop()
    drafts = await loop.run_in_executor(
        _pool(),
        lambda: draft_findings(run.get("entity_name") or "", run.get("location") or "",
                               run.get("entity_type") or "hospital", facts, findings))
    if not drafts:
        # Reached only when there WAS something draftable, which is his own
        # `draftable_ct and not drafts` condition: nothing saved, say so rather
        # than reporting a success with no content.
        return ("Refused: drafting produced no content — the model call failed or was "
                "truncated. Nothing was saved. Try again.")

    for finding in findings:
        if finding.get("finding_id") in drafts:
            finding["draft_content"] = drafts[finding["finding_id"]]
    save_content_findings(run_id, saved.get("norm_entity", ""), snapshot, findings,
                          saved.get("status", "verified"))

    lines = [f"Drafted {len(drafts)} of {len(findings)} findings for "
             f"{run.get('entity_name') or run_id}.",
             f"Runs used today: {used} of {cap}.", ""]
    for finding in findings:
        draft = finding.get("draft_content")
        if not draft:
            continue
        lines += [f"[{finding.get('finding_id')}] {finding.get('platform')} — "
                  f"{finding.get('remediation_type') or 'copy'}", str(draft), ""]
    lines += [
        "Every [VERIFY: ...] placeholder is a fact a human must confirm before publishing; "
        "the drafting prompt emits them deliberately and they are left exactly as written.",
        f"The drafts are saved against this run. To get them in a PDF, open the run in Pulse "
        f"and use the Content Analysis panel.",
    ]
    return "\n".join(lines)


# ── Registration ──────────────────────────────────────────────────────────────

def build_mcp(srv: Any) -> MCPServer:
    """Build the MCP server and register the nine tools.

    Each registered tool is a thin wrapper that supplies `srv` from this closure
    and the identity from the per-request ContextVar, so the tool functions
    themselves stay plain and directly testable. ``structured_output=False``
    keeps every result plain text — what the model reads — rather than a JSON
    envelope."""
    mcp = MCPServer("Pulse", instructions=_INSTRUCTIONS)

    async def call(impl: Callable[..., Any], **kwargs: Any) -> str:
        """Run one tool implementation for the current caller, supplying `srv`
        from this closure and the identity from the per-request ContextVar."""
        return await impl(srv, _current_identity(), **kwargs)

    @mcp.tool(name="pulse_find_entity", structured_output=False)
    async def pulse_find_entity(organization: str, city: str = "", state: str = "",
                                zip_code: str = "") -> str:
        """Find the Google listings that match an organization name, so the right
        one is confirmed before a report run is spent. Free."""
        return await call(_pulse_find_entity,
            organization=organization, city=city, state=state, zip_code=zip_code)

    @mcp.tool(name="pulse_content_check", structured_output=False)
    async def pulse_content_check(organization: str, city: str = "", state: str = "",
                                  entity_kind: str = "hospital", website_url: str = "",
                                  attach_to_run_id: str = "") -> str:
        """Check an organization's website, schema markup, llms.txt, robots.txt,
        Wikidata and Wikipedia. Code checks only, no model call, and it does not
        count against the daily cap — the best first tool. website_url, if you
        pass one, must be a public http(s) front door with no query string;
        leave it empty to let Pulse resolve the site from Google."""
        return await call(_pulse_content_check,
            organization=organization, city=city, state=state, entity_kind=entity_kind,
            website_url=website_url, attach_to_run_id=attach_to_run_id)

    @mcp.tool(name="pulse_run_report", structured_output=False)
    async def pulse_run_report(organization: str, city: str, state: str,
                               report_type: str = "hospital", specialty: str = "",
                               aggregate: bool = True, briefing: str = "") -> str:
        """Run a full Pulse report for one organization and return its score,
        pillars and PDF links. report_type is hospital, practice or fqhc. Blocks
        for several minutes and counts against the daily cap."""
        return await call(_pulse_run_report,
            organization=organization, city=city, state=state, report_type=report_type,
            specialty=specialty, aggregate=aggregate, briefing=briefing)

    @mcp.tool(name="pulse_get_report", structured_output=False)
    async def pulse_get_report(run_id: str) -> str:
        """Read a finished single-organization run of your own: AI Visibility
        Score, grade, the four pillars and short-lived PDF download links. Free.
        Market reports have no one score for an organization and are refused."""
        return await call(_pulse_get_report, run_id=run_id)

    @mcp.tool(name="pulse_compare", structured_output=False)
    async def pulse_compare(organization_a: str, city_a: str, state_a: str,
                            organization_b: str, city_b: str, state_b: str,
                            specialty_a: str = "", specialty_b: str = "",
                            entity_type_a: str = "hospital",
                            entity_type_b: str = "hospital") -> str:
        """Score two organizations head to head and return both sides with the
        comparison PDF. Blocks for several minutes and counts against the cap."""
        return await call(_pulse_compare,
            organization_a=organization_a, city_a=city_a, state_a=state_a,
            organization_b=organization_b, city_b=city_b, state_b=state_b,
            specialty_a=specialty_a, specialty_b=specialty_b,
            entity_type_a=entity_type_a, entity_type_b=entity_type_b)

    @mcp.tool(name="pulse_network_report", structured_output=False)
    async def pulse_network_report(network_name: str, hq_location: str = "",
                                   facility_type: str = "hospital",
                                   facilities: Optional[list] = None,
                                   source_url: str = "") -> str:
        """Score a whole hospital network. Pass facilities as {name, city, state}
        entries — those three keys and nothing else — or leave it empty to let
        Pulse discover the roster first. The longest-running tool; counts against
        the cap."""
        return await call(_pulse_network_report,
            network_name=network_name, hq_location=hq_location, facility_type=facility_type,
            facilities=facilities, source_url=source_url)

    @mcp.tool(name="pulse_history", structured_output=False)
    async def pulse_history(days: int = 45, organization: str = "", limit: int = 25) -> str:
        """List the Pulse runs you have started, newest first. Free."""
        return await call(_pulse_history,
            days=days, organization=organization, limit=limit)

    @mcp.tool(name="pulse_trend", structured_output=False)
    async def pulse_trend(organization: str, city: str = "", state: str = "") -> str:
        """Show how one organization's score has moved across your own snapshots.
        Free."""
        return await call(_pulse_trend,
            organization=organization, city=city, state=state)

    @mcp.tool(name="pulse_content_draft", structured_output=False)
    async def pulse_content_draft(run_id: str) -> str:
        """Write publication-ready remediation copy for a run's saved content
        findings and save it back to that run. Costs a model call and counts
        against the daily cap."""
        return await call(_pulse_content_draft, run_id=run_id)

    return mcp


# ── Mount ─────────────────────────────────────────────────────────────────────

class _AuthGate:
    """The MCP endpoint's authentication, as one ASGI middleware.

    Reads ``Authorization: Bearer <token>`` and NOTHING ELSE. His REST routes
    also accept ``?token=`` by design, for EventSource; a token in a URL lands in
    every access log and Referer between the client and Cloud Run, which is fine
    for a browser download link and not fine for the credential a connector
    presents on every call."""

    def __init__(self, app: Any, srv: Any) -> None:
        self.app = app
        self.srv = srv

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        try:
            # OFF THE EVENT LOOP. authenticate() does blocking I/O — a psycopg
            # round trip to Neon on every call, and on a JWKS refetch a
            # urllib.request.urlopen with a five-second timeout held under a
            # lock. This app has one event loop and it also serves every browser
            # user, so running that inline means one bogus JWT carrying an
            # unknown kid stalls the whole service for up to five seconds,
            # repeatable once per REFETCH_COOLDOWN_SECONDS, from an
            # unauthenticated caller. anyio's worker threads, not _pool(): that
            # pool is sized for report runs and would queue auth behind them.
            identity = await anyio.to_thread.run_sync(authenticate, token, self.srv)
        except PulseAuthError as exc:
            await self._refuse(scope, receive, send, exc.message, exc.status)
            return
        except Exception as exc:
            # Fail closed: anything that is not a PulseAuthError is a bug or an
            # outage, and either way the caller gets the generic refusal while
            # the type is logged for us.
            print(f"[pulse-mcp] authentication error: {type(exc).__name__}: {exc}")
            await self._refuse(scope, receive, send,
                               "Pulse could not verify that token right now.", 401)
            return
        reset = _IDENTITY.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            _IDENTITY.reset(reset)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, message: str,
                      status: int) -> None:
        """A refusal, as the status the reason actually deserves.

        401 means the token did not verify, and it carries the
        ``WWW-Authenticate`` challenge a connector follows to find where to
        authenticate (with ``resource_metadata`` only when OAuth is configured —
        otherwise there is nothing to point at).

        403 means the token verified and the answer is still no: no Pulse
        account, or a deactivated one. It carries NO challenge, because a
        connector that sees 401 plus a challenge re-runs the OAuth dance, and a
        rep with a valid RLDatix token and no Pulse account would sign in
        forever instead of reading the message."""
        if status == 401:
            challenge = 'Bearer realm="pulse"'
            if oauth_config() is not None:
                challenge += f', resource_metadata="{_metadata_url(self.srv)}"'
            headers = {"WWW-Authenticate": challenge}
            error = "invalid_token"
        else:
            headers = {}
            error = "access_denied"
        response = JSONResponse(
            {"error": error, "error_description": message},
            status_code=status,
            headers=headers,
        )
        await response(scope, receive, send)


def _metadata_url(srv: Any) -> str:
    """The RFC 9728 metadata URL a client derives from the resource it calls."""
    return f"{getattr(srv, 'APP_URL', '')}/.well-known/oauth-protected-resource/mcp"


def _protected_resource_document() -> dict:
    """The RFC 9728 document: which authorization server protects this resource,
    and that the token goes in a header."""
    config = oauth_config()
    issuer, audience, _jwks = config if config else ("", "", "")
    return {"resource": audience,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"]}


async def _protected_resource(request: Any) -> JSONResponse:
    """Serve the RFC 9728 document, unauthenticated — a client has to be able to
    read it before it has any token at all."""
    return JSONResponse(_protected_resource_document())


def mount_mcp(app: Any, srv: Any) -> None:
    """Attach the MCP endpoint and its discovery routes to his FastAPI app.

    Called from ``server.py`` only when PULSE_MCP_ENABLED=1, and from a point
    before the SPA catch-all, because a route registered after
    ``@app.get("/{full_path:path}")`` is a route the catch-all swallows."""
    mcp = build_mcp(srv)
    _pool()          # build the thread pool now rather than inside the first run

    http_app = mcp.streamable_http_app(
        # Exactly /mcp, not /mcp/mcp: the sub-app routes on the FULL path because
        # it is attached as a Route (which does not strip a prefix) rather than a
        # Mount (which does, and would answer only on /mcp/).
        streamable_http_path="/mcp",
        # A stateful session lives in ONE instance's session manager, and this
        # service runs --min-instances=1 --max-instances=10; a follow-up request
        # routed elsewhere would 404. --session-affinity is a browser-cookie
        # mechanism an MCP client does not carry, so every request is made
        # self-contained instead. json_response collapses the per-request SSE
        # stream into the single JSON body a stateless request wants.
        stateless_http=True,
        json_response=True,
        # The SDK's DNS-rebinding guard defaults to a localhost allowlist, which
        # would 421 every request to the deployed hostname. Pulse is a public
        # service reached by a server-side client, not a localhost daemon, and
        # his other routes do no Host validation either.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    # Starlette does NOT run a mounted sub-app's lifespan, and the Streamable
    # HTTP session manager raises "Task group is not initialized" on the first
    # request if its run() context was never entered. His app has no lifespan, so
    # chain one on rather than replacing it — a lifespan he adds later still runs.
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scope_app: Any):
        async with previous(scope_app):
            async with mcp.session_manager.run():
                yield

    app.router.lifespan_context = lifespan

    routes = [Route("/mcp", endpoint=_AuthGate(http_app, srv),
                    methods=["GET", "POST", "DELETE"])]
    if oauth_config() is not None:
        # Build the verifier NOW, while server.py is still importing. Its
        # constructor validates the issuer, the audience and the JWKS scheme
        # without any network (the fetch stays lazy inside verify()), so a
        # typo'd PULSE_MCP_OAUTH_JWKS_URL is a revision that fails to start
        # rather than one that deploys green and then answers every request with
        # the generic "could not verify that token right now", with the real
        # cause visible only in the container log.
        verifier()
        # Both forms: a strict client derives the path-suffixed form from the
        # resource it is calling and never falls back to the root form, while
        # Claude's connector tries the suffixed form first and then the root.
        routes += [
            Route("/.well-known/oauth-protected-resource", endpoint=_protected_resource,
                  methods=["GET"]),
            Route("/.well-known/oauth-protected-resource/mcp", endpoint=_protected_resource,
                  methods=["GET"]),
        ]
    app.router.routes.extend(routes)
    print(f"[pulse-mcp] mounted at /mcp with {len(_TOOL_NAMES)} tools "
          f"(OAuth {'configured' if oauth_config() else 'not configured'})")
