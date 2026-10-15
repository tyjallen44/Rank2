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


def test_link_words_are_stripped_from_names():
    assert PR._clean_name("Aaron Hanyu-Deutmeyer Click") == "Aaron Hanyu-Deutmeyer"
    assert PR._clean_name("Jane Doe View Profile") == "Jane Doe"
    assert PR.name_key("Aaron Hanyu-Deutmeyer Click") == PR.name_key("Aaron Hanyu-Deutmeyer") or PR._clean_name("Aaron Hanyu-Deutmeyer Click") == "Aaron Hanyu-Deutmeyer"


def test_button_words_in_front_of_names_are_stripped():
    assert PR._clean_name("Now Darin Tessier") == "Darin Tessier"
    assert PR._clean_name("Book Now Lydia Lee") == "Lydia Lee"
    assert PR._clean_name("Darin Tessier") == "Darin Tessier"


# ── Phase 2 (2.2): directory coverage ─────────────────────────────────────────

def test_person_from_sitemap_slug():
    p = PR._person_from_slug("https://brand.example/doctors/alan-b-carter-md")
    assert p and p["name"] == "Alan B. Carter" and p["credential"] == "MD" and p["bio_url"].endswith("alan-b-carter-md")
    assert PR._person_from_slug("https://brand.example/doctors/request-appointment") is None
    assert PR._person_from_slug("https://brand.example/locations/east-office") is None
    assert PR._person_from_slug("https://brand.example/providers/pat-kim-pa-c")["credential"] == "PA-C"


def test_sitemap_bios_join_the_directory_read(monkeypatch):
    class F:
        browser = None
        def __init__(self): self.pages = {
            "https://brand.example/": "<html><a href='/doctors/'>Our Doctors</a></html>",
            "https://brand.example/sitemap.xml": "<urlset><url><loc>https://brand.example/doctors/alan-b-carter-md</loc></url><url><loc>https://brand.example/doctors/jane-doe-do</loc></url><url><loc>https://brand.example/about</loc></url></urlset>",
            "https://brand.example/doctors/": "<html>" + "<p>Featured: Dr. Sam Lee</p>" + "x" * 3000 + "</html>"}
        def html(self, url, allow_browser=True): return self.pages.get(url)
        def close(self): pass
    res = PR.website_physicians("https://brand.example", fetcher=F())
    names = {p["name"] for p in res["physicians"]}
    assert {"Alan B. Carter", "Jane Doe", "Sam Lee"} <= names and res["sitemap_bios"] == 2


def test_not_on_website_requires_credible_directory_coverage(monkeypatch):
    web = [{"name": "Only One", "credential": "MD", "specialty": "", "bio_url": ""}]
    npp = [{"name": f"Reg Doc{i}", "npi": str(i), "credential": "MD", "specialty": ""} for i in range(10)]
    _env(monkeypatch, web=web, npp=npp)
    res = PR.resolve_physicians("Brand Ortho", "Memphis", "TN", website="https://brand.example")
    assert not any(p["website_missing"] for p in res["physicians"])          # 1 of 10 found: a crawl limitation
    cov = res["resolution"]["directory_coverage"]
    assert cov == {"website": 1, "registry": 10, "credible": False, "pct": 10}
    assert res["resolution"]["registry_only"] == []


def test_sitemap_bio_slugs_under_find_a_provider_name_their_physician():
    from perception.physician_resolver import _person_from_slug, is_physician_cred
    p = _person_from_slug("https://www.dmos.com/find-a-provider/mauricio-acebey-md")
    assert p and p["name"] == "Mauricio Acebey" and p["credential"] == "MD" and is_physician_cred(p["credential"]) is True
    mid = _person_from_slug("https://www.dmos.com/find-a-provider/karissa-junior-arnp")
    assert mid and mid["credential"] == "ARNP" and is_physician_cred(mid["credential"]) is False
    pa = _person_from_slug("https://www.dmos.com/find-a-provider/kara-buckingham-pa-c")
    assert pa and pa["credential"] == "PA-C" and is_physician_cred(pa["credential"]) is False
    assert _person_from_slug("https://www.dmos.com/find-a-provider") is None
    assert _person_from_slug("https://www.dmos.com/services/physical-therapy") is None
