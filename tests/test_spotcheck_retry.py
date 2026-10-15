"""Spot-check assistants back off on rate limits instead of recording an error (the Oct 4–6 Gemini 429 burst)."""
import types

import perception.spotcheck as sc


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_gemini_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls, sleeps = [], []
    ok = {"candidates": [{"content": {"parts": [{"text": "OrthoSouth is well regarded."}]}, "groundingMetadata": {}}]}
    seq = [_Resp(429, {"error": "rate"}, {"retry-after": "2"}), _Resp(429, {"error": "rate"}), _Resp(200, ok)]
    fake_httpx = types.SimpleNamespace(post=lambda url, json, timeout: (calls.append(url), seq.pop(0))[1])
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)
    monkeypatch.setattr(sc.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(sc._cost, "record_gemini_grounding", lambda m: None)
    r = sc.run_gemini("best orthopedic practice in Memphis")
    assert r["assistant"] == "Gemini" and "OrthoSouth" in r["text"]
    assert len(calls) == 3
    assert sleeps[0] == 2.0          # honoured Retry-After
    assert 4.0 <= sleeps[1] <= 5.0   # exponential backoff with jitter


def test_gemini_gives_up_after_retries(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake_httpx = types.SimpleNamespace(post=lambda url, json, timeout: _Resp(429, {"error": "rate"}))
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    try:
        sc.run_gemini("q")
    except RuntimeError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("expected the final 429 to raise")


def test_retry_wait_bounds():
    assert sc._retry_wait(0, "90") == 60.0
    assert sc._retry_wait(0, "garbage") >= 2.0
    assert sc._retry_wait(5, None) <= 31.0
