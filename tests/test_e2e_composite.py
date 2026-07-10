"""End-to-end composite scoring integration tests.

Tests two fixtures:
  1. BALANCED 5-practice network → full composite score issued
  2. Small-network (2 practices / 8 providers) → refusal with narrative only

These tests run purely in-process (no server, no Claude API calls).
They exercise: scoring, SAR, orphan detection, ceiling logic, and the
small-network refusal gate together.
Run with: python -m pytest tests/test_e2e_composite.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from perception.composite_scoring import (
    compute_composite,
    compute_sar,
    compute_leakage_index,
    compute_orphan_volume_share,
    check_small_network,
    network_score,
    footprint_class_and_weights,
)
from perception.composite_config import COMPOSITE_CONFIG


# ── Shared mock helpers ───────────────────────────────────────────────────────

class _E:
    """Minimal NetworkEntity-like object for scoring tests."""
    def __init__(self, id_, tier, weight):
        from perception.composite_models import OwnershipTier
        self.id = id_
        # Accept e.g. "owned" → "OWNED" or already-uppercase "OWNED"
        self.inclusion_tier = OwnershipTier(tier.upper().replace("_", "-"))
        self.inclusion_weight = weight
        self.entity_type = "practice"
        self.linked_run_id = None


# ── Fixture 1: BALANCED 5-practice network ────────────────────────────────────

class TestBalancedFivePractice:
    """
    Setup:
      - Anchor hospital score: 72
      - 5 owned practices, equal weight 1.0 each
      - Practice scores:  [68, 75, 80, 65, 70]  → NS = 71.6
      - Network encounter share: 0.35 → BALANCED (W_h=0.60, W_n=0.40)
      - SAR = 0.75 (from linkage data)
      - Continuum coherence: 0.82 → Bonus = 4
      - No modifiers, no orphans
    Expected:
      - ANS = 71.6 × (0.5 + 0.5×0.75) = 71.6 × 0.875 = 62.65
      - SC = 72×0.60 + 62.65×0.40 + 4 = 43.2 + 25.06 + 4 = 72.26
      - No ceilings triggered (SAR≥0.40, orphan=0, SC - max(72,71.6)=0.26 < 6)
      - Grade: B−
    """

    HOSPITAL_SCORE  = 72.0
    PRACTICE_SCORES = [68.0, 75.0, 80.0, 65.0, 70.0]
    WEIGHTS         = [1.0] * 5
    NETWORK_SHARE   = 0.35
    COHERENCE       = 0.82
    SAR             = 0.75

    @pytest.fixture(autouse=True)
    def setup(self):
        self.entities = [_E(f"p{i}", "owned", 1.0) for i in range(5)]
        self.linkage  = {f"p{i}": self.SAR * 100 for i in range(5)}  # 75.0 each
        self.ns       = network_score(self.PRACTICE_SCORES, self.WEIGHTS)
        self.sar      = compute_sar(self.entities, self.linkage, battery_resolution_score=0.0)
        self.result   = compute_composite(
            self.HOSPITAL_SCORE, self.ns, self.sar, self.COHERENCE,
            modifiers_total=0.0, orphan_volume_share=0.0, network_share=self.NETWORK_SHARE,
        )

    def test_small_network_not_refused(self):
        assert not check_small_network(5, 30)

    def test_footprint_class_balanced(self):
        fc, _, _ = footprint_class_and_weights(self.NETWORK_SHARE)
        assert fc == "BALANCED"

    def test_network_score(self):
        assert self.ns == pytest.approx(71.6, abs=0.05)

    def test_sar_from_linkage(self):
        cfg = COMPOSITE_CONFIG
        expected = 0.75 * cfg["sar_tier2_weight"] + 0.0 * cfg["sar_battery_weight"]
        assert self.sar == pytest.approx(expected)

    def test_no_ceiling_applied(self):
        assert not self.result["score_ceiling_applied"]

    def test_composite_score_reasonable(self):
        sc = self.result["composite_score"]
        # Should be near 72 — hospital score with modest network uplift
        assert 68.0 <= sc <= 80.0

    def test_grade_issued(self):
        assert self.result["composite_grade"] in ("A", "A−", "B+", "B", "B−", "C+", "C", "C−", "D", "F")

    def test_leakage_index(self):
        capture_rate = 0.65
        li = compute_leakage_index(self.sar, capture_rate)
        assert 0.0 <= li <= 1.0

    def test_no_orphans(self):
        share = compute_orphan_volume_share(self.entities, set())
        assert share == pytest.approx(0.0)


# ── Fixture 2: Small-network refusal ─────────────────────────────────────────

class TestSmallNetworkRefusal:
    """
    Setup:
      - 2 practices, 8 providers → both below threshold (min 3 practices, 10 providers)
    Expected:
      - check_small_network returns True → composite score NOT issued
      - Leakage index still computable
      - Orphan share still computable
    """

    PRACTICE_COUNT = 2
    PROVIDER_COUNT = 8

    def test_small_network_refused(self):
        assert check_small_network(self.PRACTICE_COUNT, self.PROVIDER_COUNT) is True

    def test_small_network_refused_only_practices(self):
        assert check_small_network(2, 20) is True

    def test_small_network_refused_only_providers(self):
        assert check_small_network(5, 9) is True

    def test_small_network_passes_at_threshold(self):
        assert check_small_network(3, 10) is False

    def test_leakage_still_computable_for_narrative(self):
        li = compute_leakage_index(0.40, 0.60)
        assert li == pytest.approx(0.36)

    def test_orphan_computable_for_narrative(self):
        entities = [_E("x", "owned", 1.0), _E("y", "owned", 1.0)]
        share = compute_orphan_volume_share(entities, {"x"})
        assert share == pytest.approx(0.50)


# ── Edge cases shared across both fixtures ────────────────────────────────────

class TestCeilingEdgeCases:
    def test_attribution_ceiling_prevents_large_uplift(self):
        r = compute_composite(
            hospital_score=50.0, ns=95.0, sar=0.25,  # SAR < 0.40 → ceiling
            coherence=0.99, modifiers_total=0.0,
            orphan_volume_share=0.0, network_share=0.50,
        )
        assert r["score_ceiling_applied"]
        assert r["composite_score"] <= 50.0 + COMPOSITE_CONFIG["ceilings"]["attribution_max_lift"] + 0.01

    def test_orphan_ceiling_caps_at_74(self):
        r = compute_composite(
            hospital_score=80.0, ns=90.0, sar=0.95,
            coherence=0.99, modifiers_total=0.0,
            orphan_volume_share=0.35, network_share=0.50,  # >0.30 → orphan ceiling
        )
        assert r["score_ceiling_applied"]
        assert r["composite_score"] <= 74.0 + 0.01

    def test_both_ceilings_most_restrictive_wins(self):
        # Low SAR + high orphan volume → both fire; orphan cap (74) < hospital+5
        r = compute_composite(
            hospital_score=80.0, ns=95.0, sar=0.15,   # attr ceiling = 80+5=85
            coherence=0.99, modifiers_total=0.0,
            orphan_volume_share=0.40, network_share=0.50,  # orphan cap = 74
        )
        # Orphan ceiling (74) wins over attribution ceiling (85)
        assert r["composite_score"] <= 74.0 + 0.01

    def test_modifiers_reduce_score(self):
        r_no_mod = compute_composite(70.0, 70.0, 0.80, 0.70, 0.0, 0.0, 0.30)
        r_mod    = compute_composite(70.0, 70.0, 0.80, 0.70, 5.0, 0.0, 0.30)
        assert r_mod["composite_score"] < r_no_mod["composite_score"]
