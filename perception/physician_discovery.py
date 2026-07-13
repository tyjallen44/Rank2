"""Discover physicians associated with a practice entity."""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable, Optional

import requests as _requests

from .analyzer import _get_client, _MODEL, _clean

_NPPES_API = "https://npiregistry.cms.hhs.gov/api/"

# NPPES taxonomy code prefixes for physician output (MDs and DOs only).
# PAs, NPs, CRNAs, and CNS are excluded from the report roster.
_PHYSICIAN_TAX: tuple[str, ...] = (
    "207",  # Allopathic physicians (all specialties)
    "208",  # Osteopathic physicians
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
        r = _requests.get(_NPPES_API, params={"version": "2.1", **params}, timeout=15)
        r.raise_for_status()
        return r.json().get("results") or []
    except Exception:
        return []


def _is_physician(rec: dict) -> bool:
    """Return True if the NPI-1 record is an MD or DO."""
    for t in rec.get("taxonomies") or []:
        code = t.get("code", "")
        if any(code.startswith(p) for p in _PHYSICIAN_TAX):
            return True
    return False


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

    if not org_zips:
        return []

    # All search variants: input names + names found in NPI-2 records
    all_variants: list[str] = list(dict.fromkeys(input_variants + extra_variants))

    # Phase 2: NPI-1 seed at each org location postal
    seed: dict[str, dict] = {}
    for postal in org_zips:
        for rec in _nppes_get({"enumeration_type": "NPI-1",
                               "postal_code": postal, "limit": 200}):
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

    # Validate each candidate: only cascade if the org appears in NPI-2 there
    cascade_zips: set[str] = set()
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
        for rec in _nppes_get({"enumeration_type": "NPI-1",
                               "postal_code": postal, "limit": 200}):
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
