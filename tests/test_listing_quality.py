"""Listing quality: NAP consistency, category, status, duplicates, review recency, physician↔office."""
from datetime import date, timedelta
from perception import listing_quality as LQ
from perception import delivery as D


def _fixture():
    offices = [
        {"name": "Brand", "address": "1 Anchor Way, Memphis, TN 38100", "website_address": "1 Anchor Way, Memphis, TN 38100", "phone": "(901) 555-0100",
         "place_id": "pa", "is_anchor": True, "sources": ["website"]},
        {"name": "Brand – East", "address": "10 East Rd, Memphis, TN 38104", "website_address": "10 East Rd, Memphis, TN 38104", "phone": "901-555-0200",
         "place_id": "pe", "is_anchor": False, "sources": ["website", "google"]},
        {"name": "Brand – Bartlett", "address": "20 West Ave, Bartlett, TN 38133", "website_address": "", "phone": "",
         "place_id": "pb", "is_anchor": False, "sources": ["google"]},
    ]
    recent = (date.today() - timedelta(days=10)).isoformat() + "T12:00:00Z"
    old = (date.today() - timedelta(days=200)).isoformat() + "T12:00:00Z"
    profiles = [
        {"place_id": "pa", "name": "Brand", "address": "1 Anchor Way, Memphis, TN 38100, USA", "phone": "+1 901-555-0100", "primary_type": "orthopedic_surgeon",
         "status": "OPERATIONAL", "last_review_at": recent, "review_count": 900, "found": True},
        {"place_id": "pe", "name": "Brand East", "address": "10 East Road, Memphis, TN 38104, USA", "phone": "(901) 555-0299", "primary_type": "doctor",
         "status": "OPERATIONAL", "last_review_at": old, "review_count": 12, "found": True},
        {"place_id": "pb", "name": "Brand Bartlett", "address": "20 West Ave, Bartlett, TN 38133, USA", "phone": "", "primary_type": "orthopedic_surgeon",
         "status": "CLOSED_PERMANENTLY", "last_review_at": None, "review_count": 0, "found": True},
    ]
    nppes = [{"address": "1 Anchor Way, Memphis, TN 38100", "city": "Memphis", "state": "TN", "zip": "38100", "phone": "9015550100", "name": "BRAND"},
             {"address": "10 East Rd Ste 2, Memphis, TN 38104", "city": "Memphis", "state": "TN", "zip": "38104", "phone": "", "name": "BRAND"}]
    physicians = [{"name": "Dr. A", "google_address": "10 East Rd, Memphis, TN 38104, USA"},
                  {"name": "Dr. B", "google_address": "500 Hospital Dr, Memphis, TN 38120, USA"},
                  {"name": "Dr. C", "google_address": None}]
    dups = [{"name": "Brand Physical Therapy", "place_id": "px", "address": "10 East Rd, Memphis, TN 38104, USA", "of": "Brand – East"}]
    return offices, profiles, nppes, physicians, dups


def test_audit_counts_and_names():
    offices, profiles, nppes, physicians, dups = _fixture()
    q = LQ.audit(offices=offices, profiles=profiles, nppes=nppes, physicians=physicians, duplicates=dups)
    assert q["phone"] == {"compared": 2, "match": 1, "mismatch": [{"name": "Brand – East", "website": "901-555-0200", "google": "(901) 555-0299"}]}
    assert q["address"]["compared"] == 2 and q["address"]["match"] == 2          # website offices only; Google-only office not compared
    assert q["npi"]["compared"] == 3 and q["npi"]["listed"] == 2 and q["npi"]["missing"] == ["Brand – Bartlett"]
    assert q["category"]["counts"] == {"orthopedic_surgeon": 2, "doctor": 1} and not q["category"]["consistent"] and q["category"]["generic"] == ["Brand – East"]
    assert q["status"]["closed"] == ["Brand – Bartlett"] and q["duplicates"] == dups
    assert q["recency"]["compared"] == 2 and q["recency"]["fresh"] == 1 and q["recency"]["stale"] == ["Brand – East"] and q["recency"]["no_reviews"] == ["Brand – Bartlett"]
    assert q["physicians"] == {"total": 3, "with_google": 2, "at_office": 1, "elsewhere": ["Dr. B"]}
    text = " ".join(LQ.issues(q))
    for needle in ("Google phone differs", "Brand – East", "different Google categories", "marked closed", "second Google profile", "no Google review in 90 days",
                   "no reviews at all", "not one of the practice's offices: Dr. B"):
        assert needle in text, needle
    assert "phone matches the website on 1 of 2" in LQ.summary_line(q) and "2 of 3 physicians have a Google profile, 1 at a practice office" in LQ.summary_line(q)


def test_clean_listings_have_no_issues():
    offices, profiles, nppes, physicians, _ = _fixture()
    offices, profiles = offices[:1], profiles[:1]
    q = LQ.audit(offices=offices, profiles=profiles, nppes=nppes, physicians=[{"name": "Dr. A", "google_address": "1 Anchor Way, Memphis, TN 38100, USA"}], duplicates=[])
    assert LQ.issues(q) == [] and q["category"]["consistent"]


def test_delivery_flag_level_follows_closed_profiles():
    offices, profiles, nppes, physicians, dups = _fixture()
    q = LQ.audit(offices=offices, profiles=profiles, nppes=nppes, physicians=physicians, duplicates=dups)
    base = {"entity_type": "practice", "specialty": "Orthopedics", "generated_at": date.today().isoformat(), "rankings": [{"ai_visibility_score": 70}],
            "website_facts": {"status": "measured"}, "spotcheck": {"asked": 3}, "physician_facts": {"status": "measured", "rows": [{"name": "A"}]},
            "executive_summary_structured": {"headline": "h", "bullets": ["b"]}, "top_recommendation": "x", "ai_visibility_verdict": "v", "listing_quality": q}
    fl = D.check_result(base, group={"type_hint": "practice"})
    assert [f["id"] for f in fl] == ["listing_quality"] and fl[0]["level"] == "fix"
    q2 = dict(q, status={"closed": []})
    fl2 = D.check_result(dict(base, listing_quality=q2), group={"type_hint": "practice"})
    assert fl2[0]["level"] == "warn"
