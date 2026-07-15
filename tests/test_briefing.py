"""Tests for the Pulse Briefing extraction engine and PDF renderer.

No server, Claude API, or network access required.
Run with: python -m pytest tests/test_briefing.py -v
"""
import sys
import os
import copy
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perception.models import (
    AnalysisResult,
    RankedProvider,
    TierScores,
    ImprovementSection,
    BatteryPromptResult,
)
from perception.briefing import (
    BriefingResult,
    BriefingValidationError,
    extract,
    validate_briefing_inputs,
    _build_candidate_pool,
    _select_findings,
    _cfg,
)


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _make_provider(**kwargs) -> RankedProvider:
    defaults = dict(
        rank=1,
        name="Desert Orthopaedic Center",
        ai_visibility_score=62,
        weighting_profile="procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=55,
            credentials_recognition=72,
            patient_experience_reviews=80,
            access_fit=40,
        ),
        entity_resolution_pct=0.65,
        linkage_integrity_pct=0.50,
        physician_capture_rate=0.55,
        score_ceiling_applied=False,
        score_ceiling_reason=None,
    )
    defaults.update(kwargs)
    return RankedProvider(**defaults)


def _make_anchor_row(**kwargs) -> dict:
    base = {
        "practice_name": "Desert Orthopaedic Center",
        "is_anchor": True,
        "not_established": False,
        "affiliation_verified": True,
        "avg_rating": 4.5,
        "total_reviews": 1306,
        "platforms_found": 1,
        "platforms_list": "Google",
        "platform_entries": [("google", 1306, None)],
        "collection_date": "2026-07-14",
        "physicians": [],
    }
    base.update(kwargs)
    return base


def _make_result(**kwargs) -> AnalysisResult:
    p = kwargs.pop("provider", _make_provider())
    anchor = kwargs.pop("anchor", _make_anchor_row())
    defaults = dict(
        run_id="test-run-001",
        location="Las Vegas, NV",
        specialty="Orthopedics",
        generated_at=date(2026, 7, 15),
        entity_name="Desert Orthopaedic Center",
        entity_type="practice",
        weighting_profile="procedural",
        rankings=[p],
        practice_composite_rows=[anchor],
        physician_composite_rows=[],
        improvement_sections=[
            ImprovementSection(
                title="Patient Experience & Reviews",
                description="Improve review coverage.",
                items=["Claim all Google Business Profiles", "Solicit reviews post-visit"],
            ),
            ImprovementSection(
                title="Linkage Integrity & Directory Cleanup",
                description="Fix NAP inconsistencies.",
                items=["Audit directory listings", "Correct phone and address data"],
            ),
        ],
        battery_results=None,
    )
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


# ── T1: Determinism ────────────────────────────────────────────────────────────

def test_determinism_sales():
    result = _make_result()
    br1 = extract(result, "sales")
    br2 = extract(result, "sales")
    import dataclasses
    assert dataclasses.asdict(br1) == dataclasses.asdict(br2), (
        "Sales briefing is not byte-identical on back-to-back extraction"
    )


def test_determinism_cs():
    result = _make_result()
    br1 = extract(result, "cs")
    br2 = extract(result, "cs")
    import dataclasses
    assert dataclasses.asdict(br1) == dataclasses.asdict(br2), (
        "CS briefing is not byte-identical on back-to-back extraction"
    )


# ── T2: Validation gate ────────────────────────────────────────────────────────

def test_validation_gate_missing_entity_name():
    result = _make_result(entity_name=None)
    errors = validate_briefing_inputs(result)
    assert any("entity_name" in e for e in errors)


def test_validation_gate_missing_score():
    p = _make_provider(ai_visibility_score=None)
    result = _make_result(provider=p)
    errors = validate_briefing_inputs(result)
    assert any("ai_visibility_score" in e for e in errors)


def test_validation_gate_missing_anchor():
    result = _make_result(practice_composite_rows=[])
    errors = validate_briefing_inputs(result)
    assert any("practice_composite_rows" in e for e in errors)


def test_validation_gate_missing_improvement_sections():
    result = _make_result(improvement_sections=[])
    errors = validate_briefing_inputs(result)
    assert any("improvement_section" in e for e in errors)


