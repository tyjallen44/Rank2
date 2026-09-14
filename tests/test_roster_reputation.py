"""Pillar 2 (Reviews & Reputation) must come from the confirmed location roster,
pinned per listing, not from one name search that can land on the parent hospital."""
from perception import practice_analyzer as pa
from perception.data import places
from perception import practice_scoring


def _read(name, rating, count, pid, match="strong", types=("doctor",), verified=True):
    return places.GoogleRead(query=name, verified=verified, rating=rating, review_count=count,
                             matched_name=name, name_match=match, place_id=pid, types=list(types),
                             formatted_address="1 Main St, Houston, TX 77030, USA")


def test_strict_gate_rejects_weak_match_and_non_healthcare():
    assert pa._strict_listing_ok(_read("Houston Methodist Hospital", 4.5, 5000, "p1", match="weak")) is False
    assert pa._strict_listing_ok(_read("Some Cafe", 4.8, 100, "p2", types=("cafe",))) is False
    assert pa._strict_listing_ok(_read("HM Ortho", 4.2, 80, "p3")) is True
    assert pa._strict_listing_ok(_read("HM Ortho", 4.2, 80, "p4", types=())) is True  # empty types pass


def test_roster_aggregate_uses_pinned_entries_and_weights_by_volume(monkeypatch):
    calls = []
    monkeypatch.setattr(places, "fetch_provider", lambda *a, **k: calls.append(a) or (None, None))
    anchor = {"name": "HM Ortho Main", "place_id": "A", "rating": 4.6, "review_count": 150}
    sibs = [
        {"name": "HM Ortho West", "place_id": "B", "rating": 4.2, "review_count": 50},
        {"name": "HM Ortho Katy", "place_id": "C", "rating": 3.0, "review_count": 100},
    ]
    rep = pa._resolve_roster_google(anchor, sibs, "Houston", "TX")
    assert calls == []                                   # pinned → no Places calls
    assert rep["rated_locations"] == 3 and rep["locations"] == 3
    assert rep["total_reviews"] == 300
    assert rep["avg_rating"] == round((4.6*150 + 4.2*50 + 3.0*100) / 300, 1)
    # A 3.9★ / 300-review roster bands far below a 4.5★ / 5000-review hospital listing.
    assert practice_scoring.reviews_band(rep["avg_rating"], rep["total_reviews"]) < \
        practice_scoring.reviews_band(4.5, 5000)


def test_unpinned_sibling_resolved_under_strict_gate_and_anchor_alias_dropped(monkeypatch):
    table = {
        "HM Ortho Main": _read("HM Ortho Main", 4.6, 150, "A"),
        "HM Ortho West": _read("HM Ortho West", 4.0, 40, "B"),
        "Houston Methodist Hospital": _read("Houston Methodist Hospital", 4.5, 5000, "H", match="weak"),
        "HM Ortho Main Office": _read("HM Ortho Main", 4.6, 150, "A"),   # alias → same place_id as anchor
    }
    monkeypatch.setattr(places, "fetch_provider", lambda name, city, state: (table[name], None))
    anchor = {"name": "HM Ortho Main"}
    sibs = [{"name": "HM Ortho West"}, {"name": "Houston Methodist Hospital"}, {"name": "HM Ortho Main Office"}]
    rep = pa._resolve_roster_google(anchor, sibs, "Houston", "TX")
    assert anchor["place_id"] == "A" and anchor["rating"] == 4.6      # pinned by the pass
    assert sibs[0]["place_id"] == "B"                                # strict-ok sibling pinned
    assert "place_id" not in sibs[1]                                 # weak-match hospital rejected
    assert sibs[2].get("_anchor_dup") is True                        # alias flagged, not counted
    assert rep["rated_locations"] == 2 and rep["total_reviews"] == 190
    assert rep["locations"] == 3


def test_roster_key_is_order_insensitive_and_roster_sensitive():
    a = pa._roster_key("Anchor", [{"name": "X"}, {"name": "Y"}])
    b = pa._roster_key("anchor", [{"name": "y"}, {"name": "x"}])
    c = pa._roster_key("Anchor", [{"name": "X"}])
    assert a == b and a != c
