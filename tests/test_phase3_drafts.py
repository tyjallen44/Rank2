"""Cleanup Phase 3 — drafted content quality (3.1–3.8). Offline; no model calls."""
import json, pathlib
import pytest

FX = pathlib.Path(__file__).parent / "fixtures" / "cleanup"


def _result(key):
    f = FX / f"{key}.json"
    if not f.exists():
        pytest.skip("fixtures not captured")
    from perception.models import AnalysisResult
    d = json.loads(f.read_text())
    return AnalysisResult.model_validate(d.get("result", d))


# 3.3 ─────────────────────────────────────────────────────────────────────────
def test_schema_specialty_map_uses_only_vocabulary_members():
    from perception.specialties import _SCHEMA_MAP, SCHEMA_MEDICAL_SPECIALTIES, schema_specialties
    for canon, (vals, _) in _SCHEMA_MAP.items():
        assert vals and all(v in SCHEMA_MEDICAL_SPECIALTIES for v in vals), canon
    assert "Orthopedic" not in SCHEMA_MEDICAL_SPECIALTIES
    assert schema_specialties("Orthopedics") == ["Musculoskeletal"]
    assert schema_specialties("Orthopaedic Surgery", surgical=True) == ["Musculoskeletal", "Surgical"]
    assert schema_specialties("Neurosurgery") == ["Neurologic", "Surgical"]
    assert schema_specialties("Pain Management") == []            # no member: the property is omitted
    assert schema_specialties("OB/GYN") == ["Obstetric", "Gynecologic"]


def test_fix_draft_replaces_invalid_specialty_and_removes_it_when_none_fits():
    from perception.draft_facts import fix_draft
    d = '{"@type": "MedicalOrganization", "medicalSpecialty": "Orthopedic", "name": "X"}'
    assert '"medicalSpecialty": ["Musculoskeletal", "Surgical"]' in fix_draft(d, ["Musculoskeletal", "Surgical"])
    assert '"medicalSpecialty": "Musculoskeletal"' in fix_draft(d, ["Musculoskeletal"])
    out = fix_draft(d, [])
    assert "medicalSpecialty" not in out and '"name": "X"' in out
    assert fix_draft('"medicalSpecialty": "Podiatric"', ["Musculoskeletal"]) == '"medicalSpecialty": "Podiatric"'   # already valid: untouched


# 3.4 ─────────────────────────────────────────────────────────────────────────
def test_canonical_url_strips_tracking_and_normalizes():
    from perception.data.places import canonical_url
    assert canonical_url("https://orthosouth.org/?scct=PNca150b&utm_source=x") == "https://orthosouth.org/"
    assert canonical_url("www.dmos.com/find-a-provider/?fbclid=abc#top") == "https://www.dmos.com/find-a-provider"
    assert canonical_url("https://Example.com/a/b/?page=2&gclid=1") == "https://example.com/a/b?page=2"
    assert canonical_url("") == "" and canonical_url(None) == ""


def test_fix_draft_strips_tracking_parameters_from_urls_in_text():
    from perception.draft_facts import fix_draft
    out = fix_draft("P856 (official website) = https://orthosouth.org/?scct=PNca150b\nSee https://x.org/p?id=3&utm_medium=e", [], "wikidata_edit")
    assert "scct" not in out and "utm_medium" not in out and "https://x.org/p?id=3" in out


# 3.5 ─────────────────────────────────────────────────────────────────────────
def test_fix_draft_drops_inapplicable_wikidata_lines():
    from perception.draft_facts import fix_draft
    d = "P31 (instance of) = Q4287745 (medical organization)\nP1056 (product or material produced) or P1344 — N/A\nP856 (official website) = https://a.org\n- P17 (country): not applicable"
    out = fix_draft(d, [], "wikidata_edit")
    assert "P1056" not in out and "P17" not in out and "P31" in out and "P856" in out


# 3.1 / 3.2 ───────────────────────────────────────────────────────────────────
def test_facts_carry_organization_locations_and_physicians_from_the_report():
    from perception.draft_facts import build_facts, facts_lines
    r = _result("dmos")
    facts = build_facts(r, {"website_urls": ["https://www.dmos.com/?utm_source=g"]}, {"specialty": r.specialty})
    assert facts["website_urls"] == ["https://www.dmos.com/"]
    assert facts["organization"] and facts["locations"] and facts["physicians"]
    assert all(l["address"] for l in facts["locations"]) and all(p["npi"] for p in facts["physicians"])
    assert facts["schema_specialties"][0] == "Musculoskeletal"
    lines = "\n".join(facts_lines("DMOS Orthopaedic Centers", r.location, "practice", facts))
    assert "Organization name" in lines and "LOCATIONS (verified" in lines and "PHYSICIANS (verified" in lines
    assert "never rename every profile" in lines and "one filled block per physician" in lines
    assert "NPI " in lines and "phone " in lines


def test_draft_label_marks_templates():
    from perception.content_report_pdf import _draft_label
    f = {"remediation_type": "schema_markup", "draft_content": "[VERIFY: a] [VERIFY: b] [VERIFY: c] [VERIFY: d]"}
    assert _draft_label(f).startswith("Template — 4 [VERIFY]")
    f["draft_content"] = '{"name": "X"}'
    assert _draft_label(f) == "Drafted content (ready to publish)"
    f["draft_content"] = '{"name": "X", "telephone": "[VERIFY: phone]"}'
    assert "1 [VERIFY] value to confirm" in _draft_label(f)


# 3.6 ─────────────────────────────────────────────────────────────────────────
def test_final_render_pass_has_no_marker_text():
    from perception.content_report_pdf import _content_body_html
    from perception.models import ContentFindings, ContentFinding
    fx = ContentFindings(findings=[ContentFinding(finding_id="CIK-001", platform="website", category="missing", severity="high",
                                                  teaser_summary="No healthcare schema", remediation_type="schema_markup", draft_content="{}")])
    measured = _content_body_html("Org", "Memphis, TN", fx, "Org", None, None)
    final = _content_body_html("Org", "Memphis, TN", fx, "Org", None, {"CIK-001": 3})
    assert "ZZCIK001ZZ" in measured and "ZZCIK001DRAFTZZ" in measured
    assert "ZZ" not in final.replace("ZZ", "ZZ") or "ZZCIK" not in final
    assert "CIK-001&nbsp;</span>" in final                       # badge text keeps a space in the text layer


# 3.7 ─────────────────────────────────────────────────────────────────────────
def test_wikipedia_never_a_first_move_for_a_practice():
    from perception.plain import prioritize_first_moves
    items = ["Check whether you qualify for a Wikipedia article, because it would give assistants a trusted public record.",
             "Create a Wikidata entry, because assistants miss a key reliable public record about you.",
             "Publish full physician bios on your own website."]
    out = prioritize_first_moves(items, "practice")
    assert not any("Wikipedia" in i for i in out) and any("Wikidata" in i for i in out) and len(out) == 2
    assert len(prioritize_first_moves(items, "hospital")) == 3
    assert len(prioritize_first_moves(items, "practice", has_article=True)) == 3


# 3.8 ─────────────────────────────────────────────────────────────────────────
def test_roadmap_wording_says_who_does_what():
    from perception.plain import PLAIN_TERMS, _SYSTEM
    joined = " ".join(b for _, b in PLAIN_TERMS) + _SYSTEM
    assert "leave this to the technical roadmap" not in joined
    assert "hand this to your web team" in joined
