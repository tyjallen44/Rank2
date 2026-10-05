"""Physician resolver: website directory → NPI registry, model last; sources, gap flags, hospital cap."""
from bs4 import BeautifulSoup
from perception import physician_resolver as PR


def test_people_from_directory_links_text_and_schema():
    html = """
    <script type="application/ld+json">{"@type":"Physician","name":"Alan B. Carter, MD","medicalSpecialty":"Orthopedic Surgery","url":"/doctors/alan-carter"}</script>
    <ul>
      <li><a href="/doctors/jane-doe-md">Jane Doe, MD</a></li>
      <li><a href="/doctors/sam-lee">Dr. Sam Lee</a></li>
      <li><a href="/doctors/pat-kim-pa-c">Pat Kim, PA-C</a></li>
      <li><a href="/locations/east">East Office</a></li>
      <li><a href="/doctors/request-appointment">Request Appointment</a></li>
    </ul>
    <p>Our surgeons include Maria Gonzalez, DO, FAAOS and Dr. Wei Chen.</p>"""
    people = PR._people_from_page(BeautifulSoup(html, "html.parser"), "https://brand.example", "brand.example")
    names = {p["name"]: p for p in people}
    assert "Alan B. Carter" in names and names["Alan B. Carter"]["credential"] == "MD"
    assert names["Jane Doe"]["credential"] == "MD" and names["Jane Doe"]["bio_url"].endswith("/doctors/jane-doe-md")
    assert "Sam Lee" in names and "Maria Gonzalez" in names and names["Maria Gonzalez"]["credential"] == "DO, FAAOS"
    assert "Wei Chen" in names and names["Pat Kim"]["credential"] == "PA-C"
    assert "East Office" not in names and "Request Appointment" not in names


def test_physician_vs_midlevel_and_name_key():
    assert PR.is_physician_cred("MD, FAAOS") is True and PR.is_physician_cred("DPM") is True
    assert PR.is_physician_cred("PA-C") is False and PR.is_physician_cred("DPT") is False
    assert PR.is_physician_cred("") is None
    assert PR.name_key("Dr. Jane A. Doe, MD") == PR.name_key("Jane Doe") == "doe j"
    assert PR.name_key("William Smith Jr.") == "smith w"
    assert not PR._looks_like_person("Sports Medicine") and not PR._looks_like_person("Meet Our Team")
    assert PR._looks_like_person("Mary-Kate O'Neil")


def _env(monkeypatch, *, web, npp, by_name=None, model=None):
    monkeypatch.setattr(PR, "website_physicians", lambda url, hint_urls=None, fetcher=None, emit=None: {
        "status": "measured" if web else "none", "directory": "/doctors", "pages_read": 2,
        "physicians": [dict(p, sources=["website"]) for p in web], "midlevels": []})
    import perception.physician_discovery as PD
    monkeypatch.setattr(PD, "_nppes_lookup", lambda org, city, state: list(npp))
    calls = {"model": 0}
    def _model(name, city, state, on_event=None):
        calls["model"] += 1
        return list(model or [])
    monkeypatch.setattr(PD, "_claude_discover", _model)
    import perception.data.physician_facts as PF
    monkeypatch.setattr(PF, "_nppes_lookup_physician", lambda name, state: list((by_name or {}).get(name, [])))
    return calls


def test_merge_website_and_registry_with_gap_flags(monkeypatch):
    web = [{"name": "Jane Doe", "credential": "MD", "specialty": "", "bio_url": "/doctors/jane-doe"},
           {"name": "Sam Lee", "credential": "", "specialty": "", "bio_url": ""},
           {"name": "Nick Name", "credential": "MD", "specialty": "", "bio_url": ""}]
    npp = [{"name": "Jane A Doe", "npi": "111", "credential": "MD", "specialty": "Orthopaedic Surgery"},
           {"name": "Gone Person", "npi": "333", "credential": "DO", "specialty": "Orthopaedic Surgery"}]
    by_name = {"Sam Lee": [{"number": "222", "basic": {"credential": "M.D."}, "taxonomies": [{"primary": True, "desc": "Sports Medicine"}]}]}
    calls = _env(monkeypatch, web=web, npp=npp, by_name=by_name)
    res = PR.resolve_physicians("Brand Ortho", "Memphis", "TN", website="https://brand.example")
    ps = {p["name"]: p for p in res["physicians"]}
    assert ps["Jane Doe"]["npi"] == "111" and ps["Jane Doe"]["sources"] == ["website", "nppes"] and ps["Jane Doe"]["specialty"] == "Orthopaedic Surgery"
    assert ps["Sam Lee"]["npi"] == "222" and ps["Sam Lee"]["credential"] == "MD" and "nppes" in ps["Sam Lee"]["sources"]
    assert ps["Nick Name"]["npi_missing"] and ps["Nick Name"]["npi"] is None
    assert ps["Gone Person"]["website_missing"] and ps["Gone Person"]["sources"] == ["nppes"]
    assert PR._clean_name("ERIC MCKENNA O'NEIL") == "Eric McKenna O'Neil"
    r = res["resolution"]
    assert r["both"] == 2 and r["website_only"] == ["Nick Name"] and r["registry_only"] == ["Gone Person"] and r["model"]["used"] is False
    assert calls["model"] == 0 and [p["name"] for p in res["physicians"]][:3] == ["Jane Doe", "Sam Lee", "Nick Name"]   # website order first


def test_model_last_resort_and_hospital_cap(monkeypatch):
    calls = _env(monkeypatch, web=[], npp=[], model=[{"name": "Recalled Doc", "npi": None, "credential": "MD", "specialty": "Ortho"}])
    res = PR.resolve_physicians("Brand Ortho", "Memphis", "TN", website="https://brand.example")
    assert calls["model"] == 1 and res["physicians"][0]["sources"] == ["model"] and res["resolution"]["model"]["used"]
    npp = [{"name": f"Doc {i}", "npi": str(i), "credential": "MD", "specialty": ""} for i in range(80)]
    web = [{"name": "Site Doc", "credential": "MD", "specialty": "", "bio_url": ""}]
    calls = _env(monkeypatch, web=web, npp=npp)
    res = PR.resolve_physicians("Big Hospital", "Memphis", "TN", website="https://h.example", cap=PR.HOSPITAL_CAP)
    assert len(res["physicians"]) == 50 and res["resolution"]["capped"] and res["physicians"][0]["name"] == "Site Doc"
    res2 = PR.resolve_physicians("Brand Ortho", "Memphis", "TN", website="https://h.example")      # practices: never capped
    assert len(res2["physicians"]) == 81 and calls["model"] == 0
