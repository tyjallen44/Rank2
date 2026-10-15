"""Typeahead on the city / state boxes (2026-10-15): suggest_entities(field=…)."""
import perception.db as db


def _rows(_role=None):
    rows = [{"name": "OrthoSouth", "kind": "specialty", "city": "Memphis", "state": "TN", "last_run": "2026-10-15", "run_count": 3},
            {"name": "DMOS Orthopaedic Centers", "kind": "specialty", "city": "West Des Moines", "state": "IA", "last_run": "2026-10-10", "run_count": 1},
            {"name": "Methodist Le Bonheur Healthcare", "kind": "network", "city": "Memphis", "state": "TN", "last_run": "2026-09-01", "run_count": 1}]
    for o in rows:
        o.setdefault("tracked", False)
        o["_hay"] = " ".join(x for x in (o.get("name"), o.get("parent_system"), o.get("city"), o.get("state")) if x).lower()
    return rows


def test_city_and_state_boxes_suggest_by_prefix(monkeypatch):
    monkeypatch.setattr(db, "_suggest_index", _rows)
    assert [r["name"] for r in db.suggest_entities("mem", field="city")] == ["OrthoSouth", "Methodist Le Bonheur Healthcare"]
    assert [r["name"] for r in db.suggest_entities("west", field="city")] == ["DMOS Orthopaedic Centers"]
    assert [r["name"] for r in db.suggest_entities("ia", field="state")] == ["DMOS Orthopaedic Centers"]
    assert [r["name"] for r in db.suggest_entities("tn", field="state", kinds=["network"])] == ["Methodist Le Bonheur Healthcare"]
    assert db.suggest_entities("zz", field="city") == []


def test_name_box_still_matches_name_city_and_state_anywhere(monkeypatch):
    monkeypatch.setattr(db, "_suggest_index", _rows)
    assert [r["name"] for r in db.suggest_entities("ortho")] == ["OrthoSouth", "DMOS Orthopaedic Centers"]
    assert [r["name"] for r in db.suggest_entities("memphis")][0] == "OrthoSouth"
    assert all(r.get("days_ago") is not None for r in db.suggest_entities("ortho"))
