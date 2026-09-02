"""Deterministic AI Visibility Score — the real algorithm (INTL-SALES-119).

The composite is computed *in code* from the four tier scores by a fixed,
specialty-aware weighting profile — it is never invented by the model. The
weights are the usage-blended source weightings the four leading AI assistants
state they use to recommend providers; the profile inverts between procedural
(objective outcomes dominate) and relationship (quality/access/experience
dominate) care. The Experience & Reviews tier can be derived straight from a
verified Google read via the anchor rubric, so the one tier we have hard data
for is reproducible rather than discretionary.
"""
from __future__ import annotations

from typing import Optional

# Canonical tier keys (stable across profiles; the *label* of the first slot
# changes by profile — see TIER_LABELS).
TIER_KEYS = (
    "clinical_outcomes_safety",
    "credentials_recognition",
    "patient_experience_reviews",
    "access_fit",
)

# Usage-blended weights from INTL-SALES-119. Keys are TIER_KEYS.
WEIGHTS: dict[str, dict[str, float]] = {
    # Procedural / surgical (ortho, cardiac, spine, surgical oncology) and
    # general hospital markets — objective outcomes dominate, reviews are tie-breakers.
    "procedural": {
        "clinical_outcomes_safety": 0.46,
        "credentials_recognition": 0.35,
        "patient_experience_reviews": 0.10,
        "access_fit": 0.09,
    },
    # Relationship / longitudinal (primary care, behavioral, chronic, multi-specialty)
    # — quality & coordination, access, and experience dominate; rankings recede.
    "relationship": {
        "clinical_outcomes_safety": 0.40,   # slot relabeled "Quality & Coordination"
        "access_fit": 0.24,
        "patient_experience_reviews": 0.23,
        "credentials_recognition": 0.13,
    },
    # Student Health (on-campus university clinics). No CMS/hospital data — the
    # slots are remapped to student-relevant pillars (see PRACTICE_TIER_LABELS
    # below). Registered here so scoring.composite_score() applies these weights.
    #   clinical_outcomes_safety   → Services & Access                     (0.25)
    #   credentials_recognition    → Findability & Identity                (0.25)
    #   patient_experience_reviews → Reviews & Reputation                  (0.30)
    #   access_fit                 → Machine-Readability & Digital Presence(0.20)
    "practice_student_health": {
        "clinical_outcomes_safety":   0.25,
        "credentials_recognition":    0.25,
        "patient_experience_reviews": 0.30,
        "access_fit":                 0.20,
    },
}

# Report-facing label for the first slot, which differs by profile.
TIER_LABELS: dict[str, dict[str, str]] = {
    "procedural": {
        "clinical_outcomes_safety": "Outcomes & Safety",
        "credentials_recognition": "Credentials & Recognition",
        "patient_experience_reviews": "Experience & Reviews",
        "access_fit": "Access & Fit",
    },
    "relationship": {
        "clinical_outcomes_safety": "Quality & Coordination",
        "credentials_recognition": "Credentials & Recognition",
        "patient_experience_reviews": "Experience & Reviews",
        "access_fit": "Access & Fit",
    },
}

# Specialty → profile classification. Substring match, case-insensitive.
_PROCEDURAL_HINTS = (
    "ortho", "surg", "cardiac", "cardio", "spine", "neuro", "oncolog",
    "urolog", "ent", "otolaryng", "ophthalm", "vascular", "plastic",
    "transplant", "bariatric", "gastro", "colorectal", "obgyn", "obstetr",
    "gynecolog", "podiat", "anesthes", "interventional", "radiolog",
)
_RELATIONSHIP_HINTS = (
    "primary", "family", "internal medicine", "internist", "pediatr",
    "behavioral", "psych", "mental", "geriatr", "chronic", "endocrin",
    "rheumatolog", "nephrolog", "pulmonolog", "allerg", "dermatolog",
    "multi-special", "multispecial", "general practice",
)


def classify_profile(specialty: Optional[str], mode: str = "hospital") -> str:
    """Pick the weighting profile for a run.

    Hospital markets default to ``procedural`` (outcomes/safety lead general
    acute care). For a specialty, classify by the specialty term, defaulting to
    ``procedural`` when ambiguous (the conservative, outcomes-led choice).
    """
    if not specialty:
        return "procedural"
    s = specialty.lower()
    if any(h in s for h in _RELATIONSHIP_HINTS):
        return "relationship"
    if any(h in s for h in _PROCEDURAL_HINTS):
        return "procedural"
    return "procedural"


