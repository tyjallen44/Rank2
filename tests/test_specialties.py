"""Controlled specialty labels + What to Do First priority order."""
from perception.specialties import normalize_specialty
from perception.plain import prioritize_first_moves


def test_specialty_synonyms_collapse():
    for raw in ("ortho", "Orthopaedics", "ORTHOPEDIC SURGERY", "Orthopedics & Sports Medicine"):
        assert normalize_specialty(raw) in ("Orthopedics", "Sports Medicine")
    assert normalize_specialty("Orthopaedics") == "Orthopedics"
    assert normalize_specialty("Orthopedics & Sports Medicine") == "Orthopedics"   # the longer keyword (orthoped) wins
    assert normalize_specialty("orthodontics") == "Orthodontics"
    assert normalize_specialty("ENT") == "Otolaryngology (ENT)"
    assert normalize_specialty("Dentistry") == "Dentistry"            # 'ent' only matches as a word
    assert normalize_specialty("GI") == "Gastroenterology"
    assert normalize_specialty("OB/GYN") == "Obstetrics & Gynecology (OB/GYN)"
    assert normalize_specialty("cardiac surgery") == "Cardiothoracic Surgery"
    assert normalize_specialty("family practice") == "Family Medicine"


def test_unknown_specialty_keeps_wording():
    assert normalize_specialty("naturopathic medicine") == "Naturopathic Medicine"
    assert normalize_specialty("") == "" and normalize_specialty(None) is None


def test_first_moves_priority_order():
    items = ["Publish Wikidata and Wikipedia entries so AI models have a trusted source.",
             "Fix your Google listings — 2 of 4 don't link to your site.",
             "Allow AI crawlers through your firewall; nothing you publish reaches them until then.",
             "Publish crawlable physician bios with board certification.",
             "Grow reviews at the two locations under 50."]
    out = prioritize_first_moves(items)
    assert out[0].startswith("Allow AI crawlers")
    assert out[1].startswith("Publish crawlable physician")
    assert out[2].startswith("Fix your Google")
    assert out[3].startswith("Grow reviews")
    assert out[4].startswith("Publish Wikidata")
