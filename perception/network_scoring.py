"""Network Pulse — scoring helpers for multi-state hospital network reports.

Three score dimensions, weighted composite:
  brand_visibility    40%  — network recognized by name in AI responses
  market_coverage     35%  — hospitals surface in local "near me" queries
  information_accuracy 25% — facts (services, size, capabilities) are correct

Grade bands:
  A   80+   Top Quartile
  B   65–79  Above Average
  C   50–64  Industry Average
  D   35–49  Below Average
  F   <35    Bottom Quartile
"""
from __future__ import annotations

from typing import Optional

# Dimension weights (must sum to 1.0)
NETWORK_WEIGHTS: dict[str, float] = {
    "brand_visibility":      0.40,
    "market_coverage":       0.35,
    "information_accuracy":  0.25,
}

# Grade thresholds (score → letter, band)
_GRADE_BANDS: list[tuple[int, str, str]] = [
    (80, "A", "Top Quartile"),
    (65, "B", "Above Average"),
    (50, "C", "Industry Average"),
    (35, "D", "Below Average"),
    (0,  "F", "Bottom Quartile"),
]


def composite(
    brand: Optional[int],
    market: Optional[int],
    accuracy: Optional[int],
) -> Optional[int]:
    """Compute composite AI Visibility Score from the three network dimensions.

    Returns None if no dimension scores are provided.  Partial sets are
    re-weighted proportionally (same pattern as fqhc_scoring.composite).
    """
    pairs = [
        ("brand_visibility",     brand),
        ("market_coverage",      market),
        ("information_accuracy", accuracy),
    ]
    total = 0.0
    weight_used = 0.0
    for key, val in pairs:
        if val is None:
            continue
        w = NETWORK_WEIGHTS[key]
        total += float(val) * w
        weight_used += w
    if weight_used == 0:
        return None
    return round(total / weight_used)


def grade_band(score: Optional[int]) -> tuple[str, str]:
    """Return (letter_grade, band_label) for a composite score.

    Returns ("—", "Not Scored") when score is None.
    """
    if score is None:
        return ("—", "Not Scored")
    for threshold, letter, label in _GRADE_BANDS:
        if score >= threshold:
            return (letter, label)
    return ("F", "Bottom Quartile")


def facility_grade(score: Optional[int]) -> str:
    """Return letter grade for an individual facility score.

    Uses the same thresholds as grade_band so A/B/C/D/F are consistent
    with the network-level letter grades.
    """
    if score is None:
        return "—"
    letter, _ = grade_band(score)
    return letter
