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
