from perception.data import physician_facts as pf


def test_linkage_and_cert_from_stubbed_registry(monkeypatch):
    def fake_lookup(name, state):
        rec = lambda npi, z: {"number": npi, "addresses": [{"address_purpose": "LOCATION", "postal_code": z}]}
        return {"Ann Linked": [rec("1", "89121")], "Bob Elsewhere": [rec("2", "10001")], "Cy Ambiguous": [rec("3", "89121"), rec("4", "89121")], "Dee Missing": []}[name]
    monkeypatch.setattr(pf, "_nppes_lookup_physician", fake_lookup)
    filler = " Our clinic offers comprehensive orthopaedic care across the valley." * 8
    pages = [{"url": "x", "text": "Dr. Ann Linked is board certified by the American Board of Orthopaedic Surgery." + filler + " Bob Elsewhere joined in 2019 and sees patients on Tuesdays."}]
    f = pf.verify_physicians([{"name": n} for n in ("Ann Linked", "Bob Elsewhere", "Cy Ambiguous", "Dee Missing")],
                             [{"address": "2800 E Desert Inn Rd, Las Vegas, NV 89121"}], "NV", pages)
    assert f["status"] == "measured" and f["checked"] == 2 and f["linked"] == 1 and f["linkage_pct"] == 50
    by = {r["name"]: r for r in f["rows"]}
    assert by["Ann Linked"]["linked"] is True and by["Ann Linked"]["cert_stated"] is True
    assert by["Bob Elsewhere"]["linked"] is False and by["Bob Elsewhere"]["cert_stated"] is False
    assert by["Cy Ambiguous"]["registry"] == "ambiguous" and by["Cy Ambiguous"]["linked"] is None
    assert by["Dee Missing"]["registry"] == "not found"
    assert f["board_cert_unverifiable"] is False
    assert "50%" in pf.evidence_lines(f) and "supporting evidence only" in pf.evidence_lines(f)


def test_no_physicians_is_skipped():
    assert pf.verify_physicians([], [], "NV", [])["status"] == "skipped"


# ── Phase 2 (2.1): linkage matching ──────────────────────────────────────────

def _rec(npi, zips=(), mailing=None, practice=()):
    return {"number": npi,
            "addresses": [{"address_purpose": "LOCATION", "postal_code": z} for z in zips] + ([{"address_purpose": "MAILING", "postal_code": mailing}] if mailing else []),
            "practiceLocations": [{"postal_code": z} for z in practice]}


def _roster(*zips):
    return [{"address": f"{100 + i} Main St, Town, TN {z}"} for i, z in enumerate(zips)]


def test_secondary_practice_locations_and_org_registry_addresses_count(monkeypatch):
    monkeypatch.setattr(pf, "_nppes_lookup_physician", lambda n, s: {"Ann Second": [_rec("1", zips=("10001",), practice=("38120",))],
                                                                      "Bob Admin": [_rec("2", zips=("38199",))]}[n])
    monkeypatch.setattr(pf, "_org_registry_zips", lambda org, st: {"38199"})      # the group's NPI-2 billing office
    f = pf.verify_physicians([{"name": "Ann Second"}, {"name": "Bob Admin"}], _roster("38120", "38138", "38117"), "TN", [], org_name="OrthoSouth")
    by = {r["name"]: r for r in f["rows"]}
    assert by["Ann Second"]["linked"] is True            # via practiceLocations
    assert by["Bob Admin"]["linked"] is True             # via the org's own registry address
    assert f["linkage_pct"] == 100 and "38199" in f["org_zips"]


def test_known_npi_never_falls_back_to_a_stranger(monkeypatch):
    monkeypatch.setattr(pf, "_nppes_by_number", lambda npi: [])
    monkeypatch.setattr(pf, "_nppes_lookup_physician", lambda n, s: [_rec("999", zips=("38120",))])   # same name, different person
    monkeypatch.setattr(pf, "_org_registry_zips", lambda org, st: set())
    f = pf.verify_physicians([{"name": "John Smith", "npi": "123"}], _roster("38120", "38138", "38117"), "TN", [])
    assert f["rows"][0]["registry"] == "not found" and f["rows"][0]["linked"] is None


def test_thin_roster_is_not_measurable(monkeypatch):
    monkeypatch.setattr(pf, "_nppes_lookup_physician", lambda n, s: [_rec("1", zips=("10001",))])
    monkeypatch.setattr(pf, "_org_registry_zips", lambda org, st: set())
    f = pf.verify_physicians([{"name": f"Doc {i} Smith"} for i in range(4)], _roster("38120") + [{"address": "Memphis, TN"}] * 5, "TN", [])
    assert f["linkage_pct"] is None and "too few" in f["linkage_note"]
    assert "NOT MEASURABLE" in pf.evidence_lines(f)


def test_single_office_practice_is_measurable(monkeypatch):
    monkeypatch.setattr(pf, "_nppes_lookup_physician", lambda n, s: [_rec("1", zips=("38120",))])
    monkeypatch.setattr(pf, "_org_registry_zips", lambda org, st: set())
    f = pf.verify_physicians([{"name": "Ann Here"}], _roster("38120"), "TN", [])
    assert f["linkage_pct"] == 100


def test_central_registration_is_not_measurable(monkeypatch):
    monkeypatch.setattr(pf, "_nppes_lookup_physician", lambda n, s: [_rec(n, zips=("30301",))])   # everyone at one billing ZIP
    monkeypatch.setattr(pf, "_org_registry_zips", lambda org, st: set())
    f = pf.verify_physicians([{"name": f"Doc{i} Jones"} for i in range(6)], _roster("30309", "30318", "30327"), "GA", [])
    assert f["linkage_pct"] is None and "central registration" in f["linkage_note"]


def test_unread_physician_pages_are_unknown_not_unverifiable(monkeypatch):
    monkeypatch.setattr(pf, "_nppes_lookup_physician", lambda n, s: [])
    monkeypatch.setattr(pf, "_org_registry_zips", lambda org, st: set())
    pages = [{"url": "x", "text": "Dr. Ann Read is board certified by the American Board of Orthopaedic Surgery. " * 3}]
    f = pf.verify_physicians([{"name": "Ann Read"}, {"name": "Zed Unread"}], _roster("38120"), "TN", pages)
    by = {r["name"]: r for r in f["rows"]}
    assert by["Ann Read"]["cert_stated"] is True and by["Zed Unread"]["cert_stated"] is None
    assert f["cert_checked"] == 1 and f["cert_unread"] == 1 and f["board_cert_unverifiable"] is False
    assert "MEASUREMENT LIMIT: 1 of 2" in pf.evidence_lines(f)