def test_validation_gate_valid_result_no_errors():
    result = _make_result()
    errors = validate_briefing_inputs(result)
    assert errors == [], f"Unexpected validation errors: {errors}"


def test_validation_raises_on_extract_with_bad_input():
    result = _make_result(entity_name=None)
    try:
        extract(result, "sales")
        assert False, "Expected BriefingValidationError"
    except BriefingValidationError as exc:
        assert exc.missing


def test_hospital_all_neutral_tier_scores_still_produces_briefing():
    """All 4 tiers in the formerly-excluded 45-69 neutral zone must still yield 3 findings."""
    p = _make_provider(
        tier_scores=TierScores(
            clinical_outcomes_safety=62,
            credentials_recognition=58,
            patient_experience_reviews=67,
            access_fit=55,
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type=None)  # hospital run
    br = extract(result, "sales")
    assert len(br.findings) == 3


# ── T3: Held physician exclusion ──────────────────────────────────────────────

def test_held_physician_excludes_physician_capture_rate():
    """If a physician is on hold, metric:physician_capture_rate must not appear."""
    p = _make_provider(physician_capture_rate=0.30)  # gap territory
    result = _make_result(
        provider=p,
        physician_composite_rows=[{
            "physician_name": "MATTHEW FOUSE",
            "practice_name": "Desert Orthopaedic Center",
            "not_established": False,
            "avg_rating": 3.9,
            "total_reviews": 45,
            "platforms_found": 1,
            "platforms_list": "Google",
            "platform_entries": [("google", 45, None)],
            "collection_date": "2026-07-14",
        }],
    )
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    assert not any(c.id == "metric:physician_capture_rate" for c in candidates), (
        "physician_capture_rate candidate must be excluded when a held physician is present"
    )


def test_held_physician_absent_from_briefing_output():
    """A held physician's name must never appear in any briefing element."""
    p = _make_provider(physician_capture_rate=0.30)
    result = _make_result(
        provider=p,
        physician_composite_rows=[{
            "physician_name": "MATTHEW FOUSE",
            "practice_name": "Desert Orthopaedic Center",
            "not_established": False,
            "avg_rating": 3.9,
            "total_reviews": 45,
            "platforms_found": 1,
            "platforms_list": "Google",
            "platform_entries": [("google", 45, None)],
            "collection_date": "2026-07-14",
        }],
    )
    br = extract(result, "sales")
    import dataclasses, json
    br_json = json.dumps(dataclasses.asdict(br))
    assert "MATTHEW FOUSE" not in br_json
    assert "MERVYN FOUSE" not in br_json


# ── T4: Report-type / edition assertion ───────────────────────────────────────

def test_practice_entity_type_accepted():
    result = _make_result(entity_type="practice")
    br = extract(result, "sales")
    assert br.entity_name == "Desert Orthopaedic Center"


def test_hospital_entity_type_accepted():
    """Hospital entity_type with hospital composite rows should pass validation."""
    anchor = _make_anchor_row()
    result = _make_result(entity_type="hospital", anchor=anchor)
    errors = validate_briefing_inputs(result)
    assert errors == [], f"Hospital edition validation errors: {errors}"


def test_invalid_variant_raises():
    result = _make_result()
    try:
        extract(result, "enterprise")
        assert False, "Expected ValueError for invalid variant"
    except ValueError:
        pass


# ── T4a: Edition label/weight binding (AC-1.2) ────────────────────────────────

def test_practice_edition_uses_practice_labels():
    """Practice run: credentials_recognition must render as 'Reviews & Reputation'."""
    p = _make_provider(
        weighting_profile="practice_procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=60,
            credentials_recognition=92,
            patient_experience_reviews=70,
            access_fit=65,
        ),
    )
    result = _make_result(provider=p, entity_type="practice")
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    cred_cand = next((c for c in candidates if c.tier_key == "credentials_recognition"), None)
    assert cred_cand is not None
    assert cred_cand.context["tier_label"] == "Reviews & Reputation", (
        f"Practice run got hospital label '{cred_cand.context['tier_label']}' "
        "instead of 'Reviews & Reputation'"
    )


