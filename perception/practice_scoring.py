"""Practice-edition AI Visibility Score: pillar weights, composite, and ceiling logic.

practice-rubric.md §2.1–§2.6. Semantically remaps the four TierScores keys that
the hospital rubric uses (they are reused for DB/model compatibility):

  clinical_outcomes_safety   → Pillar 1: Practitioner Credentials & Clinical Quality
  credentials_recognition    → Pillar 2: Reviews & Reputation
  patient_experience_reviews → Pillar 3: Identity & Machine-Readability
  access_fit                 → Pillar 4: Access & Fit

The hospital pipeline is not touched — these functions are only called by
practice_analyzer.py.
"""
from __future__ import annotations

from typing import Optional

from . import scoring

# Pillar weights by practice profile (§2.1, Pillar weights table)
WEIGHTS: dict[str, dict[str, float]] = {
    "practice_procedural": {
        "clinical_outcomes_safety":   0.30,
        "credentials_recognition":    0.30,
        "patient_experience_reviews": 0.20,
        "access_fit":                 0.20,
    },
    "practice_relationship": {
        "clinical_outcomes_safety":   0.15,
        "credentials_recognition":    0.35,
        "patient_experience_reviews": 0.20,
        "access_fit":                 0.30,
    },
    "practice_referral_fed": {
        "clinical_outcomes_safety":   0.40,
        "credentials_recognition":    0.20,
        "patient_experience_reviews": 0.25,
        "access_fit":                 0.15,
    },
}

# HYBRID = 50/50 blend of procedural and relationship (§2.1)
WEIGHTS["practice_hybrid"] = {
    k: (WEIGHTS["practice_procedural"][k] + WEIGHTS["practice_relationship"][k]) / 2
    for k in WEIGHTS["practice_procedural"]
}


def composite(
    tier_scores: dict[str, Optional[float]],
    profile: str,
    entity_resolution_pct: Optional[float] = None,
    linkage_integrity_pct: Optional[float] = None,
    board_cert_unverifiable: bool = False,
) -> tuple[Optional[int], bool, str]:
    """Compute composite AI Visibility Score for a practice.

    Returns (score, ceiling_applied, ceiling_reason).
    Ceiling logic (§2.6): no practice may score above 74 if board cert is
    unverifiable, entity resolution <85 %, or linkage integrity <70 %.
    """
    weights = WEIGHTS.get(profile, WEIGHTS["practice_procedural"])
    total = 0.0
    weight_used = 0.0
    for key, w in weights.items():
        val = tier_scores.get(key)
        if val is None:
            continue
        total += float(val) * w
        weight_used += w

    if weight_used == 0:
        return None, False, ""

    raw = round(total / weight_used)

    triggers: list[str] = []
    if board_cert_unverifiable:
        triggers.append("board certification unverifiable")
    if entity_resolution_pct is not None and entity_resolution_pct < 85:
        triggers.append(f"entity resolution {entity_resolution_pct:.0f}%")
    if linkage_integrity_pct is not None and linkage_integrity_pct < 70:
        triggers.append(f"linkage integrity {linkage_integrity_pct:.0f}%")

    if triggers:
        ceiling_reason = "; ".join(triggers)
        return min(raw, 74), True, ceiling_reason
    return raw, False, ""


def reviews_band(
    google_rating: Optional[float],
    google_count: Optional[int],
    physician_panel_avg: Optional[float] = None,
) -> Optional[int]:
    """Derive the Reviews & Reputation tier (Pillar 2, 0–100) from Google data.

    Anchored on the practice-edition review volume tiers from §2.3:
    practice volume tiers differ from hospital (<25 / 25–100 / 100–400 / 400+).
    physician_panel_avg is an optional 0–100 score for physician-level reviews
    (Healthgrades/Vitals); when provided it blends 50/50 with the org score.
    """
    if google_rating is None:
        return None
    count = google_count or 0

    # Base from rating
    if google_rating >= 4.5:
        base = 88
    elif google_rating >= 4.0:
        base = 75
    elif google_rating >= 3.5:
        base = 60
    elif google_rating >= 3.0:
        base = 45
    else:
        base = 30

    # Practice volume nudge (smaller practices → thinner review base)
    if count >= 400:
        base += 4
    elif count >= 100:
        base += 2
    elif count < 25:
        base -= 8
    elif count < 100:
        base -= 4

    org_score = max(0, min(100, base))

    if physician_panel_avg is not None:
        return round((org_score + physician_panel_avg) / 2)
    return org_score
