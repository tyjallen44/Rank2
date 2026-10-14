"""Run summary: the compact row the group page reads gives the same delivery flags as the full result."""
import json
from datetime import date
from perception.run_summary import build_summary
from perception import delivery as D


def _full():
    return {"entity_type": "practice", "specialty": "Orthopedics", "generated_at": date.today().isoformat(),
            "rankings": [{"ai_visibility_score": 74, "tier_scores": {"access_fit": 70}}],
            "website_facts": {"status": "blocked", "crawler_probe": {"blocked": ["GPTBot"], "results": [{"x": 1}] * 6}, "site_pages": ["big"] * 50},
            "spotcheck": {"asked": 30, "rows": ["…"] * 60},
            "physician_facts": {"status": "measured", "rows": [{"name": f"Dr {i}"} for i in range(46)]},
            "practice_composite_rows": [{"physicians": [{"physician_name": "a"}] * 3}],
            "executive_summary_structured": {"headline": "Strong", "bullets": ["a", "b", ""]},
            "ai_visibility_verdict": "long text", "top_recommendation": "• do this", "group_context": {"ready": True, "rank": 2},
            "location_resolution": {"total": 12, "siblings": [{"name": "x"}] * 12, "drift": {"new": ["Cov"], "missing": []}},
            "listing_quality": {"status": {"closed": []}, "phone": {"compared": 2, "match": 1, "mismatch": [{"name": "East", "website": "1", "google": "2"}]},
                                "address": {"compared": 0, "match": 0, "mismatch": []}, "npi": {}, "category": {"counts": {}}, "duplicates": [], "recency": {}, "physicians": {}}}


def test_summary_is_small_and_matches_full_delivery_check():
    full = _full()
    s = build_summary(full)
    assert len(json.dumps(s)) < 1200 and s["physician_count"] == 49 and s["rankings"][0]["ai_visibility_score"] == 74
    assert s["executive_summary_structured"]["bullets"] == ["·", "·"]
    flags_full = D.check_result(full, group={"type_hint": "practice"})
    flags_sum = D.check_result(s, group={"type_hint": "practice"})
    assert [f["id"] for f in flags_full] == [f["id"] for f in flags_sum] == ["website", "roster_drift", "listing_quality"]
    assert [f["level"] for f in flags_full] == [f["level"] for f in flags_sum]
    assert flags_sum[2]["items"] == flags_full[2]["items"]
