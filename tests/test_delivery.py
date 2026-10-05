"""Delivery check: the post-render linter that flags what a report is missing before it goes out."""
from datetime import date, timedelta
from perception import delivery as D


def _ok_result(**over):
    d = {
        "entity_type": "practice", "specialty": "Orthopedics", "generated_at": date.today().isoformat(),
        "rankings": [{"ai_visibility_score": 72}],
        "website_facts": {"status": "measured", "pages": 12},
        "spotcheck": {"asked": 3},
        "physician_facts": {"status": "measured", "rows": [{"name": "Dr. A"}]},
        "executive_summary_structured": {"headline": "Strong locally", "bullets": ["one", "two"]},
        "top_recommendation": "• Fix robots.txt",
        "ai_visibility_verdict": "Strong locally. More.",
        "group_context": {"ready": True},
    }
    d.update(over)
    return d


def _ids(flags):
    return [f["id"] for f in flags]


def test_complete_report_has_no_flags():
    assert D.check_result(_ok_result(), group={"type_hint": "practice"}) == []


def test_each_missing_piece_is_named():
    g = {"type_hint": "practice"}
    assert _ids(D.check_result(_ok_result(spotcheck=None), group=g)) == ["spotcheck"]
    assert _ids(D.check_result(_ok_result(physician_facts=None), group=g)) == ["physicians"]
    assert _ids(D.check_result(_ok_result(executive_summary_structured=None), group=g)) == ["summary"]
    assert _ids(D.check_result(_ok_result(top_recommendation=""), group=g)) == ["first_moves"]
    assert _ids(D.check_result(_ok_result(specialty="Bones and joints"), group=g)) == ["specialty"]
    assert _ids(D.check_result(_ok_result(specialty=""), group=g)) == ["specialty"]
    old = (date.today() - timedelta(days=120)).isoformat()
    assert _ids(D.check_result(_ok_result(generated_at=old), group=g)) == ["stale"]


def test_website_levels():
    g = {"type_hint": "practice"}
    blocked = D.check_result(_ok_result(website_facts={"status": "blocked", "crawler_probe": {"blocked": ["GPTBot"]}}), group=g)
    assert blocked[0]["id"] == "website" and blocked[0]["level"] == "fix" and "GPTBot" in blocked[0]["detail"]
    refused = D.check_result(_ok_result(website_facts={"status": "refused", "assistant_fetch": {"ok": True}}), group=g)
    assert refused[0]["level"] == "warn" and "live fetch" in refused[0]["detail"]
    # a hospital run with no crawl is not flagged for the website; a practice run is
    assert "website" not in _ids(D.check_result(_ok_result(entity_type="hospital", website_facts=None, physician_facts=None), group={"type_hint": "hospital"}))
    assert "website" in _ids(D.check_result(_ok_result(website_facts=None), group=g))


def test_rubric_and_benchmark_and_score():
    hosp = _ok_result(entity_type="hospital", physician_facts=None)
    fl = D.check_result(hosp, group={"type_hint": "practice"})
    assert _ids(fl) == ["rubric"] and fl[0]["level"] == "fix"
    assert "rubric" not in _ids(D.check_result(hosp, group={"type_hint": "hospital"}))
    assert _ids(D.check_result(_ok_result(group_context=None), group={"type_hint": "practice"}, benchmark_ready=True)) == ["benchmark"]
    assert "benchmark" not in _ids(D.check_result(_ok_result(group_context=None), group={"type_hint": "practice"}, benchmark_ready=False))
    fl = D.check_result(_ok_result(rankings=[]), group={"type_hint": "practice"})
    assert fl[0]["id"] == "no_score" and fl[0]["level"] == "fix"


def test_fix_flags_sort_first_and_spotcheck_can_be_waived():
    fl = D.check_result(_ok_result(spotcheck=None, website_facts={"status": "blocked"}), group={"type_hint": "practice"})
    assert _ids(fl) == ["website", "spotcheck"]
    assert D.check_result(_ok_result(spotcheck=None), group={"type_hint": "practice"}, require_spotcheck=False) == []


def test_spotcheck_required_follows_preset(monkeypatch):
    from perception import presets as P
    monkeypatch.setitem(P.PRESETS, "quiet", {"options": {"spotcheck": "hidden"}})
    assert D.spotcheck_required({"preset": "quiet"}) is False
    assert D.spotcheck_required({"preset": "association_specialty_practice"}) is True
    assert D.spotcheck_required({"preset": None}) is True
    assert D.spotcheck_required(None) is True


def test_summarize_counts():
    ms = [{"flags": []}, {"flags": [{"id": "stale", "level": "warn"}]},
          {"flags": [{"id": "website", "level": "fix"}, {"id": "stale", "level": "warn"}]}]
    s = D.summarize(ms)
    assert s == {"flagged": 2, "fix": 1, "by_flag": {"stale": 2, "website": 1}}
