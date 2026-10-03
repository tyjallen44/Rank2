"""Groups: ranking, benchmark, PDF context and the summary wording."""
from datetime import date
from perception import groups as G


def _members(scores):
    ms = [{"run_id": f"r{i}", "entity_name": f"Org {i}", "location": "City, ST", "score": s, "pillars": {}} for i, s in enumerate(scores)]
    scored = sorted([m for m in ms if m["score"] is not None], key=lambda m: -m["score"])
    rank, prev = 0, None
    for i, m in enumerate(scored, 1):
        if m["score"] != prev:
            rank, prev = i, m["score"]
        m["rank"] = rank
    return scored + [m for m in ms if m["score"] is None]


def test_benchmark_not_ready_below_min(monkeypatch):
    monkeypatch.setattr(G, "members", lambda gid: _members([70, 65, 80]))
    b = G.benchmark("g")
    assert b["scored"] == 3 and b["ready"] is False and b["median"] == 70 and b["q1"] is None


def test_benchmark_rank_and_quartiles(monkeypatch):
    scores = [90, 85, 85, 80, 75, 70, 65, 60, 55, 50, 45, None]
    monkeypatch.setattr(G, "members", lambda gid: _members(scores))
    b = G.benchmark("g", run_id="r2")        # 85, tied for 2nd
    assert b["ready"] is True and b["total"] == 11 and b["rank"] == 2 and b["score"] == 85
    assert b["median"] == 70 and b["q1"] is not None and b["q3"] is not None
    b2 = G.benchmark("g", entity_name="Org 10", location="City, ST")
    assert b2["rank"] == 11


def test_context_for_pdf_respects_show_flag(monkeypatch):
    monkeypatch.setattr(G, "members", lambda gid: _members([90, 85, 80, 75, 70, 65, 60, 55, 50, 45]))
    monkeypatch.setattr(G, "get_group", lambda gid: {"id": gid, "name": "Iowa Ortho", "archived": False, "show_on_pdf": True})
    c = G.context_for_pdf("g", "r3", "Org 3", "City, ST")
    assert c["ready"] and c["rank"] == 4 and c["total"] == 10 and c["group_name"] == "Iowa Ortho"
    monkeypatch.setattr(G, "get_group", lambda gid: {"id": gid, "name": "Iowa Ortho", "archived": False, "show_on_pdf": False})
    c2 = G.context_for_pdf("g", "r3", "Org 3", "City, ST")
    assert c2["ready"] is False and "rank" not in c2


def test_pdf_lines():
    from perception.models import AnalysisResult, RankedProvider
    from perception.deep_pdf import _group_summary_bullet, _group_cover_line
    r = AnalysisResult(run_id="r", location="Des Moines, IA", generated_at=date.today(), individual_report=True, entity_name="DMOS",
                       entity_type="practice", rankings=[RankedProvider(rank=1, name="DMOS", ai_visibility_score=74)],
                       group_context={"group_name": "Iowa Ortho", "ready": True, "rank": 3, "total": 12, "median": 61, "members": 12})
    assert _group_summary_bullet(r) == "Ranks 3rd of 12 in Iowa Ortho, 13 points above the group median of 61."
    assert "Member of Iowa Ortho" in _group_cover_line(r) and "3rd of 12" in _group_cover_line(r)
    r.group_context = {"group_name": "Iowa Ortho", "ready": False, "members": 4}
    assert "benchmarks appear once it reaches 10" in _group_summary_bullet(r)
    assert G.ordinal(1) == "1st" and G.ordinal(12) == "12th" and G.ordinal(22) == "22nd" and G.ordinal(113) == "113th"
