"""Provider health — is each outside API (Claude, ChatGPT, Gemini, Google Places) answering us?

Every call the app makes is recorded (ok / error, key scrubbed). When a provider fails half of its
calls in the last 30 minutes, or a spot check gets no answers from it at all, it is marked DOWN:
every administrator is emailed once, a banner shows in the app, and starting a report is refused
with a message that names the contact address — until an administrator re-checks (a probe call to
each provider) or chooses to allow runs without that provider ("bypassed"). A daily probe from
Cloud Scheduler catches an outage before the day's runs do.

    record(provider, ok, error)       from spotcheck / places / job errors
    blocked_message()                 None, or the sentence to show a user who tries to start a run
    probe_all(by)                     one tiny call per configured provider; sets ok / down
    bypass(provider, by) / require(provider, by)
    status()                          rows for Operations and the banner
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta
from typing import Optional

PROVIDERS = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini", "places": "Google Places"}
CONTACT_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "ty.allen@rldatix.com")
WINDOW_MIN = 30          # failure window
MIN_CALLS = 4            # at least this many calls in the window before judging
FAIL_RATE = 0.5          # ≥ half failed → down
KEEP_DAYS = 7            # provider_calls retention
_KEY_RE = re.compile(r"(key=|api[_-]?key=|Bearer\s+|sk-[A-Za-z0-9_-]{6})[^&'\"\s]*", re.I)
_lock = threading.Lock()


def scrub(text: Optional[str], n: int = 300) -> str:
    """Error text without API keys (httpx puts the Gemini URL — with ?key= — in its message)."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return _KEY_RE.sub(lambda m: m.group(1) + "…", s)[:n]


def _now() -> datetime:
    return datetime.utcnow()


# ── Storage ──────────────────────────────────────────────────────────────────
class DbStore:
    """Two tables in the app database (shared by every Cloud Run instance and job task)."""

    def _con(self):
        from .db import get_connection
        con = get_connection()
        con.execute("""CREATE TABLE IF NOT EXISTS provider_calls (
            ts TIMESTAMP NOT NULL, provider VARCHAR NOT NULL, ok BOOLEAN NOT NULL, error VARCHAR)""")
        con.execute("""CREATE TABLE IF NOT EXISTS provider_status (
            provider VARCHAR PRIMARY KEY, status VARCHAR NOT NULL, since TIMESTAMP, last_error VARCHAR,
            last_error_at TIMESTAMP, last_ok_at TIMESTAMP, changed_by VARCHAR, alerted_at TIMESTAMP)""")
        return con

    def add_call(self, provider: str, ok: bool, error: Optional[str]) -> None:
        con = self._con()
        try:
            con.execute("INSERT INTO provider_calls (ts, provider, ok, error) VALUES (?, ?, ?, ?)", [_now(), provider, bool(ok), error])
            con.execute("INSERT INTO provider_status (provider, status) VALUES (?, 'ok') ON CONFLICT (provider) DO NOTHING", [provider])
            if ok:
                con.execute("UPDATE provider_status SET last_ok_at = ? WHERE provider = ?", [_now(), provider])
            else:
                con.execute("UPDATE provider_status SET last_error = ?, last_error_at = ? WHERE provider = ?", [error, _now(), provider])
            if _now().minute == 0:   # light pruning, once an hour at most
                con.execute("DELETE FROM provider_calls WHERE ts < ?", [_now() - timedelta(days=KEEP_DAYS)])
        finally:
            con.close()

    def window_counts(self, provider: str, minutes: int) -> tuple[int, int]:
        con = self._con()
        try:
            con.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN ok THEN 0 ELSE 1 END), 0) FROM provider_calls WHERE provider = ? AND ts >= ?",
                        [provider, _now() - timedelta(minutes=minutes)])
            n, f = con.fetchone()
            return int(n or 0), int(f or 0)
        finally:
            con.close()

    def get_status(self, provider: str) -> dict:
        con = self._con()
        try:
            con.execute("SELECT provider, status, since, last_error, last_error_at, last_ok_at, changed_by, alerted_at FROM provider_status WHERE provider = ?", [provider])
            r = con.fetchone()
        finally:
            con.close()
        cols = ["provider", "status", "since", "last_error", "last_error_at", "last_ok_at", "changed_by", "alerted_at"]
        return dict(zip(cols, r)) if r else {"provider": provider, "status": "ok", "since": None, "last_error": None,
                                             "last_error_at": None, "last_ok_at": None, "changed_by": None, "alerted_at": None}

    def set_status(self, provider: str, status: str, by: str, alerted: bool) -> None:
        con = self._con()
        try:
            con.execute("INSERT INTO provider_status (provider, status) VALUES (?, 'ok') ON CONFLICT (provider) DO NOTHING", [provider])
            con.execute("UPDATE provider_status SET status = ?, since = ?, changed_by = ?" + (", alerted_at = ?" if alerted else "") + " WHERE provider = ?",
                        [status, _now(), by] + ([_now()] if alerted else []) + [provider])
        finally:
            con.close()

    def all_status(self) -> list[dict]:
        return [self.get_status(p) for p in PROVIDERS]


