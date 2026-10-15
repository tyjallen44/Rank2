"""Provider health: judging, alert-once, bypass, the user-facing message, key scrubbing."""
import perception.provider_health as ph


def _fresh(monkeypatch):
    st = ph.MemStore()
    monkeypatch.setattr(ph, "store", st)
    sent = []
    monkeypatch.setattr(ph, "_alert", lambda p, kind, err: sent.append((p, kind)))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    return st, sent


def test_scrub_removes_keys():
    s = ph.scrub("429 for url 'https://x.googleapis.com/v1beta/models/g:generateContent?key=AQ.Ab8RN6K1J_CDlhnO' and Bearer sk-proj-abcdefgh")
    assert "AQ.Ab8" not in s and "sk-proj-abcdefgh" not in s and "key=…" in s


def test_half_failures_in_window_marks_down_and_alerts_once(monkeypatch):
    st, sent = _fresh(monkeypatch)
    for _ in range(3):
        ph.record("gemini", False, "429 Too Many Requests")
    assert st.get_status("gemini")["status"] == "ok"           # fewer than MIN_CALLS: not judged yet
    ph.record("gemini", False, "429 Too Many Requests")
    assert st.get_status("gemini")["status"] == "down"
    ph.record("gemini", False, "429 Too Many Requests")
    assert sent == [("gemini", "down")]                         # one email per outage, not per failure
    assert ph.blocked_message().startswith("Reports are paused: Gemini is not responding")
    assert "ty.allen@rldatix.com" in ph.blocked_message()


def test_mostly_successes_stay_ok(monkeypatch):
    st, sent = _fresh(monkeypatch)
    for _ in range(10):
        ph.record("chatgpt", True)
    for _ in range(2):
        ph.record("chatgpt", False, "rate")                       # 2 of 12 in the window failed
    assert st.get_status("chatgpt")["status"] == "ok" and sent == [] and ph.blocked_message() is None


def test_bypass_lets_runs_continue_and_skips_provider(monkeypatch):
    st, sent = _fresh(monkeypatch)
    ph.mark_down("chatgpt", "You have no credits remaining", by="spotcheck")
    assert ph.blocked_message() and not ph.active("chatgpt")
    ph.bypass("chatgpt", "admin@x")
    assert ph.blocked_message() is None and not ph.active("chatgpt")   # runs allowed, provider skipped
    ph.record("chatgpt", False, "still broken")                          # failures while bypassed do not re-alert
    assert st.get_status("chatgpt")["status"] == "bypassed" and sent == [("chatgpt", "down")]


def test_probe_success_recovers_and_emails_once(monkeypatch):
    st, sent = _fresh(monkeypatch)
    ph.mark_down("gemini", "429", by="monitor")
    monkeypatch.setitem(ph._PROBES, "gemini", lambda: None)
    r = ph.probe("gemini", "admin@x")
    assert r["ok"] is True and st.get_status("gemini")["status"] == "ok"
    assert sent == [("gemini", "down"), ("gemini", "recovered")]


def test_probe_failure_marks_down(monkeypatch):
    st, sent = _fresh(monkeypatch)
    def boom():
        raise RuntimeError("HTTP 401: key invalid key=SECRET123")
    monkeypatch.setitem(ph._PROBES, "places", boom)
    r = ph.probe("places", "scheduler")
    assert r["ok"] is False and "SECRET123" not in r["error"]
    assert st.get_status("places")["status"] == "down" and sent == [("places", "down")]
    assert "Google Places" in ph.blocked_message()


def test_unconfigured_provider_is_inactive_and_not_probed(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY")
    assert not ph.active("gemini")
    assert ph.probe("gemini")["ok"] is None
