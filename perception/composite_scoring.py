"""Pure scoring functions for the System Composite (Tier 3) rubric.

All constants imported from composite_config — no inline magic numbers here.
"""
from __future__ import annotations

from .composite_config import COMPOSITE_CONFIG


def footprint_class_and_weights(network_share: float) -> tuple[str, float, float]:
    """Return (class_name, w_h, w_n) from the network's share of total encounters."""
    cfg = COMPOSITE_CONFIG["footprint_classes"]
    if network_share < cfg["FACILITY-CENTRIC"]["max_network_share"]:
        return "FACILITY-CENTRIC", cfg["FACILITY-CENTRIC"]["w_h"], cfg["FACILITY-CENTRIC"]["w_n"]
    if network_share < cfg["BALANCED"]["max_network_share"]:
        return "BALANCED", cfg["BALANCED"]["w_h"], cfg["BALANCED"]["w_n"]
    return "AMBULATORY-FORWARD", cfg["AMBULATORY-FORWARD"]["w_h"], cfg["AMBULATORY-FORWARD"]["w_n"]


def continuum_bonus(coherence: float) -> float:
    """Continuum Bonus from Continuum Coherence score (§2.3)."""
    cfg = COMPOSITE_CONFIG["continuum_bonus"]
    if coherence >= cfg["high_threshold"]:
        return float(cfg["high_bonus"])
    if coherence >= cfg["mid_threshold"]:
        return float(cfg["mid_bonus"])
    return float(cfg["low_bonus"])


def attributed_network_score(ns: float, sar: float) -> float:
    """ANS = NS × (0.5 + 0.5 × SAR) — the attribution gate (§2.1)."""
    gate = COMPOSITE_CONFIG["attribution_gate"]
    return ns * (gate["floor"] + gate["sar_weight"] * sar)


def network_score(practice_scores: list[float], weights: list[float]) -> float:
    """Volume-weighted average of included practice scores (NS)."""
    total = sum(weights)
    if not practice_scores or total == 0:
        return 0.0
    return sum(s * w for s, w in zip(practice_scores, weights)) / total


def check_small_network(practice_count: int, provider_count: int) -> bool:
    """True → network too small; do NOT issue a composite score."""
    cfg = COMPOSITE_CONFIG["small_network"]
    return practice_count < cfg["min_practices"] or provider_count < cfg["min_providers"]


def compute_composite(
    hospital_score: float,
    ns: float,
    sar: float,
    coherence: float,
    modifiers_total: float,
    orphan_volume_share: float,
    network_share: float,
) -> dict:
    """
    Full composite formula with all ceilings and floors applied in order.

    Returns a dict with intermediate values and the final composite_score.
    Ceilings applied in order:
      1. Attribution ceiling (low SAR)
      2. Orphan ceiling (too many orphaned practices by volume)
      3. No-masking floor (blend + bonus can't conjure points from thin air)
      4. Final clamp 0–100
    """
    caps = COMPOSITE_CONFIG["ceilings"]

    ans         = attributed_network_score(ns, sar)
    fc, w_h, w_n = footprint_class_and_weights(network_share)
    bonus        = continuum_bonus(coherence)

    sc = hospital_score * w_h + ans * w_n + bonus - modifiers_total

    ceiling_applied = False
    ceiling_reasons: list[str] = []

    # 1. Attribution ceiling
    if sar < caps["attribution_sar_threshold"]:
        limit = hospital_score + caps["attribution_max_lift"]
        if sc > limit:
            sc = limit
            ceiling_applied = True
            ceiling_reasons.append(
                f"SAR {sar:.0%} < {caps['attribution_sar_threshold']:.0%} — "
                f"network lift capped at +{caps['attribution_max_lift']} pts"
            )

    # 2. Orphan ceiling
    if orphan_volume_share > caps["orphan_volume_threshold"]:
        if sc > caps["orphan_cap"]:
            sc = float(caps["orphan_cap"])
            ceiling_applied = True
            ceiling_reasons.append(
                f"{orphan_volume_share:.0%} of network volume in Orphan List — "
                f"capped at {caps['orphan_cap']}"
            )

    # 3. No-masking floor
    best_base = max(hospital_score, ns)
    no_mask_limit = best_base + caps["no_masking_max_lift"]
    if sc > no_mask_limit:
        sc = no_mask_limit
        ceiling_applied = True
        ceiling_reasons.append(
            f"No-masking rule — capped at best base score "
            f"({best_base:.0f}) + {caps['no_masking_max_lift']}"
        )

    # 4. Final clamp
    sc = max(0.0, min(100.0, sc))

    return {
        "network_score":             round(ns, 1),
        "attributed_network_score":  round(ans, 1),
        "footprint_class":           fc,
        "w_h":                       w_h,
        "w_n":                       w_n,
        "continuum_bonus":           bonus,
        "composite_score":           round(sc, 1),
        "composite_grade":           _score_to_grade(sc),
        "merged_entity_delta":       round(sc - hospital_score, 1),
        "score_ceiling_applied":     ceiling_applied,
        "score_ceiling_reason":      "; ".join(ceiling_reasons) if ceiling_reasons else None,
    }


