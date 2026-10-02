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
