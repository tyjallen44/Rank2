"""Per-run cost metering.

Every Claude call (create + stream, incl. beta), every Gemini call and every Google Places
request made while a job runs is attributed to that job and priced with the table below.
Attribution is by thread: a job calls begin(job_id) in its worker thread; ThreadPoolExecutor
submissions inherit the parent's job so parallel lookups still count. Prices are USD and can
be overridden with the PULSE_PRICES_JSON env var (same keys). Estimates, not invoices.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

# USD per million tokens (input, output, cache_write, cache_read); web search per call.
PRICES: dict = {
    "claude": {
        "claude-opus-4-8": (5.0, 25.0, 6.25, 0.5),
        "claude-opus-5": (5.0, 25.0, 6.25, 0.5),
        "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.3),
        "claude-sonnet-5": (3.0, 15.0, 3.75, 0.3),
        "claude-haiku-4-5-20251001": (1.0, 5.0, 1.25, 0.1),
        "_default": (5.0, 25.0, 6.25, 0.5),
    },
    "web_search_per_call": 0.01,          # Anthropic web search: $10 per 1,000 searches
    "gemini": {"gemini-2.5-flash": (0.30, 2.50), "_default": (0.30, 2.50)},
    "gemini_grounding_per_query": 0.035,   # Google Search grounding (2.5 models $35/1k; 3.x $14/1k)
    "openai": {"gpt-5-mini": (0.25, 2.00), "gpt-5": (1.25, 10.0), "_default": (0.25, 2.00)},
    "openai_web_search_per_call": 0.01,
    "places": {"text_search": 0.032, "nearby": 0.032, "details": 0.017, "other": 0.017},
}
try:
    _ov = json.loads(os.environ.get("PULSE_PRICES_JSON", "") or "{}")
    for k, v in _ov.items():
        if isinstance(v, dict) and isinstance(PRICES.get(k), dict):
            PRICES[k].update(v)
        else:
            PRICES[k] = v
except Exception:
    pass

_tl = threading.local()
_lock = threading.Lock()
_active: dict = {}          # job_id -> accumulator


def _new_acc(job_id: str, kind: str = "") -> dict:
    return {"job_id": job_id, "kind": kind, "started": time.time(), "calls": 0, "input_tokens": 0,
            "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "web_searches": 0,
            "gemini_calls": 0, "gemini_tokens": 0, "places_calls": 0, "cost_usd": 0.0,
            "by_model": {}, "places": {}}


def begin(job_id: str, kind: str = "") -> None:
    with _lock:
        acc = _active.get(job_id) or _new_acc(job_id, kind)
        if kind and not acc.get("kind"):
            acc["kind"] = kind
        _active[job_id] = acc
    _tl.job_id = job_id


def current_job() -> Optional[str]:
    return getattr(_tl, "job_id", None)


def snapshot(job_id: Optional[str] = None) -> Optional[dict]:
    jid = job_id or current_job()
    with _lock:
        acc = _active.get(jid) if jid else None
        return dict(acc) if acc else None


def finish(job_id: str) -> Optional[dict]:
    with _lock:
        acc = _active.pop(job_id, None)
    if getattr(_tl, "job_id", None) == job_id:
        _tl.job_id = None
    if acc:
        acc["seconds"] = round(time.time() - acc["started"], 1)
        acc["cost_usd"] = round(acc["cost_usd"], 4)
    return acc


def _acc() -> Optional[dict]:
    jid = getattr(_tl, "job_id", None)
    if not jid:
        return None
    with _lock:
        return _active.get(jid)


def record_claude(model: str, usage) -> None:
    acc = _acc()
    if acc is None or usage is None:
        return
    g = lambda k: int(getattr(usage, k, None) or (usage.get(k) if isinstance(usage, dict) else 0) or 0)
    inp, out = g("input_tokens"), g("output_tokens")
    cw, cr = g("cache_creation_input_tokens"), g("cache_read_input_tokens")
    ws = 0
    stu = getattr(usage, "server_tool_use", None) or (usage.get("server_tool_use") if isinstance(usage, dict) else None)
    if stu is not None:
        ws = int(getattr(stu, "web_search_requests", None) or (stu.get("web_search_requests") if isinstance(stu, dict) else 0) or 0)
    p = PRICES["claude"].get(model) or next((v for k, v in PRICES["claude"].items() if model and model.startswith(k)), None) or PRICES["claude"]["_default"]
    cost = (inp * p[0] + out * p[1] + cw * p[2] + cr * p[3]) / 1e6 + ws * PRICES["web_search_per_call"]
    with _lock:
        acc["calls"] += 1; acc["input_tokens"] += inp; acc["output_tokens"] += out
        acc["cache_write_tokens"] += cw; acc["cache_read_tokens"] += cr; acc["web_searches"] += ws
        acc["cost_usd"] += cost
        m = acc["by_model"].setdefault(model or "unknown", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "web_searches": 0, "cost_usd": 0.0})
        m["calls"] += 1; m["input_tokens"] += inp; m["output_tokens"] += out; m["web_searches"] += ws; m["cost_usd"] = round(m["cost_usd"] + cost, 4)


def record_gemini(model: str, prompt_tokens: int = 0, output_tokens: int = 0) -> None:
    acc = _acc()
    if acc is None:
        return
    p = PRICES["gemini"].get(model) or PRICES["gemini"]["_default"]
    cost = (prompt_tokens * p[0] + output_tokens * p[1]) / 1e6
    with _lock:
        acc["gemini_calls"] += 1; acc["gemini_tokens"] += prompt_tokens + output_tokens; acc["cost_usd"] += cost


def record_openai(model: str, input_tokens: int = 0, output_tokens: int = 0, web_searches: int = 0) -> None:
    acc = _acc()
    if acc is None:
        return
    p = PRICES["openai"].get(model) or PRICES["openai"]["_default"]
    cost = (input_tokens * p[0] + output_tokens * p[1]) / 1e6 + web_searches * PRICES["openai_web_search_per_call"]
    with _lock:
        acc["calls"] += 1; acc["input_tokens"] += input_tokens; acc["output_tokens"] += output_tokens
        acc["web_searches"] += web_searches; acc["cost_usd"] += cost
        m = acc["by_model"].setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "web_searches": 0, "cost_usd": 0.0})
        m["calls"] += 1; m["input_tokens"] += input_tokens; m["output_tokens"] += output_tokens; m["web_searches"] += web_searches; m["cost_usd"] = round(m["cost_usd"] + cost, 4)


def record_gemini_grounding(model: str) -> None:
    acc = _acc()
    if acc is None:
        return
    with _lock:
        acc["cost_usd"] += PRICES["gemini_grounding_per_query"]


def record_places(kind: str = "other") -> None:
    acc = _acc()
    if acc is None:
        return
    cost = PRICES["places"].get(kind, PRICES["places"]["other"])
    with _lock:
        acc["places_calls"] += 1; acc["places"][kind] = acc["places"].get(kind, 0) + 1; acc["cost_usd"] += cost


# ── Hooks ─────────────────────────────────────────────────────────────────────
_installed = False


def install() -> None:
    """Patch the Anthropic SDK, httpx (Places + Gemini) and ThreadPoolExecutor once."""
    global _installed
    if _installed:
        return
    _installed = True
    # 1. Anthropic messages.create / beta.messages.create
    try:
        import anthropic.resources.messages as _m
        _orig_create = _m.Messages.create

        def _create(self, *a, **kw):
            resp = _orig_create(self, *a, **kw)
            try:
                record_claude(getattr(resp, "model", kw.get("model", "")), getattr(resp, "usage", None))
            except Exception:
                pass
            return resp
        _m.Messages.create = _create
        # streaming: record from the final snapshot when the context closes. Dunder
        # methods are looked up on the type, so wrap the manager in a proxy class.
        _orig_stream = _m.Messages.stream

        class _StreamProxy:
            def __init__(self, mgr, model):
                self._mgr, self._model, self._st = mgr, model, None

            def __enter__(self):
                self._st = self._mgr.__enter__()
                return self._st

            def __exit__(self, *ea):
                try:
                    snap = getattr(self._st, "current_message_snapshot", None) if self._st is not None else None
                    if snap is not None:
                        record_claude(getattr(snap, "model", self._model), getattr(snap, "usage", None))
                except Exception:
                    pass
                return self._mgr.__exit__(*ea)

            def __getattr__(self, name):
                return getattr(self._mgr, name)

        def _stream(self, *a, **kw):
            return _StreamProxy(_orig_stream(self, *a, **kw), kw.get("model", ""))
        _m.Messages.stream = _stream
    except Exception as exc:
        print(f"[cost] anthropic hook failed: {exc}")
    try:
        import anthropic.resources.beta.messages as _bm
        _orig_bcreate = _bm.Messages.create

        def _bcreate(self, *a, **kw):
            resp = _orig_bcreate(self, *a, **kw)
            try:
                record_claude(getattr(resp, "model", kw.get("model", "")), getattr(resp, "usage", None))
            except Exception:
                pass
            return resp
        _bm.Messages.create = _bcreate
    except Exception:
        pass
    # 2. httpx: Google Places + Gemini (URL-classified)
    try:
        import httpx
        _orig_post, _orig_get = httpx.post, httpx.get

        def _classify(url: str) -> Optional[str]:
            u = str(url)
            if "places.googleapis.com" in u:
                if "searchText" in u: return "places:text_search"
                if "searchNearby" in u: return "places:nearby"
                return "places:details"
            if "generativelanguage.googleapis.com" in u:
                return "gemini"
            return None

        def _after(kind, resp, url):
            try:
                if kind == "gemini":
                    um = (resp.json() or {}).get("usageMetadata", {}) if resp is not None else {}
                    model = "gemini-2.5-flash" if "gemini-2.5-flash" in str(url) else "_default"
                    record_gemini(model, int(um.get("promptTokenCount") or 0), int(um.get("candidatesTokenCount") or 0))
                elif kind:
                    record_places(kind.split(":", 1)[1])
            except Exception:
                pass

        def _post(url, *a, **kw):
            kind = _classify(url); resp = _orig_post(url, *a, **kw); _after(kind, resp, url); return resp

        def _get(url, *a, **kw):
            kind = _classify(url); resp = _orig_get(url, *a, **kw); _after(kind, resp, url); return resp
        httpx.post, httpx.get = _post, _get
    except Exception as exc:
        print(f"[cost] httpx hook failed: {exc}")
    # 3. ThreadPoolExecutor: child threads inherit the submitting thread's job
    try:
        import concurrent.futures as _cf
        _orig_submit = _cf.ThreadPoolExecutor.submit

        def _submit(self, fn, *a, **kw):
            jid = getattr(_tl, "job_id", None)
            if jid is None:
                return _orig_submit(self, fn, *a, **kw)

            def _wrapped(*aa, **kk):
                _tl.job_id = jid
                try:
                    return fn(*aa, **kk)
                finally:
                    _tl.job_id = None
            return _orig_submit(self, _wrapped, *a, **kw)
        _cf.ThreadPoolExecutor.submit = _submit
    except Exception as exc:
        print(f"[cost] executor hook failed: {exc}")


def summary_line(acc: Optional[dict]) -> str:
    if not acc:
        return ""
    bits = [f"{acc.get('calls', 0)} Claude call{'s' if acc.get('calls', 0) != 1 else ''}"]
    if acc.get("web_searches"): bits.append(f"{acc['web_searches']} web search{'es' if acc['web_searches'] != 1 else ''}")
    if acc.get("places_calls"): bits.append(f"{acc['places_calls']} Places lookup{'s' if acc['places_calls'] != 1 else ''}")
    if acc.get("gemini_calls"): bits.append(f"{acc['gemini_calls']} Gemini call{'s' if acc['gemini_calls'] != 1 else ''}")
    return f"${acc.get('cost_usd', 0):.2f} · " + " · ".join(bits)
