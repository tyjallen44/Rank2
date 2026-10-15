"""Phase 1 roster regression tests (report-quality cleanup, 2026-10-15).

Every example below is a real contaminant or phantom taken from the four fixture reports
(Georgia Carolina Orthopedics, DMOS Orthopaedic Centers, OrthoSouth, IBJI)."""
from bs4 import BeautifulSoup

from perception.location_resolver import (listing_belongs, display_name, _text_locations, _jsonld_locations, _LABELS)


def _c(name, address, *, types=(), primary=None, website=None):
    return {"name": name, "address": address, "types": list(types), "primary_type": primary, "website": website, "place_id": name}


# ── 1.1 unrelated businesses ────────────────────────────────────────────────

GCO = "Georgia Carolina Orthopedics"
DMOS = "Dmos Orthopaedic Centers - West Des Moines"


def test_gco_acronym_alone_never_matches():
    ok, why = listing_belongs(GCO, _c("GCO Landscape", "10 Main St, Sharpsburg, GA 30277, USA", types=["general_contractor", "point_of_interest"], primary="general_contractor"),
                              city="Augusta", state="GA", specialty="Orthopedics")
    assert not ok and why.startswith("category")
    ok, why = listing_belongs(GCO, _c("GCO Mechanical", "5 Oak Rd, Doraville, GA 30340, USA"), city="Augusta", state="GA", specialty="Orthopedics")
    assert not ok                                   # no types known: the acronym alone still does not carry it


def test_gco_real_offices_and_dbas_match():
    assert listing_belongs(GCO, _c("Georgia Carolina Orthopedics - Aiken", "1 Medical Park, Aiken, SC 29801, USA", types=["doctor", "health"], primary="orthopedic_surgeon"),
                           city="Augusta", state="GA", specialty="Orthopedics")[0]
    ok, why = listing_belongs(GCO, _c("Karim Mahmoud MD - Georgia Carolina Orthopedics", "2 Wheeler Rd, Augusta, GA 30909, USA", types=["doctor"]),
                              city="Augusta", state="GA", specialty="Orthopedics")
    assert not ok and why == "physician-profile"           # a doctor's own profile at an office is not a location
    assert listing_belongs(GCO, _c("GCO Orthopedics Thomson", "3 Hill St, Thomson, GA 30824, USA"), city="Augusta", state="GA")[0]
    ok, why = listing_belongs(GCO, _c("Some Other Name", "4 Elm St, Augusta, GA 30901, USA", website="https://www.georgiacarolinaortho.com/aiken"),
                              domain="georgiacarolinaortho.com", city="Augusta", state="GA")
    assert ok and why == "domain"


def test_dmos_place_name_tokens_never_match():
    for name, types, primary in [
        ("West Des Moines City Hall", ["city_hall", "local_government_office"], "city_hall"),
        ("West Des Moines Public Works", ["local_government_office"], "local_government_office"),
        ("West Des Moines Chamber of Commerce", ["point_of_interest"], None),
        ("West Des Moines Community Schools", ["school"], "school"),
        ("West Des Moines OBGYN Associates, PC", ["doctor", "health"], "obstetrician_gynecologist"),
        ("MercyOne West Des Moines Medical Center", ["hospital", "health"], "hospital"),
        ("The Iowa Clinic Orthopaedic Department - West Des Moines Campus", ["doctor", "health"], "doctor"),
        ("UnityPoint Clinic Family Medicine - West Des Moines", ["doctor", "health"], "doctor"),
        ("DoctorsNow West Des Moines", ["doctor"], "doctor"),
        ("Harmony Dental", ["dentist"], "dentist"),
    ]:
        ok, why = listing_belongs(DMOS, _c(name, f"1 Jordan Creek Pkwy, West Des Moines, IA 50266, USA", types=types, primary=primary),
                                  city="West Des Moines", state="IA", specialty="Orthopedics")
        assert not ok, (name, why)


def test_dmos_own_offices_match():
    for name in ("DMOS Orthopaedic Centers - Urbandale", "DMOS Urgent Injury Clinic West Des Moines", "DMOS Orthopaedic Centers – Ankeny"):
        assert listing_belongs(DMOS, _c(name, "2 Prairie Trail, Ankeny, IA 50023, USA", types=["doctor", "health"], primary="doctor"),
                               city="West Des Moines", state="IA", specialty="Orthopedics")[0], name


def test_generic_brand_requires_every_word():
    brand = "Des Moines Orthopaedic Surgeons"
    assert not listing_belongs(brand, _c("West Des Moines City Hall", "1 Main, West Des Moines, IA 50265, USA"), city="West Des Moines", state="IA")[0]
    assert listing_belongs(brand, _c("Des Moines Orthopaedic Surgeons – Ankeny", "2 Oak, Ankeny, IA 50023, USA"), city="West Des Moines", state="IA")[0]


def test_hospital_type_allowed_only_for_hospital_specialty():
    cand = _c("Northside Hospital Forsyth", "1 Hospital Dr, Cumming, GA 30041, USA", types=["hospital"], primary="hospital")
    assert not listing_belongs("Northside Hospital", cand, city="Atlanta", state="GA", specialty="Orthopedics")[0]
    assert listing_belongs("Northside Hospital", cand, city="Atlanta", state="GA", specialty="Hospital")[0]


# ── 1.2 phantom locations ───────────────────────────────────────────────────

