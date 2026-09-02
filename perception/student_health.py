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
            "anchor_city": {"type": "string", "description": "For a radius grouping only: the city where the center/anchor school is located (e.g. 'Provo'). Empty for state/conference."},
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
                f"miles of {anchor_school}. Include {anchor_school} itself. Also set anchor_city to the "
                f"city where {anchor_school} is located. For each university, identify its primary "
                f"on-campus student medical/health center.")
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


def _clinic_key(clinic: dict) -> str:
    import re
    s = " ".join(p for p in (clinic.get("school", ""), clinic.get("clinic_name", ""),
                             clinic.get("city", ""), clinic.get("state", "")) if p).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def score_clinic(clinic: dict, override: bool = False) -> dict:
    """Score one student health clinic on the four-pillar Student Health rubric.

    Returns {pulse_score, tiers, ai_says, quartile, band_label, ...}. Reuses a
    cached score within 30 days unless override=True. pulse_score is None if
    scoring could not be produced."""
    from . import scoring
    from .db import get_cached_clinic_score, upsert_cached_clinic_score
    _key = _clinic_key(clinic)
    if not override and _key:
        cached = get_cached_clinic_score(_key, days=30)
        if cached and cached.get("pulse_score") is not None:
            return cached
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

    # Reviews & Reputation: prefer a VERIFIED live Google read (real rating +
    # volume) over the model's estimate — mirrors the hospital rubric. Falls back
    # to the estimate when no listing is confidently matched.
    reviews_source = "estimate"
    g_rating = g_count = None
    reviews_val = _cl(d.get("reviews_reputation"))
    gr = _google_reviews_for_clinic(clinic)
    if gr is not None:
        rating, count = gr
        band = scoring.experience_band(rating, count)
        if band is not None:
            reviews_val, reviews_source = band, "google"
            g_rating, g_count = rating, count

    tiers = {
        "clinical_outcomes_safety":   _cl(d.get("services_access")),      # Services & Access
        "credentials_recognition":    _cl(d.get("findability_identity")), # Findability & Identity
        "patient_experience_reviews": reviews_val,                        # Reviews & Reputation
        "access_fit":                 _cl(d.get("machine_readability")),  # Machine-Readability
    }
    score = scoring.composite_score(tiers, "practice_student_health")
    q_code, band = scoring.grade_from_score(score)
    result = {"pulse_score": score, "tiers": tiers, "quartile": q_code,
              "band_label": band, "ai_says": (d.get("ai_says") or "").strip(),
              "reviews_source": reviews_source, "google_rating": g_rating,
              "google_review_count": g_count}
    if _key and score is not None:
        try:
            import json as _json
            upsert_cached_clinic_score(_key, _json.dumps(result))
        except Exception:
            pass
    return result


_CLINIC_WORDS = ("health", "clinic", "wellness", "medical", "student")


def _google_reviews_for_clinic(clinic: dict):
    """Resolve a campus clinic's Google rating → (rating, review_count) or None.

    Unlike the single-provider read (which only inspects the top result), this
    SCANS all candidates and picks the best clinic-like listing. That matters for
    dominant campuses: a search for 'BYU Student Health Center' returns the
    university itself as result #1, so a top-result-only read misses the clinic
    (ranked #2/#3). We keep only rated listings whose name looks like a clinic
    (not the university) and that plausibly match the requested clinic."""
    import httpx
    from .data import places as _pl
    key = _pl._api_key()
    if not key:
        return None
    name = (clinic.get("clinic_name") or "").strip()
    school = (clinic.get("school") or "").strip()
    city = (clinic.get("city") or "").strip()
    state = (clinic.get("state") or "").strip()
    loc = " ".join(p for p in (city, state) if p)
    # Several phrasings — campus clinics often have duplicate listings where one
    # is unrated; the rated one surfaces under a "reviews"/"health center" phrasing
    # (e.g. BYU's rated "(SHC)" listing). We collect across all variants.
    seen, queries = set(), []
    no_student = name.replace("Student ", "").replace("student ", "").strip()
    for q in (f"{name} {loc}", f"{name} reviews {loc}", f"{name} reviews",
              f"{school} student health center {loc}",
              (f"{no_student} {loc}" if no_student and no_student != name else "")):
        q = " ".join(q.split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)

    match_targets = [t for t in (name, f"{school} student health center", f"{school} {name}") if t.strip()]

    best = None   # (rank, rating, count) — global best across every variant
    for q in queries:
        try:
            resp = httpx.post(
                _pl._SEARCH_TEXT,
                headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                         "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount"},
                json={"textQuery": q}, timeout=20.0,
            )
            resp.raise_for_status()
            cands = resp.json().get("places", [])
        except Exception:
            continue
        for pl_c in cands:
            fn = (pl_c.get("displayName") or {}).get("text", "")
            rating = pl_c.get("rating")
            if rating is None or not fn:               # skip unrated (incl. the unrated duplicate)
                continue
            low = fn.lower()
            if not any(w in low for w in _CLINIC_WORDS):   # skip the university / non-clinic listings
                continue
            m = "none"
            for tgt in match_targets:
                mm = _pl._name_match(tgt, fn)
                if mm != "none":
                    m = "strong" if (mm == "strong" or m == "strong") else "weak"
            if m == "none":
                continue
            count = pl_c.get("userRatingCount") or 0
            rank = (2 if m == "strong" else 1, count)
            if best is None or rank > best[0]:
                best = (rank, float(rating), int(count))
    return (best[1], best[2]) if best is not None else None


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
        "anchor_city": (d.get("anchor_city") or "").strip(),
        "note": (d.get("note") or "").strip(),
    }
