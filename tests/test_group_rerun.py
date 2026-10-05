"""One-click re-run of a group member from its stored result."""
from datetime import date
from perception.models import AnalysisResult, RankedProvider, ConsolidatedLocation


def test_rerun_request_rebuilds_settings():
    import server
    p = RankedProvider(rank=1, name="DMOS Orthopaedic Centers", website_url="https://www.dmos.com",
                       consolidated_locations=[ConsolidatedLocation(name="DMOS Orthopaedic Centers", address="1 Main"),
                                               ConsolidatedLocation(name="DMOS West", address="2 West St")])
    r = AnalysisResult(run_id="r1", location="Des Moines, IA", generated_at=date.today(), individual_report=True,
                       entity_name="DMOS Orthopaedic Centers", entity_type="practice", specialty="Orthopedics",
                       practice_profile="practice_procedural", rankings=[p],
                       practice_composite_rows=[{"practice_name": "DMOS Orthopaedic Centers", "is_anchor": True, "place_id": "pid1", "address": "1 Main",
                                                 "rating": 4.6, "review_count": 500, "maps_url": "https://maps/x",
                                                 "physicians": [{"physician_name": "Dr. Jane Doe", "npi": "123", "specialty": "Orthopedics", "credential": "MD"}]}])
    req = server._rerun_request_from_result(r, "g1")
    assert req.city == "Des Moines" and req.state == "IA" and req.entity_type == "practice" and req.specialty == "Orthopedics"
    assert req.website == "https://www.dmos.com" and req.content_urls == ["https://www.dmos.com"]
    assert req.anchor_listing["place_id"] == "pid1"
    assert req.confirmed_siblings is None          # the roster is resolved afresh on re-run, not carried over
    assert req.physician_composite and req.physician_roster["DMOS Orthopaedic Centers"][0]["name"] == "Jane Doe"
    assert req.spotcheck and req.force_rerun and req.group_id == "g1" and req.practice_composite
    h = AnalysisResult(run_id="r2", location="Mobile, AL", generated_at=date.today(), individual_report=True, entity_name="USA Health",
                       entity_type="hospital", rankings=[RankedProvider(rank=1, name="USA Health")])
    rq = server._rerun_request_from_result(h, "g1")
    assert rq.entity_type is None and rq.specialty is None and not rq.practice_composite and rq.group_id == "g1"
