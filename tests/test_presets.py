"""Account access: presets, indicators, route guards and History scope."""
import json

from perception.presets import capabilities, report_allowed, deep_report_id, REPORT_IDS, PRESETS


def test_unrestricted_when_nothing_assigned():
    for user in (None, {}, {"preset": None, "indicators_json": None}, {"preset": "", "indicators_json": ""}):
        caps = capabilities(user)
        assert caps["unrestricted"] is True
        assert all(caps["reports"][r] for r in REPORT_IDS)
        assert caps["history_scope"] == "all"
        assert report_allowed(caps, "network")


def test_preset_limits_reports_and_sets_options():
    caps = capabilities({"preset": "association_specialty_practice", "indicators_json": None})
    assert caps["unrestricted"] is False
    assert caps["reports"]["deep_practice"] is True
    assert caps["reports"]["network"] is False and caps["reports"]["trends"] is False
    assert caps["options"]["spotcheck"] == "on" and caps["options"]["teaser"] == "hidden"
    assert caps["history_scope"] == "preset"
    assert not report_allowed(caps, "events")


def test_indicators_override_the_presets_report_list():
    caps = capabilities({"preset": "association_specialty_practice",
                         "indicators_json": json.dumps(["deep_practice", "events", "bogus"])})
    assert caps["reports"]["events"] is True and caps["reports"]["deep_practice"] is True
    assert caps["reports"]["network"] is False
    assert caps["options"]["spotcheck"] == "on"          # options still from the preset


def test_indicators_without_preset_restrict_too():
    caps = capabilities({"preset": None, "indicators_json": json.dumps(["network"])})
    assert caps["unrestricted"] is False and caps["reports"]["network"] and not caps["reports"]["compare"]
    assert caps["history_scope"] == "all"


def test_deep_report_id():
    assert deep_report_id(None) == "deep_hospital"
    assert deep_report_id("practice") == "deep_practice"
    assert deep_report_id("practice", "Orthopedics") == "deep_service_line"
    assert deep_report_id("service_line") == "deep_service_line"
    assert deep_report_id("community_health") == "deep_community"


def test_every_preset_only_names_known_indicators():
    from perception.presets import OPTION_IDS, OPTION_STATES
    for pid, p in PRESETS.items():
        assert set(p["reports"]) <= set(REPORT_IDS), pid
        assert set(p["options"]) <= set(OPTION_IDS), pid
        assert set(p["options"].values()) <= set(OPTION_STATES), pid


def test_route_guard_and_history_scope(monkeypatch):
    import server
    from perception import auth as _auth
    user = {"id": "u1", "email": "coord@assoc.org", "preset": "association_specialty_practice", "indicators_json": None}
    monkeypatch.setattr(_auth, "get_user_by_id", lambda uid: user if uid == "u1" else None)
    monkeypatch.setattr(_auth, "emails_on_preset", lambda p: ["coord@assoc.org", "member@assoc.org"])
    payload = {"uid": "u1", "email": "coord@assoc.org", "role": "user"}
    # guard
    server._require_report(payload, "deep_practice")
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        server._require_report(payload, "network")
    assert ei.value.status_code == 403
    # history scope: own + same-preset accounts only
    rows = [{"run_id": "1", "ran_by": "member@assoc.org"}, {"run_id": "2", "ran_by": "ty.allen@rldatix.com"},
            {"run_id": "3", "ran_by": "COORD@assoc.org"}, {"run_id": "4", "ran_by": None}]
    out = server._scope_history_rows(rows, payload)
    assert [r["run_id"] for r in out] == ["1", "3"]
    # unrestricted / password session: everything
    assert server._scope_history_rows(rows, {"role": "partner"}) == rows


def test_assigned_groups_scope_without_changing_report_access():
    from perception.presets import capabilities
    c = capabilities({"preset": None, "indicators_json": None, "groups_json": '["g1","g2"]'})
    assert c["unrestricted"] is True and c["groups"] == ["g1", "g2"] and c["groups_only"] is True
    c2 = capabilities({"preset": "association_specialty_practice", "indicators_json": None, "groups_json": '["g1"]'})
    assert c2["unrestricted"] is False and c2["groups"] == ["g1"] and c2["groups_only"] is True and c2["reports"]["deep_practice"]
    c3 = capabilities({"preset": None, "indicators_json": None, "groups_json": None})
    assert c3["groups"] == [] and c3["groups_only"] is False
