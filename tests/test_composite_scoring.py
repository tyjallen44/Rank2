"""Unit tests for System Composite scoring functions.

Covers every gate, ceiling, floor, and small-network/integration-grace rule.
Run with: python -m pytest tests/test_composite_scoring.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from perception.composite_scoring import (
    footprint_class_and_weights,
    continuum_bonus,
    attributed_network_score,
    network_score,
    check_small_network,
    compute_composite,
    compute_sar,
    compute_leakage_index,
    compute_orphan_volume_share,
    _score_to_grade,
)
from perception.composite_config import COMPOSITE_CONFIG


# ── Footprint classes ─────────────────────────────────────────────────────────

def test_footprint_facility_centric():
    fc, wh, wn = footprint_class_and_weights(0.10)
    assert fc == "FACILITY-CENTRIC"
    assert wh == pytest.approx(0.75)
    assert wn == pytest.approx(0.25)

def test_footprint_balanced():
    fc, wh, wn = footprint_class_and_weights(0.35)
    assert fc == "BALANCED"
    assert wh == pytest.approx(0.60)
    assert wn == pytest.approx(0.40)

def test_footprint_ambulatory_forward():
    fc, wh, wn = footprint_class_and_weights(0.60)
    assert fc == "AMBULATORY-FORWARD"
    assert wh == pytest.approx(0.50)
    assert wn == pytest.approx(0.50)

def test_footprint_boundary_25():
    # exactly 0.25 → not FACILITY-CENTRIC (max is <0.25)
    fc, _, _ = footprint_class_and_weights(0.25)
    assert fc == "BALANCED"

def test_footprint_boundary_50():
    fc, _, _ = footprint_class_and_weights(0.50)
    assert fc == "AMBULATORY-FORWARD"


# ── Continuum bonus ───────────────────────────────────────────────────────────

def test_continuum_high():
    assert continuum_bonus(0.80) == 4.0
    assert continuum_bonus(0.99) == 4.0

def test_continuum_mid():
    assert continuum_bonus(0.60) == 2.0
    assert continuum_bonus(0.79) == 2.0

def test_continuum_low():
    assert continuum_bonus(0.59) == 0.0
    assert continuum_bonus(0.0)  == 0.0


# ── Attribution gate ──────────────────────────────────────────────────────────

def test_ans_full_sar():
    # ANS = NS × (0.5 + 0.5 × 1.0) = NS × 1.0
    assert attributed_network_score(80.0, 1.0) == pytest.approx(80.0)

def test_ans_zero_sar():
    # ANS = NS × 0.5 — 50% floor ensures referral value still counted
    assert attributed_network_score(80.0, 0.0) == pytest.approx(40.0)

def test_ans_mid_sar():
    assert attributed_network_score(80.0, 0.5) == pytest.approx(60.0)


# ── Network score ─────────────────────────────────────────────────────────────

def test_network_score_equal_weights():
    assert network_score([70.0, 80.0], [1.0, 1.0]) == pytest.approx(75.0)

def test_network_score_unequal_weights():
    # weight 3:1 — should be closer to 70
    assert network_score([70.0, 90.0], [3.0, 1.0]) == pytest.approx(75.0)

def test_network_score_empty():
    assert network_score([], []) == 0.0

def test_network_score_zero_weight():
    assert network_score([80.0], [0.0]) == 0.0


# ── Small network ─────────────────────────────────────────────────────────────

def test_small_network_too_few_practices():
    assert check_small_network(2, 20) is True

def test_small_network_too_few_providers():
    assert check_small_network(5, 9) is True

def test_small_network_ok():
    assert check_small_network(3, 10) is False

def test_small_network_exactly_at_threshold():
    # min_practices=3, min_providers=10 → 3 and 10 should pass
    assert check_small_network(3, 10) is False


# ── Full composite formula & ceilings ────────────────────────────────────────

def _run(hospital=70.0, ns=75.0, sar=0.80, coherence=0.85,
         modifiers=0.0, orphan=0.0, network_share=0.30):
    return compute_composite(hospital, ns, sar, coherence, modifiers, orphan, network_share)

def test_composite_no_ceilings():
    r = _run()
    assert 0 <= r["composite_score"] <= 100
    assert not r["score_ceiling_applied"]

def test_attribution_ceiling_low_sar():
    # Construct a case where the blend genuinely exceeds hospital+5 at low SAR:
    # hospital=50, ns=100, SAR=0.39 (just under threshold), AMBULATORY-FORWARD (w=0.5/0.5)
    # ANS = 100 × (0.5 + 0.5×0.39) = 69.5; SC = 50×0.5 + 69.5×0.5 + 4 = 63.75 > 55 → ceiling fires
    r = compute_composite(
        hospital_score=50.0, ns=100.0, sar=0.39, coherence=0.85,
        modifiers_total=0.0, orphan_volume_share=0.0, network_share=0.60,
    )
    assert r["composite_score"] <= 50.0 + COMPOSITE_CONFIG["ceilings"]["attribution_max_lift"] + 0.01
    assert r["score_ceiling_applied"]
    assert "SAR" in (r["score_ceiling_reason"] or "")

def test_orphan_ceiling():
    # >30% orphan volume → cap at 74
    r = _run(hospital=70.0, ns=90.0, sar=0.90, coherence=0.85,
             orphan=0.40, network_share=0.50)
    assert r["composite_score"] <= 74.0 + 0.01
    assert r["score_ceiling_applied"]
    assert "Orphan" in (r["score_ceiling_reason"] or "") or "orphan" in (r["score_ceiling_reason"] or "").lower()

def test_no_masking_ceiling():
    # Artificially inflate: hospital=40, ns=40, sar=1.0, coherence=0.99
    # SC ≈ 40×0.60 + 40×1.0×0.40 + 4 = 24 + 16 + 4 = 44 → under limit=40+6=46 → no ceiling
    r = compute_composite(40.0, 40.0, 1.0, 0.99, 0.0, 0.0, 0.35)
    # should be ~44, no ceiling needed
    assert r["composite_score"] <= 46.0 + 0.01

def test_final_clamp_zero():
    # huge modifiers should not produce negative
    r = _run(hospital=50.0, ns=50.0, sar=0.80, modifiers=999.0)
    assert r["composite_score"] == 0.0

def test_final_clamp_hundred():
    r = _run(hospital=100.0, ns=100.0, sar=1.0, coherence=1.0)
    assert r["composite_score"] <= 100.0

def test_grade_bands():
    assert _score_to_grade(91)  == "A"
    assert _score_to_grade(85)  == "A−"
    assert _score_to_grade(80)  == "B+"
    assert _score_to_grade(75)  == "B"
    assert _score_to_grade(70)  == "B−"
    assert _score_to_grade(65)  == "C+"
    assert _score_to_grade(60)  == "C"
    assert _score_to_grade(55)  == "C−"
    assert _score_to_grade(45)  == "D"
    assert _score_to_grade(30)  == "F"

def test_delta_sign():
    r = _run(hospital=60.0, ns=80.0, sar=1.0, coherence=0.85)
    assert r["merged_entity_delta"] == pytest.approx(r["composite_score"] - 60.0, abs=0.1)


# ── SAR computation ───────────────────────────────────────────────────────────

class _MockEntity:
    def __init__(self, id_, tier, weight):
        from perception.composite_models import OwnershipTier
        self.id = id_
        self.inclusion_tier = OwnershipTier(tier.upper().replace("_", "-"))
        self.inclusion_weight = weight

def test_sar_full_linkage():
    entities = [_MockEntity("a", "OWNED", 1.0), _MockEntity("b", "OWNED", 1.0)]
    linkage  = {"a": 100.0, "b": 100.0}
    sar = compute_sar(entities, linkage, battery_resolution_score=1.0)
    assert sar == pytest.approx(1.0)

def test_sar_zero_linkage():
    entities = [_MockEntity("a", "OWNED", 1.0)]
    linkage  = {"a": 0.0}
    sar = compute_sar(entities, linkage, battery_resolution_score=0.0)
    assert sar == pytest.approx(0.0)

def test_sar_integration_grace_excluded_from_tier2():
    # None linkage entry = IN-TRANSITION in grace period; still weights battery component
    entities = [_MockEntity("a", "IN-TRANSITION", 1.0)]
    linkage  = {"a": None}   # excluded from tier-2 component
    sar = compute_sar(entities, linkage, battery_resolution_score=0.5)
    cfg = COMPOSITE_CONFIG
    expected = 0.0 * cfg["sar_tier2_weight"] + 0.5 * cfg["sar_battery_weight"]
    assert sar == pytest.approx(expected)

def test_sar_affiliated_excluded():
    # AFFILIATED entities not included in SAR weight pool
    entities = [
        _MockEntity("a", "OWNED",    1.0),
        _MockEntity("b", "AFFILIATED", 5.0),  # large weight but excluded
    ]
    linkage = {"a": 80.0, "b": 100.0}
    sar = compute_sar(entities, linkage, battery_resolution_score=0.0)
    cfg = COMPOSITE_CONFIG
    expected = (80.0 / 100.0) * cfg["sar_tier2_weight"] + 0.0
    assert sar == pytest.approx(expected)


# ── Leakage index ─────────────────────────────────────────────────────────────

def test_leakage_index():
    # (1 - 0.60) × 0.80 = 0.32
    assert compute_leakage_index(0.60, 0.80) == pytest.approx(0.32)

def test_leakage_index_full_sar():
    assert compute_leakage_index(1.0, 0.80) == pytest.approx(0.0)

def test_leakage_index_no_capture():
    assert compute_leakage_index(0.0, 0.0) == pytest.approx(0.0)


# ── Orphan volume share ───────────────────────────────────────────────────────

def test_orphan_volume_share():
    entities = [_MockEntity("a", "OWNED", 2.0), _MockEntity("b", "OWNED", 8.0)]
    share = compute_orphan_volume_share(entities, {"a"})
    assert share == pytest.approx(0.20)

def test_orphan_volume_share_none():
    entities = [_MockEntity("a", "OWNED", 1.0)]
    assert compute_orphan_volume_share(entities, set()) == pytest.approx(0.0)

def test_orphan_volume_share_affiliated_excluded():
    entities = [
        _MockEntity("a", "OWNED",      1.0),
        _MockEntity("b", "AFFILIATED", 9.0),
    ]
    share = compute_orphan_volume_share(entities, {"a"})
    assert share == pytest.approx(1.0)  # a/(a only, b excluded) = 1.0
