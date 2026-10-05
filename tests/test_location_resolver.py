"""Location resolver: website office list → NPPES → Google, model recall last; sources and gap flags."""
from bs4 import BeautifulSoup
from perception import location_resolver as LR


def test_text_addresses_named_by_nearest_heading():
    text = """Our Locations
Germantown Office
7580 Clinic Dr
Germantown, TN 38138
Phone: (901) 555-0100
Get Directions
Southaven
7580 Airways Blvd Suite 200, Southaven, MS 38671
662.555.0101
Fax 662.555.0102
"""
    locs = LR._text_locations(text, "OrthoSouth")
    assert [l["name"] for l in locs] == ["Germantown Office", "Southaven"]
    assert locs[0]["address"] == "7580 Clinic Dr, Germantown, TN 38138" and locs[0]["phone"] == "(901) 555-0100"
    assert locs[1]["city"] == "Southaven" and locs[1]["zip"] == "38671" and locs[1]["phone"] == "662.555.0101"


def test_text_address_without_comma_moves_street_type_back():
    locs = LR._text_locations("Main Office\n1234 Main Street Memphis, TN 38120\n", "Brand")
    assert locs[0]["address"].startswith("1234 Main Street,") and locs[0]["city"] == "Memphis"


def test_jsonld_locations_walks_nested_objects():
    html = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"MedicalOrganization","name":"Brand",
      "location":[{"@type":"MedicalClinic","name":"Brand – East","telephone":"901-555-0001",
                   "address":{"@type":"PostalAddress","streetAddress":"10 East Rd","addressLocality":"Memphis","addressRegion":"TN","postalCode":"38104"}},
                  {"@type":"MedicalClinic","name":"Brand – West",
                   "address":{"streetAddress":"20 West Ave, Suite 3","addressLocality":"Bartlett","addressRegion":"TN","postalCode":"38133"}}]}</script>"""
    locs = LR._jsonld_locations(BeautifulSoup(html, "html.parser"))
    assert [l["name"] for l in locs] == ["Brand – East", "Brand – West"]
    assert locs[0]["phone"] == "901-555-0001" and locs[1]["zip"] == "38133"


def test_same_office_and_brand_matching():
    assert LR.same_office("7580 Clinic Dr, Germantown, TN 38138", "7580 Clinic Drive, Germantown, TN 38138, USA")
    assert not LR.same_office("7580 Clinic Dr, Germantown, TN 38138", "7580 Clinic Dr, Memphis, TN 38104")
    assert not LR.same_office("100 Main St, X, TN 38100", "200 Main St, X, TN 38100")
    assert LR.brand_matches("OrthoSouth", "OrthoSouth Germantown")
    assert LR.brand_matches("Illinois Bone and Joint Institute", "IBJI Doctors' Office - Glenview")
    assert LR.brand_matches("Campbell Clinic Orthopaedics", "Campbell Clinic - Midtown")
    assert not LR.brand_matches("OrthoSouth", "Memphis Dental Group")


def _fake_env(monkeypatch, *, web_locs, google, nppes=(), model=None, anchor_addr="1 Anchor Way, Memphis, TN 38100"):
    monkeypatch.setattr(LR, "website_locations", lambda url, brand, emit=None, fetcher=None: {
        "status": "measured" if web_locs else "none", "url": url, "page": url + "/locations", "pages_read": 2, "locations": list(web_locs)})
    monkeypatch.setattr(LR, "nppes_cities", lambda org, st: list(nppes))

    def _search(name, city, state, n=10):
        out = []
        for g in google:
            if (city or "").lower() in (g["address"].lower() if city else "") or not city or city.lower() in name.lower():
                out.append(dict(g))
        return out
    monkeypatch.setattr(LR, "_search", _search)

    class _Read:
        place_id, formatted_address, website = "anchor-pid", anchor_addr, "https://brand.example"
    import perception.data.places as P
    monkeypatch.setattr(P, "fetch_provider", lambda n, c, s: (_Read(), None))
    calls = {"model": 0}
    import perception.practice_discovery as PD
    def _model(name, city, state, on_event=None, force_rerun=False):
        calls["model"] += 1
        return list(model or []), "Brand Parent"
    monkeypatch.setattr(PD, "discover_practice_siblings", _model)
    return calls


def test_resolve_pins_website_offices_and_flags_gaps(monkeypatch):
    web = [{"name": "Brand – East", "address": "10 East Rd, Memphis, TN 38104", "city": "Memphis", "state": "TN", "zip": "38104", "phone": ""},
           {"name": "Brand – Hidden", "address": "55 Nowhere Ln, Collierville, TN 38017", "city": "Collierville", "state": "TN", "zip": "38017", "phone": ""},
           {"name": "Anchor", "address": "1 Anchor Way, Memphis, TN 38100", "city": "Memphis", "state": "TN", "zip": "38100", "phone": ""}]
    google = [{"name": "Brand Orthopedics East", "address": "10 East Rd, Memphis, TN 38104, USA", "place_id": "g-east", "rating": 4.6, "review_count": 120},
              {"name": "Brand Orthopedics Bartlett", "address": "20 West Ave, Bartlett, TN 38133, USA", "place_id": "g-bart", "rating": 4.2, "review_count": 40},
              {"name": "Brand Orthopedics", "address": "1 Anchor Way, Memphis, TN 38100, USA", "place_id": "anchor-pid", "rating": 4.8, "review_count": 900}]
    calls = _fake_env(monkeypatch, web_locs=web, google=google, nppes=[("Bartlett", "TN")])
    res = LR.resolve_locations("Brand Orthopedics", "Memphis", "TN", website="https://brand.example", use_registry=False)
    sib = {s["name"]: s for s in res["siblings"]}
    assert "Anchor" not in sib and "Brand Orthopedics" not in sib            # the analyzed listing is never a sibling
    east = sib["Brand Orthopedics East"]                                      # website office pinned to its Google listing
    assert east["place_id"] == "g-east" and east["sources"] == ["website", "google"] and not east["google_missing"]
    hidden = sib["Brand – Hidden"]
    assert hidden["google_missing"] and "place_id" not in hidden and hidden["sources"] == ["website"]
    bart = sib["Brand Orthopedics Bartlett"]                                  # Google listing the website omits
    assert bart["website_missing"] and bart["sources"] == ["google", "nppes"]
    r = res["resolution"]
    assert r["website_only"] == ["Brand – Hidden"] and r["google_only"] == ["Brand Orthopedics Bartlett"]
    assert r["google"]["pinned"] == 1 and r["google"]["extra"] == 1 and r["model"]["used"] is False
    assert calls["model"] == 0


def test_model_is_last_resort_only(monkeypatch):
    calls = _fake_env(monkeypatch, web_locs=[], google=[], model=[{"name": "Brand – Recalled", "entity_type": "practice", "city": "Memphis", "state": "TN"}])
    res = LR.resolve_locations("Brand Orthopedics", "Memphis", "TN", website="https://brand.example", use_registry=False)
    assert calls["model"] == 1 and [s["sources"] for s in res["siblings"]] == [["model"]]
    assert res["resolution"]["model"]["used"] is True and res["parent_org_name"] == "Brand Parent"
    # a Google-only roster does not call the model
    google = [{"name": "Brand Orthopedics Bartlett", "address": "20 West Ave, Bartlett, TN 38133, USA", "place_id": "g-bart", "rating": 4.2, "review_count": 40}]
    calls = _fake_env(monkeypatch, web_locs=[], google=google)
    res = LR.resolve_locations("Brand Orthopedics", "Memphis", "TN", website=None, use_registry=False)
    assert calls["model"] == 0 and len(res["siblings"]) == 1 and res["siblings"][0]["website_missing"] is False


def test_registry_roundtrip_keeps_sources(monkeypatch):
    from perception import entity_registry as ER
    store = {}
    class _Con:
        def execute(self, sql, params=None):
            if sql.strip().startswith("DELETE"):
                store.clear(); return self
            if sql.strip().startswith("INSERT"):
                store[params[0]] = params; return self
            self._rows = [(p[2], p[3], p[4], p[5], None, p[8]) for p in store.values()]; return self
        def fetchall(self): return self._rows
        def close(self): pass
    monkeypatch.setattr(ER, "get_connection", lambda: _Con())
    ER.save_registry_siblings("Brand", "Memphis", "TN", [{"name": "Brand – East", "city": "Memphis", "state": "TN", "address": "10 East Rd, Memphis, TN 38104",
                                                           "place_id": "g-east", "sources": ["website", "google"], "google_missing": False, "website_missing": False}])
    got = ER.get_registry_siblings("Brand", "Memphis", "TN")
    assert got[0]["place_id"] == "g-east" and got[0]["sources"] == ["website", "google"] and got[0]["address"].startswith("10 East Rd")


def test_display_name_tells_offices_apart():
    assert LR.display_name("OrthoSouth", "OrthoSouth", "OrthoSouth – Memphis Poplar", "Memphis") == "OrthoSouth – Memphis Poplar"
    assert LR.display_name("OrthoSouth", "OrthoSouth Germantown | Orthopedic Clinic", "OrthoSouth – Germantown", "Germantown") == "OrthoSouth Germantown"
    assert LR.display_name("OrthoSouth", "OrthoSouth", None, "Covington") == "OrthoSouth – Covington"
    assert LR.display_name("Campbell Clinic", None, "Wolf River", "Germantown") == "Campbell Clinic – Wolf River"
    assert LR.display_name("Campbell Clinic", None, "Collierville", "Collierville") == "Campbell Clinic – Collierville"