class MemStore:
    """In-memory twin of DbStore (tests, and a fallback if the database is unreachable)."""

    def __init__(self):
        self.calls: list[tuple[datetime, str, bool, Optional[str]]] = []
        self.status: dict[str, dict] = {}

    def _row(self, p):
        return self.status.setdefault(p, {"provider": p, "status": "ok", "since": None, "last_error": None,
                                          "last_error_at": None, "last_ok_at": None, "changed_by": None, "alerted_at": None})

    def add_call(self, provider, ok, error):
        self.calls.append((_now(), provider, bool(ok), error))
        r = self._row(provider)
        if ok:
            r["last_ok_at"] = _now()
        else:
            r["last_error"], r["last_error_at"] = error, _now()

    def window_counts(self, provider, minutes):
        cut = _now() - timedelta(minutes=minutes)
        rows = [c for c in self.calls if c[1] == provider and c[0] >= cut]
        return len(rows), sum(1 for c in rows if not c[2])

    def get_status(self, provider):
        return dict(self._row(provider))

    def set_status(self, provider, status, by, alerted):
        r = self._row(provider)
        r.update(status=status, since=_now(), changed_by=by)
        if alerted:
            r["alerted_at"] = _now()

    def all_status(self):
        return [self.get_status(p) for p in PROVIDERS]


store = DbStore()


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as exc:          # health bookkeeping must never break a report
        print(f"[providers] {fn.__name__} failed: {type(exc).__name__}: {scrub(exc, 160)}", flush=True)
        return default


# ── Recording and judging ────────────────────────────────────────────────────
def configured(provider: str) -> bool:
    env = {"claude": "ANTHROPIC_API_KEY", "chatgpt": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "places": "GOOGLE_PLACES_API_KEY"}[provider]
    return bool((os.environ.get(env) or "").strip())


def record(provider: str, ok: bool, error: Optional[str] = None) -> None:
    """Record one call. A failure re-judges the provider against the 30-minute window."""
    if provider not in PROVIDERS:
        return
    err = scrub(error) if not ok else None
    _safe(store.add_call, provider, ok, err)
    if not ok:
        n, f = _safe(store.window_counts, provider, WINDOW_MIN, default=(0, 0))
        if n >= MIN_CALLS and f / n >= FAIL_RATE:
            mark_down(provider, err or "repeated failures", by="monitor")


def mark_down(provider: str, error: str, by: str = "monitor") -> bool:
    """Set DOWN and alert the administrators once per outage. Bypassed providers stay bypassed."""
    with _lock:
        cur = _safe(store.get_status, provider, default={"status": "ok"})
        if cur.get("status") in ("down", "bypassed"):
            return False
        _safe(store.set_status, provider, "down", by, True)
    print(f"[providers] {PROVIDERS[provider]} marked DOWN by {by}: {scrub(error, 200)}", flush=True)
    _alert(provider, "down", scrub(error))
    return True


def mark_ok(provider: str, by: str = "probe") -> bool:
    cur = _safe(store.get_status, provider, default={"status": "ok"})
    if cur.get("status") == "ok":
        return False
    was = cur.get("status")
    _safe(store.set_status, provider, "ok", by, True)
    print(f"[providers] {PROVIDERS[provider]} back to OK by {by}", flush=True)
    if was == "down":
        _alert(provider, "recovered", "")
    return True


def bypass(provider: str, by: str) -> None:
    """Administrator's choice: run reports without this provider until it is required again."""
    _safe(store.set_status, provider, "bypassed", by, False)


def active(provider: str) -> bool:
    """Should a run call this provider? Configured and not bypassed / down."""
    if not configured(provider):
        return False
    return (_safe(store.get_status, provider, default={"status": "ok"}) or {}).get("status") == "ok"


