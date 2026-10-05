"""Confirm once, reuse everywhere: a confirmed roster is the roster; the resolver enriches it and reports drift."""
from perception.location_resolver import merge_confirmed_locations
from perception.physician_resolver import merge_confirmed_physicians
from perception import delivery as D


def test_confirmed_locations_win_and_drift_is_reported():
    confirmed = [{"name": "Brand – East", "city": "Memphis", "state": "TN", "address": "10 East Rd, Memphis, TN 38104", "place_id": "g-east"},
                 {"name": "Brand – Closed", "city": "Bartlett", "state": "TN", "address": "99 Gone St, Bartlett, TN 38133", "place_id": None}]
    resolved = [{"name": "Brand Orthopedics East", "city": "Memphis", "state": "TN", "address": "10 East Rd, Memphis, TN 38104, USA", "place_id": "g-east",
                 "rating": 4.6, "review_count": 120, "sources": ["website", "google", "nppes"], "google_missing": False, "website_missing": False},
                {"name": "Brand – Covington", "city": "Covington", "state": "TN", "address": "1 New Way, Covington, TN 38019", "place_id": "g-cov",
                 "sources": ["google"], "google_missing": False, "website_missing": True}]
    out, drift = merge_confirmed_locations(confirmed, resolved)
    assert [o["name"] for o in out] == ["Brand – East", "Brand – Closed"]           # the confirmed roster, in its order
    assert out[0]["rating"] == 4.6 and out[0]["sources"] == ["confirmed", "website", "google", "nppes"]
    assert out[1]["sources"] == ["confirmed"]
    assert drift == {"new": ["Brand – Covington"], "missing": ["Brand – Closed"]}


def test_confirmed_physicians_match_by_npi_or_name():
    confirmed = [{"name": "Jane Doe", "npi": "111", "credential": "MD", "specialty": ""},
                 {"name": "Sam Lee", "npi": None, "credential": "", "specialty": ""},
                 {"name": "Left Town", "npi": "999", "credential": "MD", "specialty": ""}]
    resolved = [{"name": "Jane A. Doe", "npi": "111", "credential": "MD", "specialty": "Orthopaedic Surgery", "bio_url": "/doctors/jane", "sources": ["website", "nppes"]},
                {"name": "Samuel Lee", "npi": "222", "credential": "DO", "specialty": "", "sources": ["website", "nppes"]},
                {"name": "New Hire", "npi": "333", "credential": "MD", "specialty": "", "sources": ["nppes"], "website_missing": True}]
    out, drift = merge_confirmed_physicians(confirmed, resolved)
    assert [o["name"] for o in out] == ["Jane Doe", "Sam Lee", "Left Town"]
    assert out[0]["specialty"] == "Orthopaedic Surgery" and out[0]["bio_url"] == "/doctors/jane" and out[0]["sources"][0] == "confirmed"
    assert out[1]["npi"] == "222" and out[1]["credential"] == "DO" and out[1]["npi_missing"] is False
    assert out[2]["sources"] == ["confirmed"]
    assert drift == {"new": ["New Hire"], "missing": ["Left Town"]}


def test_delivery_flags_roster_drift():
    d = {"entity_type": "practice", "specialty": "Orthopedics", "generated_at": "2026-10-05", "rankings": [{"ai_visibility_score": 70}],
         "website_facts": {"status": "measured"}, "spotcheck": {"asked": 3}, "physician_facts": {"status": "measured", "rows": [{"name": "A"}]},
         "executive_summary_structured": {"headline": "h", "bullets": ["b"]}, "top_recommendation": "x", "ai_visibility_verdict": "v",
         "location_resolution": {"confirmed": {"count": 2}, "drift": {"new": ["Brand – Covington"], "missing": []}},
         "physician_resolution": {"confirmed": {"count": 3}, "drift": {"new": [], "missing": ["Left Town"]}}}
    import datetime
    d["generated_at"] = datetime.date.today().isoformat()
    fl = D.check_result(d, group={"type_hint": "practice"})
    assert [f["id"] for f in fl] == ["roster_drift"] and fl[0]["level"] == "warn"
    assert "Brand – Covington" in fl[0]["detail"] and "Left Town" in fl[0]["detail"]
    d["location_resolution"]["drift"] = {"new": [], "missing": []}; d["physician_resolution"]["drift"] = {"new": [], "missing": []}
    assert D.check_result(d, group={"type_hint": "practice"}) == []


def test_soft_confirmation_keeps_fresh_findings_and_hard_does_not():
    from perception.location_resolver import apply_confirmed_locations
    from perception.physician_resolver import apply_confirmed_physicians
    confirmed = [{"name": "OrthoSouth", "city": "Memphis", "state": "TN", "address": "4515 Poplar Ave #206, Memphis, TN 38117", "place_id": "g1"},
                 {"name": "OrthoSouth (old)", "city": "Memphis", "state": "TN", "address": "1 Gone Rd, Memphis, TN 38100", "place_id": None}]
    resolved = [{"name": "OrthoSouth – Memphis Poplar", "city": "Memphis", "state": "TN", "address": "4515 Poplar Ave #206, Memphis, TN 38117, USA", "place_id": "g1", "sources": ["website", "google"]},
                {"name": "OrthoSouth – Covington", "city": "Covington", "state": "TN", "address": "1995 Hwy 51 S, Covington, TN 38019", "place_id": "g2", "sources": ["google"]}]
    soft, d1 = apply_confirmed_locations(confirmed, resolved, hard=False)
    assert [s["name"] for s in soft] == ["OrthoSouth", "OrthoSouth – Covington", "OrthoSouth (old)"] and d1["new"] == [] and d1["soft"]
    hard, d2 = apply_confirmed_locations(confirmed, resolved, hard=True)
    assert [s["name"] for s in hard] == ["OrthoSouth", "OrthoSouth (old)"] and d2["new"] == ["OrthoSouth – Covington"]
    cp = [{"name": "ANDREW WODOWSKI", "npi": "1", "credential": "MD"}]
    rp = [{"name": "Andrew J. Wodowski", "npi": "1", "credential": "MD", "sources": ["website", "nppes"]}, {"name": "New Hire", "npi": "2", "credential": "MD", "sources": ["nppes"]}]
    softp, _ = apply_confirmed_physicians(cp, rp, hard=False)
    assert [p["name"] for p in softp] == ["Andrew Wodowski", "New Hire"]          # upper-case confirmed names are title-cased
    hardp, dp = apply_confirmed_physicians(cp, rp, hard=True)
    assert [p["name"] for p in hardp] == ["Andrew Wodowski"] and dp["new"] == ["New Hire"]
