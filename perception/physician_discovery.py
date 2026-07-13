"""Discover physicians associated with a practice entity."""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable, Optional

import httpx as _httpx

from .analyzer import _get_client, _MODEL, _clean
from .data import places as _places

_NPPES_API = "https://npiregistry.cms.hhs.gov/api/"

# NPPES taxonomy code prefixes for physician output.
_PHYSICIAN_TAX: tuple[str, ...] = ("207", "208")   # Allopathic / Osteopathic physicians

# Taxonomy prefixes that disqualify a record even if it also has a 207/208 code.
# Some PAs/NPs self-register a physician taxonomy alongside their actual PA/NP
# taxonomy — these must be excluded.
_MIDLEVEL_TAX: tuple[str, ...] = (
    "363",  # Physician Assistants and Nurse Practitioners
    "364",  # Clinical Nurse Specialists
    "367",  # Advanced Practice Midwives / Anesthesiology Assistants
    "374",  # CRNAs / DNPs
)

_PHYSICIAN_DISCOVER_TOOL = {
    "name": "submit_physician_roster",
    "description": (
        "Submit the roster of physicians associated with this practice. "
        "Only include individual physicians you can corroborate via NPI registry "
        "or official provider directory listings."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "physicians": {
                "type": "array",
                "description": "Physicians affiliated with this practice.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":       {"type": "string"},
                        "npi":        {"type": ["string", "null"]},
                        "specialty":  {"type": ["string", "null"]},
                        "credential": {"type": ["string", "null"]},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["physicians"],
        "additionalProperties": False,
    },
}


# ── NPPES helpers ─────────────────────────────────────────────────────────────

def _nppes_get(params: dict) -> list[dict]:
    try:
        r = _httpx.get(_NPPES_API, params={"version": "2.1", **params}, timeout=15)
        r.raise_for_status()
        return r.json().get("results") or []
    except Exception:
        return []


def _is_physician(rec: dict) -> bool:
    """Return True if the NPI-1 record is an MD or DO.

    Excluded: any record that carries a mid-level (PA/NP/CRNA/CNS) taxonomy
    code, even when it also carries a 207/208 code — some providers
    self-register dual taxonomies but are not physicians.
    """
    has_physician = False
    for t in rec.get("taxonomies") or []:
        code = t.get("code", "")
        if any(code.startswith(p) for p in _MIDLEVEL_TAX):
            return False
        if any(code.startswith(p) for p in _PHYSICIAN_TAX):
            has_physician = True
    return has_physician


def _parse_npi1(rec: dict) -> dict | None:
    basic = rec.get("basic", {})
    if basic.get("status") != "A":
        return None
    first = (basic.get("first_name") or "").strip()
    last  = (basic.get("last_name")  or "").strip()
    name  = f"{first} {last}".strip()
    if not name:
        return None
    raw_cred   = basic.get("credential") or ""
    credential = raw_cred.replace(".", "").replace(" ", "").upper() or None
    taxonomies = rec.get("taxonomies") or []
    primary    = (
        next((t for t in taxonomies if t.get("primary")), None)
        or (taxonomies[0] if taxonomies else {})
    )
    return {
        "name":       name,
        "npi":        rec.get("number") or None,
        "credential": credential,
        "specialty":  primary.get("desc") or None,
    }


def _primary_postal(rec: dict) -> str:
    """Return the LOCATION postal code for a NPI-1 record, or ''."""
    loc = next((a for a in rec.get("addresses", [])
                if a.get("address_purpose") == "LOCATION"), None)
    return (loc.get("postal_code") or "").strip() if loc else ""


# Maps lowercase keywords in org names to NPPES taxonomy_description search terms.
_SPECIALTY_KEYWORDS: list[tuple[str, str]] = [
    ("orthopaed",       "Orthopaedic"),
    ("orthoped",        "Orthopaedic"),
    ("cardio",          "Cardiology"),
    ("cardiac",         "Cardiology"),
    ("heart",           "Cardiology"),
    ("neurol",          "Neurology"),
    ("dermatol",        "Dermatology"),
    ("ophthalmol",      "Ophthalmology"),
    ("oncol",           "Oncology"),
    ("gastro",          "Gastroenterology"),
    ("urol",            "Urology"),
    ("rheumatol",       "Rheumatology"),
    ("endocrin",        "Endocrinology"),
    ("pulmon",          "Pulmonary"),
    ("nephrol",         "Nephrology"),
    ("gynecol",         "Gynecology"),
    ("obstet",          "Obstetrics"),
    ("pediatr",         "Pediatrics"),
    ("psychiatr",       "Psychiatry"),
    ("pain",            "Pain"),
    ("spine",           "Spine"),
    ("vascular",        "Vascular"),
    ("plastics",        "Plastic"),
    ("plastic",         "Plastic"),
    ("ear",             "Otolaryngology"),
    ("ent ",            "Otolaryngology"),
    ("otolaryngol",     "Otolaryngology"),
    # Short prefix — must come after the longer "orthopaed"/"orthoped" entries.
    ("ortho",           "Orthopaedic"),
]


def _specialty_tax_term(org_name: str) -> str | None:
    """Return an NPPES taxonomy_description search term inferred from the org name."""
    lower = org_name.lower()
    for keyword, tax in _SPECIALTY_KEYWORDS:
        if keyword in lower:
            return tax
    return None


# ── Main NPPES lookup ─────────────────────────────────────────────────────────

def _nppes_lookup(org_name: str, city: str, state: str) -> list[dict]:
    """Cascading NPPES lookup for multi-site specialty practices.

    Phase 1 – NPI-2 seed: Search org by name in the home state to collect
      clinic location postal codes.  Extracts org name variants (legal name +
      DBA) from each NPI-2 record to use in cascade validation.

    Phase 2 – NPI-1 seed: Search individual providers at each NPI-2 location
      postal.  NPPES matches postal_code against mailing addresses too, so
      physicians with secondary practice sites appear even when their primary
      address is elsewhere.

    Phase 3 – Validated cascade: Identify candidate cascade zips from
      physician-only (MD/DO) seed providers' primary addresses.  Validate
      each candidate by confirming the org name appears in NPI-2 at that zip.
      This filters out adjacent high-density sites (hospitals, other group
      practices) that share a geographic area but are not the target org.

    Phase 4 – Filter: Keep only clinical providers (physicians, NPs, PAs,
      CRNAs, CNS) by checking NPPES taxonomy code prefixes.
    """
    spaced   = re.sub(r"([a-z])([A-Z])", r"\1 \2", org_name)
    input_variants: list[str] = list(dict.fromkeys([org_name, spaced]))

    # Specialty taxonomy inferred from org name — applied as a Phase 2 filter
    # to exclude co-located non-specialty providers sharing the same building.
    org_tax_term: str | None = _specialty_tax_term(org_name)

    # Phase 1: NPI-2 → org postal codes + derive name variants for validation
    org_zips: set[str]          = set()
    seen_npi2_names: set[str]   = set()
    extra_variants: list[str]   = []

    for variant in input_variants:
        for rec in _nppes_get({"enumeration_type": "NPI-2",
                               "organization_name": variant,
                               "state": state, "limit": 50}):
            for addr in rec.get("addresses", []):
                if addr.get("address_purpose") != "LOCATION":
                    continue
                postal = (addr.get("postal_code") or "").strip()
                # Only use 9-digit zips — 5-digit scans an entire zip area
                # and pulls in unrelated practices sharing the same zip code.
                if len(postal) >= 9:
                    org_zips.add(postal)

            # Harvest legal name + DBA for cascade validation
            b = rec.get("basic", {})
            legal = (b.get("organization_name") or "").strip().upper()
            if legal and legal not in seen_npi2_names:
                seen_npi2_names.add(legal)
                # Use meaningful words from the legal name (len > 3)
                words = [w for w in re.sub(r"[,.]", "", legal).split() if len(w) > 3]
                if words:
                    extra_variants.append(" ".join(words[:2]))

            for other in rec.get("other_names", []) or []:
                dba = (other.get("organization_name") or "").strip()
                if dba and dba.upper() not in seen_npi2_names:
                    seen_npi2_names.add(dba.upper())
                    extra_variants.append(dba)

    # Phase 1b: practice has no NPI-2 entity — use Google Places + specialty
    # taxonomy to resolve 9-digit postal codes for the practice's building(s).
    #
    # Google Places gives a 5-digit zip; NPPES taxonomy+city search returns
    # all local physicians of that type; clustering their 9-digit primary
    # addresses filtered to the Google Places zip prefix reveals the group's
    # building(s) even when no NPI-2 exists.
    places_fallback = False
    fallback_tax_term: str | None = None
    if not org_zips:
        google_postal = _places.fetch_address_postal(org_name, city, state)
        if google_postal:
            zip5 = google_postal[:5]
            tax_term = org_tax_term
            if tax_term:
                freq: Counter[str] = Counter()
                for rec in _nppes_get({"enumeration_type": "NPI-1",
                                       "taxonomy_description": tax_term,
                                       "city": city.upper(),
                                       "state": state, "limit": 200}):
                    if not _is_physician(rec):
                        continue
                    pz = _primary_postal(rec)
                    if len(pz) >= 9 and pz.startswith(zip5):
                        freq[pz] += 1
                # Group by 8-digit building prefix (same street number,
                # different suite +4).  Accept all 9-digit zips that belong
                # to a building with 5+ physicians — this captures physicians
                # in minority suites (e.g. a single-occupant suite) that
                # share the building with the practice's main cluster.
                prefix_totals: Counter[str] = Counter()
                for pz, cnt in freq.items():
                    prefix_totals[pz[:8]] += cnt
                for pz in freq:
                    if prefix_totals[pz[:8]] >= 5:
                        org_zips.add(pz)
            if org_zips:
                fallback_tax_term = tax_term
            else:
                # Taxonomy gave nothing — add the raw Google postal as last resort
                org_zips.add(google_postal)
            places_fallback = True

    if not org_zips:
        return []

    # All search variants: input names + names found in NPI-2 records
    all_variants: list[str] = list(dict.fromkeys(input_variants + extra_variants))

    # Phase 2: NPI-1 seed at each org location postal.
    # Apply specialty taxonomy filter whenever the org name implies a specialty —
    # this excludes co-located non-specialty providers (urologists, ENTs, etc.)
    # sharing a building with the target practice, regardless of whether org_zips
    # came from NPI-2 records (normal path) or Google Places fallback.
    phase2_tax = fallback_tax_term or org_tax_term
    seed: dict[str, dict] = {}
    for postal in org_zips:
        params: dict = {"enumeration_type": "NPI-1", "postal_code": postal, "limit": 200}
        if phase2_tax:
            params["taxonomy_description"] = phase2_tax
        for rec in _nppes_get(params):
            npi = rec.get("number") or ""
            if npi and npi not in seed:
                seed[npi] = rec

    # Phase 3: physician-only cascade with NPI-2 validation
    # Count how many physician (MD/DO) seeds list each external zip as primary
    physician_zip_freq: Counter[str] = Counter()
    for rec in seed.values():
        if not _is_physician(rec):
            continue
        pz = _primary_postal(rec)
        if len(pz) >= 9 and pz not in org_zips:
            physician_zip_freq[pz] += 1

    # Validate each candidate: only cascade if the org appears in NPI-2 there.
    # Skip cascade entirely when org_zips came from the Google Places fallback
    # (no NPI-2 records exist to validate against).
    cascade_zips: set[str] = set()
    if not places_fallback:
        for pz, cnt in physician_zip_freq.items():
            if cnt < 2:
                continue
            for v in all_variants:
                if _nppes_get({"enumeration_type": "NPI-2",
                               "organization_name": v,
                               "postal_code": pz, "limit": 1}):
                    cascade_zips.add(pz)
                    break

    all_recs: dict[str, dict] = dict(seed)
    for postal in cascade_zips:
        cparams: dict = {"enumeration_type": "NPI-1", "postal_code": postal, "limit": 200}
        if phase2_tax:
            cparams["taxonomy_description"] = phase2_tax
        for rec in _nppes_get(cparams):
            npi = rec.get("number") or ""
            if npi and npi not in all_recs:
                all_recs[npi] = rec

    # Phase 4: filter to physicians (MD/DO) only and parse.
    # Only keep providers who have at least one registered address at a
    # confirmed org location (org_zips or validated cascade_zips).
    confirmed_zips = org_zips | cascade_zips

    physicians: list[dict] = []
    seen_names: set[str]   = set()
    for rec in all_recs.values():
        if not _is_physician(rec):
            continue
        addrs = rec.get("addresses") or []
        if not any((a.get("postal_code") or "").strip() in confirmed_zips
                   for a in addrs):
            continue
        parsed = _parse_npi1(rec)
        if not parsed or parsed["name"] in seen_names:
            continue
        seen_names.add(parsed["name"])
        physicians.append(parsed)

    return physicians


# ── Claude fallback ───────────────────────────────────────────────────────────

def _claude_discover(
    practice_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> list[dict]:
    """Ask Claude to recall provider affiliations from training data."""
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    prompt = (
        f"You are researching the physician roster for '{practice_name}', "
        f"a medical organization based in {city}, {state}.\n\n"
        "Using NPPES NPI registry records and publicly available provider "
        "directory information, list the physicians (MDs, DOs) and advanced "
        "practitioners (NPs, PAs) affiliated with this organization at any of "
        "its locations.\n\n"
        "For each provider include full name (no title prefix), NPI, "
        "primary specialty, and credential (MD, DO, NP, PA, etc.).\n\n"
        "Call submit_physician_roster with your findings. "
        "An empty list is acceptable if you cannot confirm any providers."
    )

    try:
        with _get_client().messages.stream(
            model=_MODEL,
            max_tokens=4000,
            tools=[_PHYSICIAN_DISCOVER_TOOL],
            tool_choice={"type": "tool", "name": "submit_physician_roster"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    except Exception:
        emit({"type": "text",
              "text": f"Physician discovery unavailable for {practice_name}."})
        return []

    roster_data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_physician_roster":
            roster_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    physicians: list[dict] = []
    for ph in roster_data.get("physicians") or []:
        name = _clean(ph.get("name", ""))
        if not name:
            continue
        physicians.append({
            "name":       name,
            "npi":        ph.get("npi") or None,
            "specialty":  ph.get("specialty") or None,
            "credential": ph.get("credential") or None,
        })
    return physicians


# ── Public entry point ────────────────────────────────────────────────────────

def discover_physicians(
    practice_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> list[dict]:
    """Discover clinical providers affiliated with a practice.

    Primary path: cascading NPPES NPI registry lookup (real-time, authoritative).
    Fallback: Claude knowledge recall (used only when NPPES returns nothing).

    Returns list of dicts: {name, npi, specialty, credential}.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    emit({"type": "text",
          "text": f"Looking up {practice_name} providers in NPPES registry…"})
    physicians = _nppes_lookup(practice_name, city, state)

    if physicians:
        emit({"type": "text",
              "text": f"Found {len(physicians)} provider(s) for {practice_name} via NPPES."})
        return physicians

    emit({"type": "text",
          "text": f"NPPES returned no results; asking Claude about {practice_name} physicians…"})
    physicians = _claude_discover(practice_name, city, state, on_event=on_event)
    emit({"type": "text",
          "text": f"Found {len(physicians)} physician(s) for {practice_name} via Claude."})
    return physicians
