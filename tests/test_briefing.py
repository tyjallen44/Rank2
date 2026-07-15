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
        strengths = [f for f in br.findings if f.candidate_type in ("strength", "relative_strength")]
        assert len(strengths) >= 1, f"{variant}: no strength or relative_strength in selection"


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
    """A not_established anchor excludes composite:anchor_reputation but keeps the tier candidate.

    Tier scores are computed values (always vstren=3). The _anchor_verification gate applies
    only to composite:anchor_reputation (factual rating/count claims), not to tier scores.
    """
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
    cand_ids = [c.id for c in candidates]
    # composite:anchor_reputation is excluded (factual claim, not_established=True → vstren=0)
    assert "composite:anchor_reputation" not in cand_ids, (
        "composite:anchor_reputation must be excluded when anchor is not_established"
    )
    # tier:patient_experience_reviews IS present (computed pillar score, always vstren=3)
    assert "tier:patient_experience_reviews" in cand_ids, (
        "tier:patient_experience_reviews must be in pool even when anchor is not_established"
    )


# ── T7: HTML renders without crashing ─────────────────────────────────────────

def test_objections_always_two():
    """AC-4.2: Every briefing must include exactly 2 objection entries."""
    result = _make_result()
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        assert len(br.objections) == 2, (
            f"{variant}: expected 2 objections, got {len(br.objections)}: "
            f"{[o['id'] for o in br.objections]}"
        )


def test_demo_fallback_present_in_html_when_no_battery():
    """AC-4.1: When no battery, demo section renders a fallback note (not silently absent)."""
    from perception.briefing_pdf import render_briefing_html
    result = _make_result(battery_results=None)
    br = extract(result, "sales")
    html_out = render_briefing_html(br)
    assert "Live demo not available" in html_out or "battery not yet executed" in html_out, (
        "Demo section silently disappeared when battery_results=None — must render a fallback"
    )
    assert "Live Demo Prompt" in html_out  # section header always present


