"""Student Health Clinics — roster resolver.

Given a grouping (state | radius | conference), enumerate the NCAA Division I
universities and resolve each to its PRIMARY on-campus student medical/health
center (name, city, state, URL). The resolved roster is shown to the user for
confirmation before any scoring, so model-knowledge gaps (e.g. conference
realignment) can be corrected by hand.

This module only builds the roster. Scoring/ranking reuses the practice engine
with the `practice_student_health` weighting profile.
"""
from __future__ import annotations

import json
from typing import Optional

import anthropic

client = anthropic.Anthropic()
_MODEL = "claude-opus-4-8"

# Supported athletic conferences: key → display name.
CONFERENCES: dict[str, str] = {
    "big12":        "Big 12",
    "bigten":       "Big Ten",
    "sec":          "SEC",
    "acc":          "ACC",
    "pac12":        "Pac-12",
    "bigeast":      "Big East",
    "aac":          "American Athletic Conference (AAC)",
    "mountainwest": "Mountain West",
}

_ROSTER_TOOL = {
    "name": "submit_student_health_roster",
    "description": "Submit the universities and their primary on-campus student health/medical centers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "group_label": {
                "type": "string",
                "description": "Human label for this group, e.g. 'Big 12 Conference' or 'Universities in Utah'.",
            },
            "schools": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "school": {"type": "string", "description": "University name, e.g. 'Brigham Young University'."},
                        "clinic_name": {"type": "string", "description": "Official name of the primary on-campus student medical/health center, e.g. 'BYU Student Health Center'."},
                        "city": {"type": "string"},
                        "state": {"type": "string", "description": "Two-letter state code, e.g. 'UT'."},
                        "url": {"type": "string", "description": "Official website URL of the student health center (include https://). Empty string if unknown."},
                    },
                    "required": ["school", "clinic_name", "city", "state"],
                },
            },
            "note": {"type": "string", "description": "Any caveats — schools omitted, membership uncertainty, etc."},
        },
        "required": ["group_label", "schools"],
    },
}

_SYSTEM = (
    "You are an expert on U.S. higher education and NCAA Division I athletics. For a "
    "requested group of universities, you enumerate the schools and identify each one's "
    "PRIMARY on-campus student medical/health center — the clinic that provides general "
    "medical care to enrolled students. Do NOT return the counseling center, the pharmacy "
    "alone, or the university's teaching hospital; return the student health/medical clinic "
    "as students would refer to it. Provide each clinic's official name, city, two-letter "
    "state code, and official website URL when known. Include only schools that operate an "
    "on-campus student health center. Be accurate and current, reflecting the latest NCAA "
    "conference realignment; if a detail is uncertain, still include the school and note it."
)


def _build_prompt(mode: str, state: Optional[str], conference: Optional[str],
                  anchor_school: Optional[str], radius_miles: Optional[int]) -> str:
    if mode == "conference":
        conf = CONFERENCES.get((conference or "").lower(), conference or "")
        return (f"List the CURRENT full member universities of the {conf} athletic conference, "
                f"reflecting the latest conference realignment. For each member university, identify "
                f"its primary on-campus student medical/health center.")
    if mode == "state":
        return (f"List the NCAA Division I universities in {state} that have an on-campus student "
                f"medical/health center. For each, identify that center.")
    if mode == "radius":
        return (f"List the NCAA Division I universities located within approximately {radius_miles} "
                f"miles of {anchor_school}. Include {anchor_school} itself. For each university, "
                f"identify its primary on-campus student medical/health center.")
    raise ValueError(f"unknown grouping mode: {mode!r}")