def status() -> list[dict]:
    """One row per provider for Operations and the banner (24-hour call counts included)."""
    out = []
    for p, label in PROVIDERS.items():
        r = _safe(store.get_status, p, default={"provider": p, "status": "ok"}) or {}
        n, f = _safe(store.window_counts, p, 24 * 60, default=(0, 0))
        out.append({"provider": p, "label": label, "configured": configured(p), "status": r.get("status", "ok"),
                    "since": _iso(r.get("since")), "last_ok_at": _iso(r.get("last_ok_at")), "last_error_at": _iso(r.get("last_error_at")),
                    "last_error": r.get("last_error") or "", "changed_by": r.get("changed_by") or "", "calls_24h": n, "failures_24h": f})
    return out


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else str(v)


def blocked() -> list[dict]:
    return [r for r in status() if r["status"] == "down"]

def blocked_message() -> Optional[str]:
    """The sentence a user sees when a run is refused; None when everything is up or bypassed."""
    rows = blocked()
    if not rows:
        return None
    names = " and ".join(r["label"] for r in rows)
    first = rows[0]
    why = f" ({first['last_error'][:140]})" if first.get("last_error") else ""
    since = f" since {first['since'][:16].replace('T', ' ')} UTC" if first.get("since") else ""
    return (f"Reports are paused: {names} {'is' if len(rows) == 1 else 'are'} not responding{why}{since}. "
            f"The administrators have been notified; for help contact {CONTACT_EMAIL}.")


# ── Alerts ───────────────────────────────────────────────────────────────────
def admin_emails() -> list[str]:
    try:
        from .auth import list_users
        out = [u["email"] for u in list_users() if u.get("role") == "admin" and u.get("is_active", True) and u.get("email")]
    except Exception:
        out = []
    if CONTACT_EMAIL and CONTACT_EMAIL not in out:
        out.append(CONTACT_EMAIL)
    return out


def _alert(provider: str, kind: str, error: str) -> None:
    def _go():
        try:
            from .email_utils import send_provider_alert
            for em in admin_emails():
                try:
                    send_provider_alert(em, PROVIDERS[provider], kind, error)
                except Exception as exc:
                    print(f"[providers] alert to {em} failed: {type(exc).__name__}: {exc}", flush=True)
        except Exception as exc:
            print(f"[providers] alert failed: {type(exc).__name__}: {exc}", flush=True)
    threading.Thread(target=_go, daemon=True).start()


# ── Probes ───────────────────────────────────────────────────────────────────
def _probe_claude() -> None:
    import anthropic
    anthropic.Anthropic().messages.create(model="claude-haiku-4-5-20251001", max_tokens=5, messages=[{"role": "user", "content": "Say OK."}])


def _probe_chatgpt() -> None:
    from openai import OpenAI
    OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip()).responses.create(model=os.environ.get("SPOTCHECK_OPENAI_MODEL", "gpt-5-mini"), input="Say OK.", max_output_tokens=16)


def _probe_gemini() -> None:
    import httpx
    model = os.environ.get("SPOTCHECK_GEMINI_MODEL", "gemini-3.6-flash")
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY'].strip()}",
                   json={"contents": [{"parts": [{"text": "Say OK."}]}], "tools": [{"google_search": {}}], "generationConfig": {"maxOutputTokens": 20}}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")


def _probe_places() -> None:
    import httpx
    r = httpx.post("https://places.googleapis.com/v1/places:searchText",
                   headers={"Content-Type": "application/json", "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"].strip(), "X-Goog-FieldMask": "places.displayName"},
                   json={"textQuery": "hospital in Memphis TN", "pageSize": 1}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")


_PROBES = {"claude": _probe_claude, "chatgpt": _probe_chatgpt, "gemini": _probe_gemini, "places": _probe_places}


def probe(provider: str, by: str = "probe") -> dict:
    """One tiny call. Success → ok (recovery emailed if it was down); failure → down (alert emailed once)."""
    if not configured(provider):
        return {"provider": provider, "ok": None, "error": "not configured"}
    try:
        _PROBES[provider]()
        record(provider, True)
        mark_ok(provider, by)
        return {"provider": provider, "ok": True}
    except Exception as exc:
        err = scrub(f"{type(exc).__name__}: {exc}")
        record(provider, False, err)
        mark_down(provider, err, by)
        return {"provider": provider, "ok": False, "error": err}


def probe_all(by: str = "probe", only_bypassed_too: bool = True) -> list[dict]:
    """Probe every configured provider in parallel (a few seconds). Bypassed providers are probed too but
    stay bypassed unless they answer — then they return to ok."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(lambda p: probe(p, by), [p for p in PROVIDERS if configured(p)]))


def require(provider: str, by: str) -> dict:
    """Administrator ends a bypass: the provider is probed and becomes ok or down accordingly."""
    _safe(store.set_status, provider, "ok", by, False)
    return probe(provider, by)
