"""Phase 1 physician-name regression tests — every example is from the four fixture reports."""
from perception.physician_resolver import _clean_name, _looks_like_person, _dedupe_people, name_key, same_person, surname


def test_role_and_specialty_prefixes_are_stripped():
    assert _clean_name("Specialist David Doman") == "David Doman"
    assert _clean_name("Orthopedics Nathan Kukowski") == "Nathan Kukowski"
    assert _clean_name("Specialist Tara Swim") == "Tara Swim"
    assert not _looks_like_person(_clean_name("Dr. Physician Extenders"))   # nothing person-like is left


def test_non_person_strings_are_rejected():
    for junk in ("Physician Extenders", "Our Staff", "Orthopedic Team", "Sports Medicine Specialists", "Physician Assistants", "PA-C Group"):
        assert not _looks_like_person(_clean_name(junk) or junk), junk
    assert _looks_like_person("David Doman")
    assert _looks_like_person("Taylor Den Hartog")


def test_surname_particles_and_same_person():
    assert surname("Taylor Den Hartog") == "den hartog"
    assert same_person("Den Hartog", "Taylor Den Hartog")
    assert same_person("R. Vaughan Massie", "Vaughan Massie")
    assert same_person("J. Smith", "John Smith")
    assert not same_person("John Smith", "Jane Smith")
    assert not same_person("Mark Snoddy", "Mark Snyder")


def test_dedupe_merges_partial_forms_and_keeps_fullest():
    people = [{"name": "Vaughan Massie", "credential": "MD", "specialty": "", "bio_url": ""},
              {"name": "R. Vaughan Massie", "credential": "", "specialty": "Spine", "bio_url": "https://x/bio"},
              {"name": "Den Hartog", "credential": "", "specialty": "", "bio_url": ""},
              {"name": "Taylor Den Hartog", "credential": "DO", "specialty": "", "bio_url": ""},
              {"name": "Karim Mahmoud", "credential": "MD", "specialty": "", "bio_url": ""}]
    out = _dedupe_people(people)
    names = [p["name"] for p in out]
    assert names == ["R. Vaughan Massie", "Taylor Den Hartog", "Karim Mahmoud"]
    assert out[0]["credential"] == "MD" and out[0]["bio_url"] == "https://x/bio"
    assert out[1]["credential"] == "DO"


def test_name_key_uses_particle_surname():
    assert name_key("Taylor Den Hartog") == "den hartog t"