def test_hospital_edition_uses_hospital_labels():
    """Hospital run: credentials_recognition must render as 'Credentials & Recognition'."""
    p = _make_provider(
        weighting_profile="procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=60,
            credentials_recognition=92,
            patient_experience_reviews=70,
            access_fit=65,
        ),
    )
    result = _make_result(provider=p, entity_type=None)
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    cred_cand = next((c for c in candidates if c.tier_key == "credentials_recognition"), None)
    assert cred_cand is not None
    assert cred_cand.context["tier_label"] == "Credentials & Recognition", (
        f"Hospital run got wrong label '{cred_cand.context['tier_label']}'"
    )


def test_positional_binding_canary():
    """Same score on credentials_recognition must produce different labels in different editions."""
    scores = TierScores(
        clinical_outcomes_safety=60,
        credentials_recognition=92,
        patient_experience_reviews=70,
        access_fit=65,
    )
    p_practice = _make_provider(weighting_profile="practice_procedural", tier_scores=scores)
    p_hospital = _make_provider(weighting_profile="procedural", tier_scores=scores)
    r_practice = _make_result(provider=p_practice, entity_type="practice")
    r_hospital = _make_result(provider=p_hospital, entity_type=None)
    cfg = _cfg()
    pool_practice = _build_candidate_pool(r_practice, p_practice, cfg)
    pool_hospital = _build_candidate_pool(r_hospital, p_hospital, cfg)
    label_p = next(c for c in pool_practice if c.tier_key == "credentials_recognition").context["tier_label"]
    label_h = next(c for c in pool_hospital if c.tier_key == "credentials_recognition").context["tier_label"]
    assert label_p != label_h, (
        f"Positional binding: both editions returned same label '{label_p}' "
        "for credentials_recognition — edition lookup is not keyed correctly"
    )


def test_top_tier_label_is_computed_from_weight_table():
    """top_tier_label in candidate context must reflect actual highest-weight pillar."""
    # practice_relationship: credentials_recognition has weight 0.35 (highest)
    p = _make_provider(
        weighting_profile="practice_relationship",
        tier_scores=TierScores(
            clinical_outcomes_safety=80,
            credentials_recognition=75,
            patient_experience_reviews=70,
            access_fit=65,
        ),
    )
    result = _make_result(provider=p, entity_type="practice")
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    top_labels = {c.context["top_tier_label"] for c in candidates if c.tier_key in
                  ("clinical_outcomes_safety", "credentials_recognition")}
    assert top_labels == {"Reviews & Reputation"}, (
        f"Expected top_tier_label='Reviews & Reputation' for practice_relationship; got {top_labels}"
    )


def test_no_hardcoded_pillar_names_in_templates():
    """AC-1.3: No finding template may contain a hardcoded pillar label string."""
    cfg = _cfg()
    hardcoded = [
        "Outcomes & Safety",
        "Credentials & Recognition",
        "Experience & Reviews",
        "Practitioner Credentials & Clinical Quality",
        "Reviews & Reputation",
        "Identity & Machine-Readability",
    ]
    violations = []
    for tkey, tpl in cfg["finding_templates"].items():
        for field in ("what", "why", "say"):
            text = tpl.get(field, "")
            for name in hardcoded:
                if name in text:
                    violations.append(f"finding_templates[{tkey}][{field}]: '{name}'")
    for tkey, tpl in cfg["hook_templates"].items():
        for name in hardcoded:
            if name in tpl:
                violations.append(f"hook_templates[{tkey}]: '{name}'")
    assert not violations, "Hardcoded pillar names found in templates:\n" + "\n".join(violations)


# ── T5: Selection constraints ──────────────────────────────────────────────────

def test_exactly_3_findings():
    result = _make_result()
    br = extract(result, "sales")
    assert len(br.findings) == 3, f"Expected 3 findings, got {len(br.findings)}"


def test_at_least_one_strength():
    result = _make_result()
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        strengths = [f for f in br.findings if f.candidate_type == "strength"]
        assert len(strengths) >= 1, f"{variant}: no strength finding in selection"


def test_no_two_from_same_tier():
    result = _make_result()
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        cfg = _cfg()
        p = result.rankings[0]
        pool = _build_candidate_pool(result, p, cfg)
        # Map finding id → tier_key
        id_to_tier = {c.id: c.tier_key for c in pool}
        used_tiers: list[str] = []
        for f in br.findings:
            tk = id_to_tier.get(f.candidate_id, f.candidate_id)
            assert tk not in used_tiers, (
                f"{variant}: duplicate tier '{tk}' in findings"
            )
            used_tiers.append(tk)