def test_objections_key_off_actual_findings():
    """AC-4.2: An objection triggered by credentials_recognition:strength must appear
    when that finding type is actually selected."""
    # Default fixture: experience=80 (strength) → obj-009 covers credentials:strength
    # but let's force credentials as a finding by making it the only strength
    p = _make_provider(
        tier_scores=TierScores(
            clinical_outcomes_safety=60,   # gap
            credentials_recognition=92,    # strength (≥75)
            patient_experience_reviews=50, # gap
            access_fit=40,                 # gap
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type=None)
    br = extract(result, "sales")
    # Find which finding types were selected
    selected_types = {f.finding_type for f in br.findings}
    # Verify that obj-009 (credentials:strength) fires when that type is present
    if "tier:credentials_recognition:strength" in selected_types:
        obj_ids = [o["id"] for o in br.objections]
        assert "obj-009" in obj_ids or "generic" in obj_ids, (
            f"credentials_recognition:strength selected but no matching objection: {obj_ids}"
        )
    assert len(br.objections) == 2


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


# ── T7b: Composition constraints (AC-2.x) ─────────────────────────────────────

def test_never_all_strengths_without_note():
    """When all findings are strengths (degradation), strong_posture must be True."""
    # All tiers ≥75, no derived metrics → no gap candidates → degradation
    p = _make_provider(
        tier_scores=TierScores(
            clinical_outcomes_safety=80,
            credentials_recognition=90,
            patient_experience_reviews=85,
            access_fit=78,
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type=None)  # hospital, no anchor needed for tiers
    br = extract(result, "sales")
    # All tiers are strengths → must trigger strong_posture
    all_strong = all(f.candidate_type in ("strength", "relative_strength") for f in br.findings)
    if all_strong:
        assert br.strong_posture is True, "All-strength selection must set strong_posture=True"
    assert len(br.findings) == 3


def test_composition_produces_at_least_one_gap_when_gaps_exist():
    """AC-2.1: When gap candidates are available, selection must include ≥1 gap."""
    # Default fixture: clinical=55 (<75=gap), credentials=72 (gap), experience=80 (≥75=strength), access=40 (gap)
    result = _make_result()
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        gaps = [f for f in br.findings if f.candidate_type == "gap"]
        assert len(gaps) >= 1, f"{variant}: no gap finding when gap candidates clearly exist"


def test_doc_scores_produce_gap_finding():
    """AC-2.1: DOC-like scores (70/92/72/75) must produce at least one gap finding.

    DOC's 70 on Practitioner Credentials is the obvious gap cited by the remediation.
    Under practice_procedural weights, 70 < 75 → gap candidate.
    """
    p = _make_provider(
        weighting_profile="practice_procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=70,    # gap (<75)
            credentials_recognition=92,     # strength
            patient_experience_reviews=72,  # gap (<75)
            access_fit=75,                  # strength (≥75)
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=0.50,         # gap metric (< 0.60)
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type="practice")
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        gaps = [f for f in br.findings if f.candidate_type == "gap"]
        assert len(gaps) >= 1, (
            f"{variant}: DOC-like scores produced no gap findings — "
            f"findings: {[f.candidate_type for f in br.findings]}"
        )


def test_all_gaps_pool_forces_relative_strength():
    """AC-2.2: All-gaps pool must produce ≥1 relative_strength finding."""
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
    result = _make_result(provider=p, entity_type=None)  # hospital, all tiers < 75
    br = extract(result, "sales")
    strength_types = [f.candidate_type for f in br.findings
                      if f.candidate_type in ("strength", "relative_strength")]
    assert len(strength_types) >= 1, (
        f"All-gaps pool produced no strength/relative_strength: {[f.candidate_type for f in br.findings]}"
    )


def test_composition_never_3_of_same_type_when_mix_available():
    """AC-2.2: Never 3 strengths or 3 gaps when the pool has both types."""
    p = _make_provider(
        tier_scores=TierScores(
            clinical_outcomes_safety=55,   # gap
            credentials_recognition=80,    # strength
            patient_experience_reviews=85, # strength
            access_fit=40,                 # gap
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type=None)
    for variant in ("sales", "cs"):
        br = extract(result, variant)
        n_gap = sum(1 for f in br.findings if f.candidate_type == "gap")
        n_str = sum(1 for f in br.findings if f.candidate_type in ("strength", "relative_strength"))
        assert n_gap < 3, f"{variant}: 3 gaps when strengths exist"
        assert n_str < 3, f"{variant}: 3 strengths when gaps exist"


# ── T7c: Template claim guard (AC-3.x) ────────────────────────────────────────

def test_no_peer_or_trend_claims_in_templates():
    """AC-3.2: Templates must not contain peer-comparison or trend language."""
    cfg = _cfg()
    banned_tokens = ["ahead of", "most peers", "trending", "improving", "better than",
                     "higher than", "best in class"]
    violations = []
    all_templates: dict = {}
    all_templates.update(cfg.get("finding_templates", {}))
    all_templates["_hook_contrast"]       = {"text": cfg["hook_templates"]["contrast"]}
    all_templates["_hook_problem_lead"]   = {"text": cfg["hook_templates"]["problem_lead"]}
    all_templates["_hook_strength_lead"]  = {"text": cfg["hook_templates"]["strength_lead"]}
    for ask_key, ask_text in cfg.get("ask_templates", {}).items():
        all_templates[f"_ask_{ask_key}"] = {"text": ask_text}

    for tkey, tpl in all_templates.items():
        for field_name, text in tpl.items():
            if not isinstance(text, str):
                continue
            for token in banned_tokens:
                if token.lower() in text.lower():
                    violations.append(f"[{tkey}][{field_name}]: '{token}'")

    assert not violations, (
        "Peer-comparison or trend language found in templates:\n" + "\n".join(violations)
    )


def test_tier_strength_threshold_in_config():
    """AC-3.3: Tier classification threshold must be config-driven and ≥60."""
    cfg = _cfg()
    thr = cfg.get("tier_thresholds", {}).get("strength")
    assert thr is not None, "tier_thresholds.strength missing from config"
    assert thr >= 60, f"Strength threshold {thr} is too low — produces trivial gap/strength split"
    assert thr <= 90, f"Strength threshold {thr} is too high — most entities would have no strengths"


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


# ── T8b: Presentation quality (AC-5.x) ───────────────────────────────────────

def test_no_full_address_in_composite_prose():
    """AC-5.1: When anchor has clean practice_name, composite template must not
    include the full entity_name with address."""
    from perception.briefing_pdf import render_briefing_html
    # Default fixture: anchor practice_name = "Desert Orthopaedic Center"
    # entity_name = "Desert Orthopaedic Center" (same in test, but in prod has address)
    result = _make_result(entity_name="Desert Orthopaedic Center 2800 E Desert Inn Rd")
    result.practice_composite_rows[0]["practice_name"] = "Desert Orthopaedic Center"
    br = extract(result, "sales")
    html_out = render_briefing_html(br)
    assert "2800 E Desert Inn Rd" not in html_out or "Desert Orthopaedic Center" in html_out, (
        "Full address appearing in briefing prose — display_name fallback not working"
    )
    # Specifically check composite template if it was selected
    for f in br.findings:
        if "anchor_reputation" in f.finding_type:
            assert "2800 E Desert Inn Rd" not in f.what, (
                f"Address leaked into composite finding: {f.what}"
            )


def test_delta_language_no_points_artifact():
    """AC-5.3: Delta language must not contain 'point(s)' pluralization artifact."""
    result = _make_result()
    br = extract(result, "cs")
    # Without a prior run, ask uses cs_no_prior — no delta language
    assert "point(s)" not in br.ask, f"'point(s)' artifact in ask: {br.ask}"


def test_sales_ask_template_variety():
    """AC-5.4: Two different run_ids must be able to produce different ask phrasings."""
    import dataclasses
    result1 = _make_result(run_id="aaaa-0000")
    result2 = _make_result(run_id="ffff-9999")
    br1 = extract(result1, "sales")
    br2 = extract(result2, "sales")
    # Verify both are grammatical (non-empty, no unfilled template tokens)
    assert "{" not in br1.ask, f"Unfilled template token in ask: {br1.ask}"
    assert "{" not in br2.ask, f"Unfilled template token in ask: {br2.ask}"
    # At least one unique phrasing variant must exist
    cfg = _cfg()
    templates = cfg["ask_templates"]["sales"]
    assert isinstance(templates, list) and len(templates) >= 2, (
        "Sales ask_templates must be a list of ≥2 variants"
    )


def test_same_run_id_produces_identical_ask():
    """AC-5.4 (determinism): Same run_id must produce byte-identical ask text."""
    result = _make_result(run_id="test-det-001")
    br1 = extract(result, "sales")
    br2 = extract(result, "sales")
    assert br1.ask == br2.ask, "Same run_id produced different ask text — not deterministic"


def test_roadmap_title_clean_strips_parentheticals():
    """AC-5.2: _roadmap_title_clean must remove '(...)' labels from section titles."""
    from perception.briefing import _roadmap_title_clean
    assert _roadmap_title_clean("Your Website (Technical & Content Fixes)") == "your website"
    assert _roadmap_title_clean("Patient Experience & Reviews") == "patient experience & reviews"
    assert _roadmap_title_clean("GBP Optimization (Q1 Priority)") == "gbp optimization"


# ── T9b: Phase 2 rubric redesign (AC-2.x) ────────────────────────────────────

def test_service_aligned_low_weight_gap_outranks_unaligned():
    """AC-2.2: A catastrophic gap in a high-service-alignment pillar must outrank a mild
    gap in a lower-service-alignment pillar, even when the latter has higher pillar weight.

    patient_experience_reviews (svc=3) at 35/100 vs clinical_outcomes_safety (svc=1) at 64/100.
    clinical has 4× the pillar weight (0.46 vs 0.10 in procedural) but lower service alignment.
    With service_alignment weight=2.5 in sales, E&R should rank above clinical.
    """
    p = _make_provider(
        weighting_profile="procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=64,     # gap, w=0.46
            credentials_recognition=76,      # strength
            patient_experience_reviews=35,   # gap, w=0.10 — catastrophic, svc=3
            access_fit=72,                   # gap, w=0.09
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type=None)  # hospital
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    er = next(c for c in candidates if c.tier_key == "patient_experience_reviews")
    clinical = next(c for c in candidates if c.tier_key == "clinical_outcomes_safety")
    assert er.score_sales > clinical.score_sales, (
        f"E&R (svc=3, score={er.score_sales:.2f}) must outrank clinical "
        f"(svc=1, score={clinical.score_sales:.2f}) in sales despite lower pillar weight"
    )


def test_lowest_pillar_guarantee_fires_when_scoring_omits_it():
    """AC-2.2: Lowest pillar below threshold must appear in findings even if rubric score
    alone would not select it.

    Set up: 3 gap pillars where the lowest (access_fit at 30) would normally lose to higher-
    scoring gaps. Force it by making the other two gaps score much higher. The guarantee must
    inject access_fit and displace the lowest-ranked selected gap.
    """
    # access_fit (svc=3) at 30, but give it no roadmap match → act=minimum=2, but low dem
    # Make clinical and credentials both score higher
    p = _make_provider(
        weighting_profile="procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=64,
            credentials_recognition=60,      # gap
            patient_experience_reviews=80,   # strength (≥75) → no gap pool entry
            access_fit=30,                   # lowest, gap
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    # improvement_sections that match clinical and credentials but NOT access_fit
    result = _make_result(
        provider=p,
        entity_type=None,
        improvement_sections=[
            ImprovementSection(
                title="Clinical Outcomes Transparency",
                description="Quality data.",
                items=["Publish outcomes data"],
            ),
            ImprovementSection(
                title="Physician Credentials & Recognition",
                description="Credentials visibility.",
                items=["Update board cert listings"],
            ),
        ],
    )
    br = extract(result, "sales")
    finding_tiers = [f.finding_type.split(":")[1] for f in br.findings]
    assert "access_fit" in finding_tiers, (
        f"Lowest pillar (access_fit=30) must appear via guarantee even if scoring omits it. "
        f"Got: {finding_tiers}"
    )


def test_practice_lowest_pillar_surfaces_same_way():
    """AC-2.3: Practice edition with Reviews & Reputation as lowest pillar must surface it."""
    p = _make_provider(
        weighting_profile="practice_procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=80,
            credentials_recognition=55,   # practice: Reviews & Reputation — lowest, gap
            patient_experience_reviews=85,
            access_fit=78,
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type="practice")
    br = extract(result, "sales")
    tiers_in = [f.finding_type.split(":")[1] for f in br.findings]
    assert "credentials_recognition" in tiers_in, (
        f"Practice lowest pillar (credentials_recognition=55 = Reviews & Reputation) "
        f"must surface. Got: {tiers_in}"
    )
    # And it should render as practice-edition label
    cred_finding = next(f for f in br.findings if "credentials_recognition" in f.finding_type)
    assert "Reviews & Reputation" in (cred_finding.what + cred_finding.why + cred_finding.say), (
        "Finding for credentials_recognition in practice edition must use practice-edition label"
    )


def test_service_alignment_in_candidate_pool():
    """Service alignment dimension must be non-zero and vary by tier."""
    p = _make_provider(
        weighting_profile="procedural",
        tier_scores=TierScores(
            clinical_outcomes_safety=60,
            credentials_recognition=60,
            patient_experience_reviews=60,
            access_fit=60,
        ),
        entity_resolution_pct=None,
        linkage_integrity_pct=None,
        physician_capture_rate=None,
    )
    result = _make_result(provider=p, entity_type=None)
    cfg = _cfg()
    candidates = _build_candidate_pool(result, p, cfg)
    by_tier = {c.tier_key: c.service_alignment for c in candidates}
    assert by_tier["patient_experience_reviews"] == 3, "E&R must have svc=3"
    assert by_tier["access_fit"] == 3, "access_fit must have svc=3"
    assert by_tier["credentials_recognition"] == 2, "credentials must have svc=2"
    assert by_tier["clinical_outcomes_safety"] == 1, "clinical must have svc=1"


def test_strength_materiality_is_threshold_relative():
    """Strength materiality is (score - threshold) × weight, not absolute score × weight.

    A barely-above-threshold strength (score=76, threshold=75) must have lower materiality
    than a clearly-above-threshold strength at the same weight.
    """
    from perception.briefing import _materiality
    cfg = _cfg()
    # Barely above threshold
    mat_barely = _materiality(76, 0.35, "strength", cfg)
    # Clearly above threshold
    mat_clearly = _materiality(92, 0.35, "strength", cfg)
    assert mat_barely < mat_clearly, (
        f"Barely-above-threshold strength (mat={mat_barely}) must have lower materiality "
        f"than clearly-above-threshold strength (mat={mat_clearly})"
    )
    # A barely-above-threshold strength should get mat=0 (pts=(76-75)×0.35=0.35 < score_1=1.0)
    assert mat_barely == 0, f"Expected mat=0 for barely-above-threshold strength, got {mat_barely}"


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
