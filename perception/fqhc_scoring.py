"""FQHC Community Health Edition AI Visibility Score.

5-pillar rubric — Community Health Edition v1.0.

Pillar weights (sum to 1.0):
  access_findability        0.25  (sub-components below)
    mqcr_score              0.12  — Mission Query Capture Rate; requires battery
    multilingual_score      0.08  — Multilingual capture; requires battery
    service_adjacent_score  0.05  — Service-adjacent findability; always assessed

  eligibility_cost_accuracy 0.25
  site_service_completeness 0.20
  experience_reputation     0.20
  institutional_signals     0.10

Round 1 (no battery): mqcr_score and multilingual_score are None.
weight_used ≈ 0.80 → still above 0.50 gate, composite renders.
Round 2: all sub-scores available, weight_used = 1.0.
"""
from __future__ import annotations

from typing import Optional

from . import scoring

# Sub-score keys and their fractional weights (sum to 1.0)
FQHC_SUB_WEIGHTS: dict[str, float] = {
    "mqcr_score":               0.12,
    "multilingual_score":       0.08,
    "service_adjacent_score":   0.05,
    "eligibility_cost_accuracy": 0.25,
    "site_service_completeness": 0.20,
    "experience_reputation":    0.20,
    "institutional_signals":    0.10,
}

# Display labels for each pillar (used in PDF and briefing)
FQHC_PILLAR_LABELS: dict[str, str] = {
    "mqcr_score":                "Mission Query Capture",
    "multilingual_score":        "Multilingual Capture",
    "service_adjacent_score":    "Service-Adjacent Findability",
    "eligibility_cost_accuracy": "Eligibility & Cost Accuracy",
    "site_service_completeness": "Site & Service Completeness",
    "experience_reputation":     "Experience & Reputation",
    "institutional_signals":     "Institutional Signals",
}

# The 5 top-level pillar labels (for display in scorecards)
FQHC_PILLAR5_LABELS: list[tuple[str, str]] = [
    ("access_findability",        "Access & Findability"),
    ("eligibility_cost_accuracy", "Eligibility & Cost Accuracy"),
    ("site_service_completeness", "Site & Service Completeness"),
    ("experience_reputation",     "Experience & Reputation"),
    ("institutional_signals",     "Institutional Signals"),
]


def composite(sub_scores: dict[str, Optional[float]]) -> Optional[int]:
    """Compute composite AI Visibility Score from FQHC sub-scores.

    sub_scores keys: see FQHC_SUB_WEIGHTS.  None values are excluded from
    weight_used (e.g. mqcr_score=None in Round 1 excludes 12pts of weight).
    Returns None when weight_used == 0 or weight_used < 0.50.
    """
    total = 0.0
    weight_used = 0.0
    for key, w in FQHC_SUB_WEIGHTS.items():
        val = sub_scores.get(key)
        if val is None:
            continue
        total += float(val) * w
        weight_used += w
    if weight_used == 0:
        return None
    if weight_used < 0.50:
        return None
    return round(total / weight_used)


def pillar1_score(sub_scores: dict[str, Optional[float]]) -> Optional[int]:
    """Compute Pillar 1 Access & Findability score (0-100) from available sub-scores.

    Returns None if no Pillar 1 sub-score is available.
    """
    p1_keys = ("mqcr_score", "multilingual_score", "service_adjacent_score")
    total = 0.0
    weight_used = 0.0
    for k in p1_keys:
        w = FQHC_SUB_WEIGHTS[k]
        v = sub_scores.get(k)
        if v is not None:
            total += float(v) * w
            weight_used += w
    if weight_used == 0:
        return None
    return round(total / weight_used)


def weight_used_pct(sub_scores: dict[str, Optional[float]]) -> float:
    """Return the fraction of total weight actually scored (0.0–1.0)."""
    used = sum(w for k, w in FQHC_SUB_WEIGHTS.items() if sub_scores.get(k) is not None)
    return used


def mqcr_to_score(mqcr: float) -> int:
    """Convert raw MQCR fraction (0.0–1.0) to 0–100 sub-score.

    Anchor points:
      1.0  → 100  (all 10 queries surfaced the center)
      0.7  →  75  (7/10 — good visibility)
      0.5  →  50  (5/10 — marginal)
      0.0  →   0  (no queries surfaced the center)

    Linear interpolation between anchors.
    """
    return round(max(0.0, min(1.0, float(mqcr))) * 100)


def experience_band(rating: Optional[float], review_count: Optional[int]) -> Optional[int]:
    """Derive Experience & Reputation sub-score from Google front-door read.

    Same anchor rubric as scoring.experience_band(), reused here with the
    FQHC context note that patient volumes at CHCs are typically lower.
    """
    return scoring.experience_band(rating, review_count)