def test_demo_absent_when_no_battery():
    result = _make_result(battery_results=None)
    br = extract(result, "sales")
    assert br.demo is None


def test_demo_present_when_battery_eligible():
    bat = {
        "prompt-001": BatteryPromptResult(
            prompt_id="prompt-001",
            prompt_text="What is the best orthopedic practice in Las Vegas?",
            run_count=6,
            outcomes=["recommended"] * 5 + ["mentioned"],
            dominant_outcome="recommended",
            dominant_pct=0.833,
        )
    }
    result = _make_result(battery_results=bat)
    br = extract(result, "sales")
    assert br.demo is not None
    assert br.demo.dominant_outcome == "recommended"


def test_demo_absent_when_below_threshold():
    bat = {
        "prompt-001": BatteryPromptResult(
            prompt_id="prompt-001",
            prompt_text="What is the best orthopedic practice in Las Vegas?",
            run_count=6,
            outcomes=["recommended"] * 4 + ["absent"] * 2,
            dominant_outcome="recommended",
            dominant_pct=0.667,   # below 0.80 threshold
        )
    }
    result = _make_result(battery_results=bat)
    br = extract(result, "sales")
    assert br.demo is None


# ── T6: Score ceiling exclusion from verification_strength=0 ─────────────────

def test_not_established_anchor_excluded():
    """An anchor marked not_established has vstren=0 and no patient_experience tier candidate."""
    anchor = _make_anchor_row(not_established=True)
    p = _make_provider(
        tier_scores=TierScores(
            clinical_outcomes_safety=55,
            credentials_recognition=72,
            patient_experience_reviews=80,
            access_fit=40,
        )
    )
    result = _make_result(provider=p, anchor=anchor)
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    per_ids = [c.id for c in candidates]
    # patient_experience tier candidate requires vstren>0; should be absent
    assert "tier:patient_experience_reviews" not in per_ids


# ── T7: HTML renders without crashing ─────────────────────────────────────────

def test_render_briefing_html_smoke():
    from perception.briefing_pdf import render_briefing_html
    result = _make_result()
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        html_out = render_briefing_html(br)
        assert "Desert Orthopaedic Center" in html_out
        assert "Internal use only" in html_out
        assert len(html_out) > 500


def test_render_briefing_html_escapes_entity_name():
    from perception.briefing_pdf import render_briefing_html
    result = _make_result(entity_name="Foo & Bar <Practice>")
    # Allow validation to pass for modified name
    p = result.rankings[0]
    result.practice_composite_rows[0]["practice_name"] = "Foo & Bar <Practice>"
    br_result = extract(result, "sales")
    html_out = render_briefing_html(br_result)
    assert "&amp;" in html_out or "Foo" in html_out  # XSS check
    assert "<Practice>" not in html_out


# ── T8: Variant produces different ordering ────────────────────────────────────

def test_sales_vs_cs_may_differ():
    """Sales and CS briefings may select different findings due to different weights."""
    result = _make_result()
    br_sales = extract(result, "sales")
    br_cs = extract(result, "cs")
    # Both are valid — just confirm they run without error and return 3 findings each
    assert len(br_sales.findings) == 3
    assert len(br_cs.findings) == 3
    # Variant is recorded correctly
    assert br_sales.variant == "sales"
    assert br_cs.variant == "cs"


# ── T9: Full round-6 regression guard ─────────────────────────────────────────

def test_holds_module_still_works():
    from perception.holds import is_held
    assert is_held("MATTHEW FOUSE", "Desert Orthopaedic Center")
    assert is_held("MERVYN FOUSE", "Desert Orthopaedic Center")
    assert not is_held("JOHN SMITH", "Desert Orthopaedic Center")


def test_scoring_grade_still_works():
    from perception.scoring import grade_from_score
    grade, band = grade_from_score(62)
    assert grade in ("C", "C+", "C-", "B-")
    grade_a, _ = grade_from_score(95)
    assert grade_a.startswith("A")