def compute_sar(
    entities: list,                     # NetworkEntity objects
    linkage_data: dict,                 # {entity_id: linkage_integrity_pct  (0–100 scale)}
    battery_resolution_score: float = 0.0,  # normalized 0–1 from N1/N4 battery
) -> float:
    """
    Volume-weighted SAR (0–1 scale).

    Blends Tier 2 linkage_integrity_pct (70%) with N1/N4 network battery (30%)
    per composite_config sar_tier2_weight / sar_battery_weight.
    IN-TRANSITION entities whose attribution is inside the 12-month integration
    grace window should have their linkage_data entry set to None by the caller;
    they are excluded from the tier-2 component but their weight still counts.
    """
    from .composite_models import OwnershipTier

    t2_w   = COMPOSITE_CONFIG["sar_tier2_weight"]
    bat_w  = COMPOSITE_CONFIG["sar_battery_weight"]

    scoreable = [
        e for e in entities
        if e.inclusion_tier != OwnershipTier.affiliated
    ]
    total_weight = sum(e.inclusion_weight for e in scoreable)
    if total_weight == 0:
        return 0.0

    tier2_weighted = 0.0
    for e in scoreable:
        val = linkage_data.get(e.id)   # 0–100 or None
        if val is not None:
            tier2_weighted += (val / 100.0) * e.inclusion_weight
    tier2_sar = tier2_weighted / total_weight

    return tier2_sar * t2_w + battery_resolution_score * bat_w


def compute_leakage_index(sar: float, weighted_capture_rate: float) -> float:
    """% of physician-first wins that are unattributed to the system."""
    return max(0.0, (1.0 - sar) * weighted_capture_rate)


def compute_orphan_volume_share(entities: list, orphan_ids: set) -> float:
    """Fraction of included network volume sitting in Orphan List practices."""
    from .composite_models import OwnershipTier
    scoreable = [e for e in entities if e.inclusion_tier != OwnershipTier.affiliated]
    total = sum(e.inclusion_weight for e in scoreable)
    if total == 0:
        return 0.0
    orphan = sum(e.inclusion_weight for e in scoreable if e.id in orphan_ids)
    return orphan / total


def points_recoverable(
    entity: object,         # NetworkEntity
    hospital_score: float,
    all_entities: list,
    linkage_data: dict,
    battery_resolution: float,
    coherence: float,
    modifiers_total: float,
    network_share: float,
) -> float:
    """
    Re-run the composite gate as if this entity had perfect linkage (100%)
    to quantify "points recoverable" for the Orphan List report section.
    """
    patched = dict(linkage_data)
    patched[entity.id] = 100.0

    sar_fixed = compute_sar(all_entities, patched, battery_resolution)

    scores  = []
    weights = []
    for e in all_entities:
        if e.inclusion_tier.value == "AFFILIATED":
            continue
        score = linkage_data.get(e.id, 50.0)  # fallback estimate
        scores.append(score)
        weights.append(e.inclusion_weight)

    ns_fixed  = network_score(scores, weights)
    result    = compute_composite(
        hospital_score, ns_fixed, sar_fixed, coherence,
        modifiers_total, 0.0, network_share,
    )
    return result["composite_score"]


def _score_to_grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 85: return "A−"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    if score >= 70: return "B−"
    if score >= 65: return "C+"
    if score >= 60: return "C"
    if score >= 55: return "C−"
    if score >= 45: return "D"
    return "F"
