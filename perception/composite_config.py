"""Version-controlled configuration for the System Composite (Tier 3) rubric.

All gate parameters, blend weights, ceilings, and floors live here.
Any change to a numeric constant must bump COMPOSITE_CONFIG["version"].
"""
from __future__ import annotations

COMPOSITE_CONFIG: dict = {
    "version": "composite-v1.0",

    # Attribution gate: ANS = NS × (floor + sar_weight × SAR)
    "attribution_gate": {
        "floor": 0.5,
        "sar_weight": 0.5,
    },

    # Footprint classes — keyed by name, ordered by max_network_share ascending
    "footprint_classes": {
        "FACILITY-CENTRIC": {
            "max_network_share": 0.25,   # network share < 25%
            "w_h": 0.75,
            "w_n": 0.25,
        },
        "BALANCED": {
            "max_network_share": 0.50,   # 25–50%
            "w_h": 0.60,
            "w_n": 0.40,
        },
        "AMBULATORY-FORWARD": {
            "max_network_share": 1.01,   # > 50%
            "w_h": 0.50,
            "w_n": 0.50,
        },
    },

    # Continuum Bonus (from Continuum Coherence %)
    "continuum_bonus": {
        "high_threshold": 0.80,
        "high_bonus": 4,
        "mid_threshold": 0.60,
        "mid_bonus": 2,
        "low_bonus": 0,
    },

    # Score ceilings and floors
    "ceilings": {
        "attribution_sar_threshold": 0.40,   # SAR below this → lift capped
        "attribution_max_lift": 5,           # max points composite can exceed hospital score at low SAR
        "orphan_volume_threshold": 0.30,     # orphan volume above this → hard cap
        "orphan_cap": 74,
        "no_masking_max_lift": 6,            # composite ≤ max(H, NS) + this
    },

    # Small-network refusal (no composite issued below these thresholds)
    "small_network": {
        "min_practices": 3,
        "min_providers": 10,
    },

    # Optional strategic multiplier on up to N designated practices
    "strategic_multiplier": {
        "max_designations": 3,
        "min_value": 1.25,
        "max_value": 1.50,
    },

    # Inclusion weight by ownership tier (before renormalization)
    "inclusion_weights": {
        "OWNED": 1.0,
        "CONTROLLED": 0.5,
        "AFFILIATED": 0.0,       # excluded from scoring
        "IN-TRANSITION": 1.0,    # full weight but SAR attribution excluded per integration grace
    },

    # SAR computation: blend of Tier 2 linkage data and network battery
    "sar_tier2_weight": 0.70,    # 70% from linkage_integrity_pct
    "sar_battery_weight": 0.30,  # 30% from N1/N4 network_resolution

    # Staleness rules
    "staleness_days": 90,
    "cross_tier_flag_days": 45,
    "integration_grace_months": 12,
    "registry_reattest_days": 90,
}
