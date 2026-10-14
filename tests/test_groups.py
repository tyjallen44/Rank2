"""Groups: ranking, benchmark, PDF context and the summary wording."""
from datetime import date
from perception import groups as G


def _members(scores):
    ms = [{"run_id": f"r{i}", "entity_name": f"Org {i}", "location": "City, ST", "score": s, "pillars": {}, "status": "member"} for i, s in enumerate(scores)]
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


def test_prospect_gets_would_rank_and_pdf_wording(monkeypatch):
    base = _members([90, 85, 80, 75, 70, 65, 60, 55, 50, 45])
    prospect = {"run_id": "px", "entity_name": "New Ortho", "location": "City, ST", "score": 78, "pillars": {}, "status": "prospect", "rank": None, "would_rank": 4}
    monkeypatch.setattr(G, "members", lambda gid: base + [prospect])
    b = G.benchmark("g", run_id="px")
    assert b["prospect"] is True and b["rank"] == 4 and b["total"] == 10 and b["prospects"] == 1
    monkeypatch.setattr(G, "get_group", lambda gid: {"id": gid, "name": "Iowa Ortho", "archived": False, "show_on_pdf": True})
    c = G.context_for_pdf("g", "px", "New Ortho", "City, ST")
    assert c["prospect"] and c["rank"] == 4
    from datetime import date
    from perception.models import AnalysisResult, RankedProvider
    from perception.deep_pdf import _group_summary_bullet, _group_cover_line
    r = AnalysisResult(run_id="px", location="City, ST", generated_at=date.today(), individual_report=True, entity_name="New Ortho",
                       entity_type="practice", rankings=[RankedProvider(rank=1, name="New Ortho", ai_visibility_score=78)], group_context=c | {"median": 67, "total": 10})
    assert _group_summary_bullet(r).startswith("Would rank 4th of 10 members of Iowa Ortho")
    assert "Prospect for Iowa Ortho" in _group_cover_line(r) and "would rank 4th of 10" in _group_cover_line(r)


def test_remove_run_takes_the_whole_organization(monkeypatch):
    """Re-runs leave earlier memberships behind; Remove drops every run of the organization."""
    calls = []
    class _Con:
        def execute(self, sql, params=None):
            calls.append((sql.strip()[:6], params))
            class R:
                def fetchone(self_inner): return ("Orthosouth", "Memphis, TN")
                def fetchall(self_inner): return [("r-new",), ("r-old",)]
            return R()
        def close(self): pass
    monkeypatch.setattr(G, "get_connection", lambda: _Con())
    assert G.remove_run("g", "r-new") == 2
    deleted = [p[1] for s, p in calls if s.startswith("DELETE")]
    assert deleted == ["r-new", "r-old"]
    calls.clear()
    assert G.remove_run("g", "r-new", whole_org=False) == 1 and [p[1] for s, p in calls if s.startswith("DELETE")] == ["r-new"]


def test_customer_cover_line():
    from types import SimpleNamespace
    from perception.deep_pdf import _customer_cover_line
    assert "RLDatix customer" in _customer_cover_line(SimpleNamespace(rldatix_customer=True, entity_name="X", location="Memphis, TN"))
    assert _customer_cover_line(SimpleNamespace(rldatix_customer=False, entity_name="X", location="Memphis, TN")) == ""


def test_physician_roster_zip_is_the_last_five_digit_group():
    from perception.data import physician_facts as PF
    rec = {"number": "1", "addresses": [{"address_purpose": "LOCATION", "postal_code": "774331234"}]}
    monkeypatch_lookup = lambda name, state: [rec]
    orig = PF._nppes_lookup_physician
    PF._nppes_lookup_physician = monkeypatch_lookup
    try:
        f = PF.verify_physicians([{"name": "Jane Doe", "npi": "1"}], [{"address": "20203 Bridgeland Creek Pkwy Ste 200, Cypress, TX 77433, USA"}], "TX", [], sample=5)
    finally:
        PF._nppes_lookup_physician = orig
    assert f["rows"][0]["linked"] is True and f["linkage_pct"] == 100