def composite_score(tier_scores: dict[str, float], profile: str) -> Optional[int]:
    """Weighted blend of the four tier scores → 0–100 integer.

    Returns None if no tier could be scored (so the report shows the score as
    unavailable rather than a misleading 0).
    """
    weights = WEIGHTS.get(profile, WEIGHTS["procedural"])
    total = 0.0
    weight_used = 0.0
    for key, w in weights.items():
        val = tier_scores.get(key)
        if val is None:
            continue
        total += float(val) * w
        weight_used += w
    if weight_used == 0:
        return None
    if weight_used < 0.5:
        # More than half the total weight is unscored — collection was too thin
        # to produce a credible composite. Caller must treat None as a failed run.
        return None
    return round(total / weight_used)


# ─────────────────────────────────────────────────────────────────────────────
# Practice Edition constants (practice-rubric.md §2.1–§2.5)
# Tier keys are REUSED from TIER_KEYS with semantic remapping per profile:
#   clinical_outcomes_safety   → Pillar 1: Practitioner Credentials & Clinical Quality
#   credentials_recognition    → Pillar 2: Reviews & Reputation
#   patient_experience_reviews → Pillar 3: Identity & Machine-Readability
#   access_fit                 → Pillar 4: Access & Fit
# ─────────────────────────────────────────────────────────────────────────────

PRACTICE_TIER_LABELS: dict[str, dict[str, str]] = {
    profile: {
        "clinical_outcomes_safety":   "Practitioner Credentials & Clinical Quality",
        "credentials_recognition":    "Reviews & Reputation",
        "patient_experience_reviews": "Identity & Machine-Readability",
        "access_fit":                 "Access & Fit",
    }
    for profile in (
        "practice_procedural", "practice_relationship",
        "practice_referral_fed", "practice_hybrid",
    )
}

# Student Health uses its own pillar labels (on-campus clinics have no clinical
# outcomes / credentials data — the four slots become student-relevant pillars).
PRACTICE_TIER_LABELS["practice_student_health"] = {
    "clinical_outcomes_safety":   "Services & Access",
    "credentials_recognition":    "Findability & Identity",
    "patient_experience_reviews": "Reviews & Reputation",
    "access_fit":                 "Machine-Readability & Digital Presence",
}

PRACTICE_PROFILE_DISPLAY: dict[str, str] = {
    "practice_procedural":   "Procedural",
    "practice_relationship": "Relationship",
    "practice_referral_fed": "Referral-Fed",
    "practice_hybrid":       "Hybrid",
    "practice_student_health": "Student Health",
}


def grade_from_score(score: int | None) -> tuple[str, str]:
    """Return (quartile_code, quartile_label) for a 0–100 AI Visibility Score.

    Thresholds calibrated against 270 scored entities in the Rank2 database
    (Aug 2026): P25=58, P50=68, P75=74.

      ≥ 75 → Q1 / Top Quartile
      68–74 → Q2 / Upper Middle
      58–67 → Q3 / Lower Middle
        <58 → Q4 / Bottom Quartile
       None → —  / Unscored
    """
    if score is None:
        return "—", "Unscored"
    s = max(0, min(100, score))
    if s >= 75: return "Q1", "Top Quartile"
    if s >= 68: return "Q2", "Upper Middle"
    if s >= 58: return "Q3", "Lower Middle"
    return "Q4", "Bottom Quartile"


def experience_band(
    rating: Optional[float],
    review_count: Optional[int],
    *,
    recency: Optional[str] = None,
) -> Optional[int]:
    """Derive the Experience & Reviews tier (0–100) from a Google read.

    Anchor rubric (INTL-SALES-119): rating × volume × recency.
      4.5★+ / high volume / active → 85+
      4.0–4.4 → 70–84
      3.5–3.9 → 55–69
      3.0–3.4 → 40–54
      < 3.0 or thin/stale → < 40
    """
    if rating is None:
        return None
    count = review_count or 0

    if rating >= 4.5:
        base = 88
    elif rating >= 4.0:
        base = 77
    elif rating >= 3.5:
        base = 62
    elif rating >= 3.0:
        base = 47
    else:
        base = 33

    # Volume nudges within ±5: thin review counts undercut a high star rating;
    # deep volume reinforces it.
    if count >= 1000:
        base += 4
    elif count >= 300:
        base += 2
    elif count < 50:
        base -= 6
    elif count < 150:
        base -= 3

    if recency and recency.lower() in {"stale", "inactive"}:
        base -= 5

    return max(0, min(100, base))