def resolve_roster(mode: str, *, state: Optional[str] = None,
                   conference: Optional[str] = None, anchor_school: Optional[str] = None,
                   radius_miles: Optional[int] = None) -> dict:
    """Resolve a group of universities to their student health clinics.

    Returns {"group_label": str, "schools": [{school, clinic_name, city, state, url}], "note": str}.
    Deduplicated by school. Never raises on an empty result — returns an empty roster.
    """
    prompt = _build_prompt(mode, state, conference, anchor_school, radius_miles)
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=8192,
        tools=[_ROSTER_TOOL],
        tool_choice={"type": "tool", "name": "submit_student_health_roster"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_student_health_roster":
            d = block.input if isinstance(block.input, dict) else json.loads(block.input)
            return _clean(d)
    return {"group_label": "", "schools": [], "note": "No roster returned."}


# ── Per-clinic scoring on the Student Health rubric ───────────────────────────

_SCORE_TOOL = {
    "name": "submit_clinic_scores",
    "description": "Submit the AI-visibility pillar scores for one student health clinic.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findability_identity": {"type": "integer", "description": "0-100: how reliably AI assistants surface this clinic with the CORRECT name and clear affiliation to its university when a student asks about campus healthcare."},
            "services_access":      {"type": "integer", "description": "0-100: how accurately AI knows the clinic's services (primary/urgent care, pharmacy, labs, immunizations, sports medicine, telehealth) and how students access it (hours, walk-in vs appointment, insurance/cost)."},
            "reviews_reputation":   {"type": "integer", "description": "0-100: strength, volume, and sentiment of student reviews/ratings AI can cite for this clinic."},
            "machine_readability":  {"type": "integer", "description": "0-100: how structured and crawlable the clinic's digital presence is — clear website, patient portal, online scheduling, consistent name/address/phone."},
            "ai_says":              {"type": "string",  "description": "1-2 sentences on what AI assistants currently say about this clinic."},
        },
        "required": ["findability_identity", "services_access", "reviews_reputation",
                     "machine_readability", "ai_says"],
    },
}

_SCORE_SYSTEM = (
    "You assess how the leading AI assistants (ChatGPT, Gemini, Claude) currently perceive a "
    "specific on-campus student health/medical center when a student asks about campus healthcare. "
    "Score four pillars 0-100 based on how a typical AI assistant would describe and recommend this "
    "clinic TODAY, from its real public/digital footprint — not its aspirations. Score conservatively "
    "and comparably across clinics: a generic, hard-to-find clinic with thin online information should "
    "score low; a well-known clinic with a clear website, patient portal, online scheduling, and solid "
    "student reviews should score high."
)


def score_clinic(clinic: dict) -> dict:
    """Score one student health clinic on the four-pillar Student Health rubric.

    Returns {pulse_score, tiers, ai_says, quartile, band_label}. pulse_score is
    None if scoring could not be produced."""
    from . import scoring
    name = (clinic.get("clinic_name") or clinic.get("school") or "").strip()
    school = (clinic.get("school") or "").strip()
    loc = ", ".join([p for p in [clinic.get("city", ""), clinic.get("state", "")] if p])
    url = (clinic.get("url") or "").strip()
    prompt = (f"Clinic: {name}\nUniversity: {school}\nLocation: {loc}\n"
              + (f"Website: {url}\n" if url else "")
              + "Score how AI assistants currently perceive this student health center on the four pillars.")
    resp = client.messages.create(
        model=_MODEL, max_tokens=2048, tools=[_SCORE_TOOL],
        tool_choice={"type": "tool", "name": "submit_clinic_scores"},
        system=_SCORE_SYSTEM, messages=[{"role": "user", "content": prompt}],
    )
    d: dict = {}
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_clinic_scores":
            d = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    def _cl(v):
        try:
            return max(0, min(100, int(v)))
        except (TypeError, ValueError):
            return None

    tiers = {
        "clinical_outcomes_safety":   _cl(d.get("services_access")),      # Services & Access
        "credentials_recognition":    _cl(d.get("findability_identity")), # Findability & Identity
        "patient_experience_reviews": _cl(d.get("reviews_reputation")),   # Reviews & Reputation
        "access_fit":                 _cl(d.get("machine_readability")),  # Machine-Readability
    }
    score = scoring.composite_score(tiers, "practice_student_health")
    q_code, band = scoring.grade_from_score(score)
    return {"pulse_score": score, "tiers": tiers, "quartile": q_code,
            "band_label": band, "ai_says": (d.get("ai_says") or "").strip()}


def _clean(d: dict) -> dict:
    schools, seen = [], set()
    for s in d.get("schools", []):
        school = (s.get("school") or "").strip()
        clinic = (s.get("clinic_name") or "").strip()
        key = (school or clinic).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        schools.append({
            "school":      school,
            "clinic_name": clinic or school,
            "city":        (s.get("city") or "").strip(),
            "state":       (s.get("state") or "").strip().upper()[:2],
            "url":         (s.get("url") or "").strip(),
        })
    return {
        "group_label": (d.get("group_label") or "").strip(),
        "schools": schools,
        "note": (d.get("note") or "").strip(),
    }
