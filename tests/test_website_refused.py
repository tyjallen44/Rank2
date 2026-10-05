"""Website access: a site that refuses our server (browser included) is not an AI block."""
from perception.data import website_facts as wf
from perception.checklist import build_checklist


def test_refused_vs_blocked(monkeypatch):
    import perception.content_analyzer as ca
    monkeypatch.setattr(ca, "_crawl_site", lambda client, url, budget, browser: {"reachable": False, "fetch_status": "blocked"})
    class _B:
        def close(self): pass
    monkeypatch.setattr(ca, "_BrowserFetcher", _B)
    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ca, "_client", lambda: _C())
    monkeypatch.setattr(wf, "assistant_fetch_check", lambda url: {"assistant": "Claude", "ok": True, "note": "read"})
    monkeypatch.setattr(wf, "probe_ai_crawlers", lambda url, timeout=15.0: {"results": [], "blocked": ["GPTBot", "ClaudeBot"], "allowed": [], "browser_blocked": True, "where": "firewall"})
    f = wf.fetch_website_facts("https://www.dmos.com")
    assert f["status"] == "refused" and f["assistant_fetch"]["ok"] is True
    assert wf.ai_access_problem(f) is None and wf.identity_cap(f) is None
    assert "unverified" in wf.summary(f, "practice") and "Claude" in wf.summary(f, "practice")
    assert "Do NOT say AI assistants cannot read" in wf.evidence_lines(f, "practice")
    monkeypatch.setattr(wf, "probe_ai_crawlers", lambda url, timeout=15.0: {"results": [], "blocked": ["GPTBot", "ClaudeBot"], "allowed": [], "browser_blocked": False, "where": "firewall"})
    f2 = wf.fetch_website_facts("https://example.org")
    assert f2["status"] == "blocked" and wf.ai_access_problem(f2)["level"] == "critical" and wf.identity_cap(f2) == (45, "AI crawlers are turned away at the firewall")


def test_checklist_rows_for_refused():
    from datetime import date
    from perception.models import AnalysisResult, RankedProvider
    r = AnalysisResult(run_id="r", location="Des Moines, IA", generated_at=date.today(), individual_report=True, entity_name="DMOS",
                       entity_type="practice", weighting_profile="practice_procedural",
                       rankings=[RankedProvider(rank=1, name="DMOS", weighting_profile="practice_procedural", report_type="practice")],
                       website_facts={"status": "refused", "assistant_fetch": {"ok": True}})
    rows = {x["id"]: x for x in build_checklist(r)}
    assert rows["ai_access"]["status"] == "partial" and "Claude" in rows["ai_access"]["detail"]
    assert rows["robots"]["status"] == "na"


def test_challenge_wall_or_failed_live_fetch_is_blocked(monkeypatch):
    """A JavaScript challenge page shuts out every non-JS reader (AI crawlers included) → blocked; a bare
    refusal of our hosting stays 'refused' only while an AI assistant's own live fetch still succeeds."""
    from perception.data import website_facts as wf
    import perception.content_analyzer as ca
    monkeypatch.setattr(ca, "_crawl_site", lambda client, url, budget, browser: {"reachable": False, "fetch_status": "blocked"})
    class _B:
        def close(self): pass
    monkeypatch.setattr(ca, "_BrowserFetcher", _B)
    probe_wall = {"results": [], "blocked": ["GPTBot", "ClaudeBot"], "allowed": [], "browser_blocked": True, "browser_challenge": True, "where": "firewall"}
    monkeypatch.setattr(wf, "probe_ai_crawlers", lambda url, timeout=15.0: dict(probe_wall))
    monkeypatch.setattr(wf, "assistant_fetch_check", lambda url: {"assistant": "Claude", "ok": True, "note": "read"})
    f = wf.fetch_website_facts("https://www.usahealthsystem.com/")
    assert f["status"] == "blocked" and "challenge" in f["note"]
    a = wf.ai_access_problem(f)
    assert a and a["level"] == "critical" and "JavaScript challenge" in a["points"][0]
    # bare refusal + Claude refused too → blocked
    probe_bare = dict(probe_wall, browser_challenge=False)
    monkeypatch.setattr(wf, "probe_ai_crawlers", lambda url, timeout=15.0: dict(probe_bare))
    monkeypatch.setattr(wf, "assistant_fetch_check", lambda url: {"assistant": "Claude", "ok": False, "note": "refused"})
    f2 = wf.fetch_website_facts("https://www.dmos.com/")
    assert f2["status"] == "blocked" and "live fetch" in f2["note"] and "live fetch was turned away" in wf.ai_access_problem(f2)["points"][0]
    # bare refusal + Claude read it → refused (unverified), no alert
    monkeypatch.setattr(wf, "assistant_fetch_check", lambda url: {"assistant": "Claude", "ok": True, "note": "read"})
    f3 = wf.fetch_website_facts("https://www.dmos.com/")
    assert f3["status"] == "refused" and wf.ai_access_problem(f3) is None
