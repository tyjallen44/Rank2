"""Practice-edition profile classification for the AI Visibility Score (practice-rubric.md §2.1)."""
from __future__ import annotations

from enum import Enum
from typing import Optional


class PracticeProfile(str, Enum):
    procedural   = "practice_procedural"
    relationship = "practice_relationship"
    referral_fed = "practice_referral_fed"
    hybrid       = "practice_hybrid"


# Specialty substring hints → profile (checked in priority order)
_REFERRAL_FED = (
    "oncolog", "cardiol", "cardio", "nephrol", "rheumatol", "hematol",
    "infect", "endocrin", "surgical subspecialt",
)
_RELATIONSHIP = (
    "primary care", "family", "internal medicine", "internist", "pediatr",
    "ob/gyn", "behavioral", "mental health", "psych", "geriatr",
    "dental", "vision", "optom", "general practice",
)
_PROCEDURAL = (
    "ortho", "plastic", "ophthalmol", "lasik", "cataract", "fertility",
    "bariatric", "dermatol", "cosmetic", "oral surg", "obgyn", "obstet",
    "gynecol", "spine", "neuro surg", "vascular", "ent", "otolaryng",
    "urolog", "podiat", "audiol",
)


def classify_practice_profile(specialty: Optional[str]) -> str:
    """Map a practice specialty to its AI Visibility weighting profile code.

    Returns one of the four PracticeProfile enum values. When a specialty
    matches both referral-fed and procedural patterns, referral-fed wins
    (e.g. cardiac surgery is referral-driven despite being procedural in
    technique).
    """
    if not specialty:
        return PracticeProfile.procedural.value
    s = specialty.lower()
    if any(h in s for h in _REFERRAL_FED):
        return PracticeProfile.referral_fed.value
    if any(h in s for h in _RELATIONSHIP):
        return PracticeProfile.relationship.value
    if any(h in s for h in _PROCEDURAL):
        return PracticeProfile.procedural.value
    return PracticeProfile.procedural.value


PROFILE_DISPLAY: dict[str, str] = {
    "practice_procedural":   "Procedural",
    "practice_relationship": "Relationship",
    "practice_referral_fed": "Referral-Fed",
    "practice_hybrid":       "Hybrid",
}
