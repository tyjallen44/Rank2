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
