"""Phase 2 SCORE inputs (2.3 organization name, 2.7 measurement limits): pure helpers."""
from bs4 import BeautifulSoup

from perception.pipeline import resolve_org_name, measurement_limits_block
from perception.content_analyzer import site_name_from_home
from perception.practice_analyzer import _roster_label


def test_site_name_prefers_schema_then_og_then_title():
    html = '<html><head><title>Home | IBJI</title><meta property="og:site_name" content="Illinois Bone & Joint Institute">' \
           '<script type="application/ld+json">{"@type":"MedicalOrganization","name":"Illinois Bone and Joint Institute"}</script></head></html>'
    assert site_name_from_home(BeautifulSoup(html, "html.parser")) == "Illinois Bone and Joint Institute"
    html2 = '<html><head><title>OrthoSouth | Orthopedic Care in Memphis</title></head></html>'
    assert site_name_from_home(BeautifulSoup(html2, "html.parser")) == "Orthopedic Care in Memphis" or site_name_from_home(BeautifulSoup(html2, "html.parser")) == "OrthoSouth"
    html3 = '<html><head><meta property="og:site_name" content="DMOS"><title>Welcome</title></head></html>'
    assert site_name_from_home(BeautifulSoup(html3, "html.parser")) == "DMOS"


def test_resolve_org_name_from_site_when_it_agrees():
    assert resolve_org_name("Ibji Doctors' Office - Chicago-Avondale", "Chicago", "Illinois Bone & Joint Institute") == "Illinois Bone & Joint Institute"
    assert resolve_org_name("Dmos Orthopaedic Centers - West Des Moines", "West Des Moines", "DMOS Orthopaedic Centers") == "DMOS Orthopaedic Centers"
    assert resolve_org_name("Orthosouth", "Memphis", "OrthoSouth") == "OrthoSouth"


def test_resolve_org_name_falls_back_to_entity_without_city():
    assert resolve_org_name("Dmos Orthopaedic Centers - West Des Moines", "West Des Moines", "") == "Dmos Orthopaedic Centers"
    # an unrelated page title never becomes the organization
    assert resolve_org_name("Georgia Carolina Orthopedics", "Augusta", "Patient Portal Login") == "Georgia Carolina Orthopedics"
    assert resolve_org_name("Campbell Clinic", "Memphis", "Baptist Memorial Health Care") == "Campbell Clinic"


def test_measurement_limits_block_names_crawl_limits_not_practice_gaps():
    lr = {"rejected": [{"name": "City Hall"}] * 3, "website": {"status": "measured"}, "model": {"used": True}}
    pr = {"directory_coverage": {"website": 4, "registry": 100, "credible": False, "pct": 4}, "website": {"status": "measured"}}
    pf = {"status": "measured", "rows": [{"registry": "ambiguous"}, {"registry": "found"}]}
    txt = measurement_limits_block(lr, None, None) + measurement_limits_block(None, pr, pf)
    assert "3 Google listings" in txt and "EXCLUDED" in txt
    assert "UNVERIFIED" in txt
    assert "CRAWL LIMITATION" in txt and "4 of 100" in txt and "possibly departed" in txt
    assert "1 physician name(s) matched several registry records" in txt
    assert measurement_limits_block({}, {}, {}) == ""


def test_roster_label_tags_model_recall_only():
    assert _roster_label({"name": "Brand – Aiken", "sources": ["website", "google"]}) == "Brand – Aiken"
    assert "UNVERIFIED" in _roster_label({"name": "Brand – Nowhere", "sources": ["model"]})


# ── Phase 2 presentation helpers ──────────────────────────────────────────────