def test_location_rows_one_per_office():
    from types import SimpleNamespace
    from perception.deep_pdf import _location_rows
    locs = [SimpleNamespace(name="Brand Ortho", address="", google_rating=None, google_review_count=None)]
    p = SimpleNamespace(name="Brand Ortho", consolidated_locations=locs)
    comp = [{"practice_name": "Brand Ortho", "is_anchor": True, "place_id": "pa", "address": "1 Anchor Way, Cypress, TX 77433, USA", "avg_rating": 4.8, "google_rating": 4.8, "total_reviews": 673},
            {"practice_name": "Brand Ortho", "is_anchor": True, "place_id": "pa", "address": "1 Anchor Way, Cypress, TX 77433, USA", "avg_rating": 4.8, "google_rating": 4.8, "total_reviews": 673},
            {"practice_name": "Brand & Ortho", "is_anchor": False, "place_id": "pk", "address": "601 Rockmead Dr, Kingwood, TX 77339, USA", "avg_rating": 4.6, "total_reviews": 197},
            {"practice_name": "Brand & Ortho", "is_anchor": False, "place_id": "ph", "address": "11800 FM 1960 W, Houston, TX 77065, USA", "avg_rating": 3.0, "total_reviews": 2},
            {"practice_name": "Brand & Ortho", "is_anchor": False, "place_id": "pk", "address": "601 Rockmead Dr, Kingwood, TX 77339, USA", "avg_rating": 4.6, "total_reviews": 197}]
    audit = {"profiles": [{"found": True, "name": "Brand Ortho", "place_id": "pa", "address": "1 Anchor Way, Cypress, TX 77433, USA", "rating": 4.8, "review_count": 673},
                          {"found": True, "name": "Brand & Ortho", "place_id": "pk", "address": "601 Rockmead Dr, Kingwood, TX 77339, USA", "rating": 4.6, "review_count": 197}]}
    r = SimpleNamespace(entity_name="Brand Ortho", practice_composite_rows=comp, profile_audit=audit, location_resolution=None)
    rows = _location_rows(r, p)
    assert len(rows) == 3, [x["name"] + "|" + (x.get("address") or "") for x in rows]
    assert rows[0].get("anchor") and rows[0]["address"].startswith("1 Anchor Way")
    assert {x["address"].split(",")[0] for x in rows[1:]} == {"601 Rockmead Dr", "11800 FM 1960 W"}


def test_set_contacts_cleans_and_dedupes(monkeypatch):
    from perception import graph as G2
    store = {}
    class _Con:
        def execute(self, sql, params=None):
            s = sql.strip().upper()
            if s.startswith("SELECT 1"):
                class R:
                    def fetchone(self_inner): return (1,)
                return R()
            if s.startswith("UPDATE ORG_GRAPH SET CONTACTS_JSON"):
                store["contacts_json"] = params[0]
            class R2:
                def fetchone(self_inner): return None
                def fetchall(self_inner): return []
            return R2()
        def close(self): pass
    monkeypatch.setattr(G2, "get_connection", lambda: _Con())
    monkeypatch.setattr(G2, "init_graph", lambda con=None: None)
    saved = G2.set_contacts("Brand", "Memphis", "TN", [{"name": " Jane Doe ", "email": "Jane@Brand.com"}, {"name": "dup", "email": "jane@brand.com"},
                                                      {"name": "bad", "email": "not-an-email"}, {"name": "", "email": "ops@brand.com"}], by="ty")
    assert saved == [{"name": "Jane Doe", "email": "jane@brand.com"}, {"name": "", "email": "ops@brand.com"}]
    import json
    assert json.loads(store["contacts_json"]) == saved


def test_trend_gate_note():
    from perception.groups import trend_note, TREND_MIN
    assert TREND_MIN == 3
    assert trend_note(0) == "Trend report available after 3 monthly reports (0 so far)"
    assert trend_note(2).endswith("(2 so far)") and trend_note(3) == "" and trend_note(7) == ""