def test_section_headings_never_become_office_names():
    brand = "Georgia Carolina Orthopedics"
    assert display_name(brand, None, "Office Locations", "Augusta") == f"{brand} – Augusta"
    assert display_name(brand, None, "Our Locations", "Augusta") == f"{brand} – Augusta"
    assert display_name("DMOS Orthopaedic Centers", None, "West Des Moines – Ankeny", "Ankeny") == "DMOS Orthopaedic Centers – Ankeny"
    assert display_name("DMOS Orthopaedic Centers", None, "Mailing Address", "Ankeny") == "DMOS Orthopaedic Centers – Ankeny"
    # a real office descriptor still names the office
    assert display_name(brand, None, "Aiken Clinic", "Aiken") == f"{brand} – Aiken Clinic"
    assert display_name("GCO Orthopedics", None, "GCO Aiken", "Aiken") == "GCO Aiken"   # shares brand tokens


def test_label_regex_covers_new_headings():
    for h in ("Office Locations", "All Locations", "Locations & Hours", "Mailing Address", "Corporate Office", "Headquarters", "Hours of Operation"):
        assert _LABELS.match(h), h


def test_mailing_and_po_box_addresses_are_not_offices():
    text = """Ankeny Clinic
2 Prairie Trail, Ankeny, IA 50023
(515) 555-0100

Mailing Address
PO Box 1234, West Des Moines, IA 50265

Billing
100 Corporate Way, West Des Moines, IA 50266
"""
    locs = _text_locations(text, "DMOS")
    addrs = [l["address"] for l in locs]
    assert any("Prairie Trail" in a for a in addrs)
    assert not any("Corporate Way" in a for a in addrs)
    assert not any("Box" in a for a in addrs)


def test_jsonld_person_and_postal_nodes_are_skipped():
    html = """<script type="application/ld+json">[
      {"@type": "MedicalClinic", "name": "IBJI Avondale", "address": {"streetAddress": "3000 N Halsted St", "addressLocality": "Chicago", "addressRegion": "IL", "postalCode": "60657"}},
      {"@type": "Person", "name": "Jane Doe", "address": {"streetAddress": "1 Mailing Rd", "addressLocality": "Chicago", "addressRegion": "IL", "postalCode": "60601"}},
      {"@type": "Organization", "name": "IBJI", "address": {"streetAddress": "PO Box 55 (mailing)", "addressLocality": "Chicago", "addressRegion": "IL", "postalCode": "60601"}}
    ]</script>"""
    locs = _jsonld_locations(BeautifulSoup(html, "html.parser"))
    assert [l["name"] for l in locs] == ["IBJI Avondale"]


# ── live re-run follow-ups (2026-10-15) ───────────────────────────────────────
from perception.location_resolver import street_key, strip_city_suffix, dedupe_roster


def test_uninformative_google_types_do_not_reject_a_brand_named_listing():
    ok, why = listing_belongs(GCO, _c("Georgia Carolina Orthopedics", "3650 J Dewey Gray Cir, Augusta, GA 30909, USA", types=["point_of_interest", "establishment"]),
                              city="Augusta", state="GA", specialty="Orthopedics")
    assert ok, why


def test_physician_profile_listings_are_not_offices():
    ok, why = listing_belongs(GCO, _c("Maxwell J. Vogel, MD - Georgia Carolina Orthopedics", "2511 Associates Way, Evans, GA 30809, USA", types=["doctor"]),
                              city="Augusta", state="GA")
    assert not ok and why == "physician-profile"
    assert listing_belongs(GCO, _c("Georgia Carolina Orthopedic Urgent Care", "4369 Evans to Locks Rd, Evans, GA 30809, USA", types=["doctor"]), city="Augusta", state="GA")[0]


def test_street_key_ignores_leading_directional():
    assert street_key("4850 NW 100th St, Urbandale, IA 50322") == street_key("4850 100th St, Urbandale, IA 50322") == "4850 100th"
    assert street_key("210 E DeRenne Ave") == "210 derenne"


def test_brand_loses_its_own_city_suffix():
    assert strip_city_suffix("Dmos Orthopaedic Centers - West Des Moines", "West Des Moines") == "Dmos Orthopaedic Centers"
    assert strip_city_suffix("OrthoSouth", "Memphis") == "OrthoSouth"
    assert display_name("Dmos Orthopaedic Centers", None, "Urbandale Clinic", "Urbandale") == "Dmos Orthopaedic Centers – Urbandale Clinic"


def test_dedupe_roster_drops_city_only_rows_duplicates_and_anchor():
    locs = [{"name": "OrthoSouth", "address": "1244 Primacy Pkwy, Memphis, TN 38119", "place_id": "A"},
            {"name": "OrthoSouth (1244 Primacy Pkwy)", "address": "1244 Primacy Pkwy, Memphis, TN"},
            {"name": "Dmos – West Des Moines", "address": "West Des Moines, IA, USA", "place_id": "B"},
            {"name": "Anchor dup", "address": "6286 Briarcrest Ave, Memphis, TN", "place_id": "ANCH"},
            {"name": "Germantown", "address": "2100 Exeter Rd #200, Germantown, TN 38138", "place_id": "C"}]
    out = dedupe_roster(locs, anchor_place_id="ANCH")
    assert [l["name"] for l in out] == ["OrthoSouth", "Germantown"]
