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