def test_normalize_input_keeps_mixed_case_and_titles_shouted_input():
    import server
    assert server._normalize_input("OrthoSouth") == "OrthoSouth"
    assert server._normalize_input("DMOS Orthopaedic Centers") == "DMOS Orthopaedic Centers"
    assert server._normalize_input("IBJI Doctors’ Office - Chicago-Avondale") == "IBJI Doctors’ Office - Chicago-Avondale"
    assert server._normalize_input("GEORGIA CAROLINA ORTHOPEDICS") == "Georgia Carolina Orthopedics"
    assert server._normalize_input("illinois bone and joint institute") == "Illinois Bone and Joint Institute"


def test_measured_counts_come_from_the_report_not_the_model():
    from types import SimpleNamespace
    from perception.plain import measured_counts
    r = SimpleNamespace(practice_composite_rows=[{"not_established": False}] * 7 + [{"not_established": True}],
                        physician_facts={"rows": [{}] * 141, "registry_found": 120}, physician_resolution={"total": 141})
    assert measured_counts(r) == {"locations_verified": 7, "physicians_on_roster": 141, "physicians_registry_found": 120}


def test_elsewhere_profiles_need_a_credible_office_set():
    from perception.listing_quality import audit
    offices = [{"name": "A", "address": "1 Main St, Memphis, TN 38100", "place_id": "a", "is_anchor": True, "sources": ["website"]},
               {"name": "B", "address": "", "place_id": "b", "is_anchor": False, "sources": ["google"]},
               {"name": "C", "address": "", "place_id": "c", "is_anchor": False, "sources": ["google"]}]
    profiles = [{"place_id": "a", "address": "1 Main St, Memphis, TN 38100, USA", "found": True}]
    phys = [{"name": "Dr. X", "google_address": "500 Hospital Dr, Memphis, TN 38120, USA"}]
    q = audit(offices=offices, profiles=profiles, nppes=[], physicians=phys)
    assert q["physicians"]["elsewhere"] == [] and q["physicians"]["office_set_credible"] is False
    profiles += [{"place_id": "b", "address": "2 Oak St, Memphis, TN 38104, USA", "found": True}, {"place_id": "c", "address": "3 Elm St, Memphis, TN 38105, USA", "found": True}]
    q = audit(offices=offices, profiles=profiles, nppes=[{"address": "500 Hospital Dr, Memphis, TN 38120"}], physicians=phys)
    assert q["physicians"]["office_set_credible"] is True and q["physicians"]["elsewhere"] == []     # the registry address made it an office


def test_confidence_drops_below_high_when_physician_coverage_is_thin():
    from types import SimpleNamespace
    from perception.confidence import score_confidence
    fd = SimpleNamespace(verified=True, count=3000, rating=4.8, recency=None)
    sa = SimpleNamespace(available=False, total_reviews=None, location_count=None)
    ts = SimpleNamespace(as_dict=lambda: {"clinical_outcomes_safety": 70, "credentials_recognition": 90, "patient_experience_reviews": 60, "access_fit": 70})
    p = SimpleNamespace(google_footprint=SimpleNamespace(front_door=fd, system_aggregate=sa), tier_scores=ts, is_target=True, weighting_profile="practice_procedural")
    base = dict(rankings=[p], entity_type="practice", practice_composite_rows=[{"total_reviews": 3000, "google_count": 3000, "is_anchor": True}],
                verified_quality={}, web_search_used=True, rubric_note="", website_facts={}, profile_audit={})
    thin = SimpleNamespace(**base, physician_facts={"status": "measured", "rows": [{}] * 100, "cert_unread": 96, "linkage_pct": 70},
                           physician_resolution={"directory_coverage": {"credible": False, "website": 4, "registry": 100}})
    full = SimpleNamespace(**base, physician_facts={"status": "measured", "rows": [{}] * 100, "cert_unread": 10, "linkage_pct": 70},
                           physician_resolution={"directory_coverage": {"credible": True, "website": 95, "registry": 100}})
    assert score_confidence(thin)["level"] == "medium" and "thin" in score_confidence(thin)["note"]
    assert score_confidence(full)["level"] == "high"
