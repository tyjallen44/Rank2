"""Verified facts for the content drafts (cleanup 3.1 / 3.2 / 3.3).

The drafting model may only use what it is given, so everything the report already verified
is handed over: the organization name (never the analyzed office), every confirmed location
with the address and phone read from its Google Business Profile, every physician found in
the NPI registry with NPI and bio page, and the schema.org medicalSpecialty values that are
valid for the specialty. A value we did not verify stays a [VERIFY: …] placeholder."""
from __future__ import annotations

import re
from typing import Optional

MAX_LOCATIONS = 40
MAX_PHYSICIANS = 80


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def build_facts(result, snap: Optional[dict] = None, base: Optional[dict] = None) -> dict:
    """{organization, analyzed_listing, website_urls, specialty, schema_specialties, locations,
    physicians, wikidata_qid, wikipedia_article, …}. Fail-soft on any shape: a missing block is
    simply absent."""
    facts = dict(base or {})
    snap = snap or {}
    if "website_urls" not in facts:
        facts["website_urls"] = snap.get("website_urls", [])
    for k in ("wikidata_qid", "wikipedia_article"):
        facts.setdefault(k, snap.get(k))
    if result is None:
        return facts
    try:
        from .data.places import canonical_url
        facts["website_urls"] = [canonical_url(u) for u in (facts.get("website_urls") or []) if u]
    except Exception:
        pass
    org = _s(getattr(result, "report_title", None)) or _s(getattr(result, "entity_name", None))
    facts["organization"] = org
    anchor_name = _s(getattr(result, "entity_name", None))
    facts.setdefault("specialty", getattr(result, "specialty", None))
    p = next(iter(getattr(result, "rankings", None) or []), None)
    profile = _s(getattr(p, "weighting_profile", None)) if p is not None else ""
    try:
        from .specialties import schema_specialties
        facts["schema_specialties"] = schema_specialties(facts.get("specialty"), surgical=profile.endswith("procedural"))
    except Exception:
        facts["schema_specialties"] = []
    # Locations: composite rows (name, address) enriched by the profile audit (phone, website).
    profiles = [x for x in ((getattr(result, "profile_audit", None) or {}).get("profiles") or []) if x.get("found")]
    audit = {x.get("place_id"): x for x in profiles if x.get("place_id")}
    audit_by_name = {_s(x.get("name")).lower(): x for x in profiles if _s(x.get("name"))}
    locs, seen = [], set()
    for cr in (getattr(result, "practice_composite_rows", None) or []):
        if cr.get("not_established"):
            continue
        a = audit.get(cr.get("place_id")) or audit_by_name.get(_s(cr.get("practice_name")).lower()) or {}
        row = {"name": _s(cr.get("practice_name")), "address": _s(a.get("address") or cr.get("address")),
               "phone": _s(a.get("phone") or cr.get("phone")), "website": _s(a.get("website")),
               "maps_url": _s(cr.get("google_url")), "is_anchor": bool(cr.get("is_anchor"))}
        if row["name"] and row["address"]:
            locs.append(row)
            seen.add(a.get("place_id") or row["name"].lower())
            if row["is_anchor"]:
                facts["analyzed_listing"] = {"name": row["name"], "address": row["address"]}
    for a in profiles:                       # audited profiles the composite did not carry (older runs)
        key = a.get("place_id") or _s(a.get("name")).lower()
        if key in seen or not _s(a.get("address")) or not _s(a.get("name")):
            continue
        seen.add(key)
        locs.append({"name": _s(a.get("name")), "address": _s(a.get("address")), "phone": _s(a.get("phone")),
                     "website": _s(a.get("website")), "maps_url": "", "is_anchor": bool(a.get("is_anchor"))})
    facts["locations"] = locs[:MAX_LOCATIONS]
    facts["locations_total"] = len(locs)
    # Physicians: registry-verified rows with NPI and bio page.
    phys = []
    for r in ((getattr(result, "physician_facts", None) or {}).get("rows") or []):
        if r.get("registry") != "found" or not r.get("npi"):
            continue
        phys.append({"name": _s(r.get("name")), "credential": _s(r.get("credential")), "npi": _s(r.get("npi")),
                     "bio_url": _s(r.get("bio_url")), "specialty": _s(r.get("specialty"))})
    facts["physicians"] = phys[:MAX_PHYSICIANS]
    facts["physicians_total"] = len(phys)
    if anchor_name and org and anchor_name.lower() != org.lower():
        facts.setdefault("analyzed_listing", {"name": anchor_name, "address": ""})
    return facts


def facts_lines(entity_name: str, location: str, entity_kind: str, facts: dict) -> list[str]:
    """The FACTS block the drafting model sees."""
    org = _s(facts.get("organization")) or entity_name
    lines = [f"- Organization name (use this for every organization-level artifact — Organization schema, Wikidata, "
             f"Wikipedia, llms.txt): {org}",
             f"- Location: {location}",
             f"- Type: {'specialty practice' if entity_kind == 'practice' else 'hospital / health system'}"]
    al = facts.get("analyzed_listing") or {}
    if al.get("name") and al["name"].lower() != org.lower():
        lines.append(f"- Analyzed Google listing (ONE office of the organization, never the organization's name): {al['name']}"
                     + (f" — {al['address']}" if al.get("address") else ""))
    for k, label in [("website_urls", "Website URL(s)"), ("specialty", "Specialty"),
                     ("wikidata_qid", "Wikidata QID"), ("wikipedia_article", "Wikipedia article")]:
        v = facts.get(k)
        if v:
            lines.append(f"- {label}: {v if not isinstance(v, list) else ', '.join(v)}")
    ss = facts.get("schema_specialties") or []
    if ss:
        lines.append(f"- schema.org medicalSpecialty values valid for this specialty (use ONLY these): {', '.join(ss)}")
    else:
        lines.append("- schema.org medicalSpecialty: no valid enumeration value for this specialty — omit the property.")
    locs = facts.get("locations") or []
    if locs:
        lines.append(f"- LOCATIONS (verified from Google Business Profiles; {facts.get('locations_total', len(locs))} total"
                     + (f", first {len(locs)} listed" if facts.get("locations_total", len(locs)) > len(locs) else "")
                     + "). Each location keeps its OWN name — never rename every profile to one location's name:")
        for l in locs:
            bits = [l["name"], l["address"]]
            if l.get("phone"):
                bits.append(f"phone {l['phone']}")
            if l.get("website"):
                bits.append(f"website {l['website']}")
            lines.append("    • " + " | ".join(bits))
    phys = facts.get("physicians") or []
    if phys:
        lines.append(f"- PHYSICIANS (verified in the NPI registry; {facts.get('physicians_total', len(phys))} total"
                     + (f", first {len(phys)} listed" if facts.get("physicians_total", len(phys)) > len(phys) else "")
                     + "). For Physician schema write one filled block per physician below, not a template:")
        for ph in phys:
            bits = [ph["name"] + (f", {ph['credential']}" if ph.get("credential") else ""), f"NPI {ph['npi']}"]
            if ph.get("bio_url"):
                bits.append(f"bio {ph['bio_url']}")
            if ph.get("specialty"):
                bits.append(ph["specialty"])
            lines.append("    • " + " | ".join(bits))
    return lines


_VERIFY = re.compile(r"\[VERIFY[^\]]*\]", re.I)
_SPEC_VALUE = re.compile(r'("medicalSpecialty"\s*:\s*)("([^"]*)"|\[[^\]]*\])')
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_NA_LINE = re.compile(r"^\s*(?:[-•*]\s*)?P\d+\b[^\n]*(?:\bN/?A\b|not applicable|no value|none)[^\n]*$", re.I | re.M)


def placeholder_count(draft: str) -> int:
    return len(_VERIFY.findall(draft or ""))


def fix_draft(draft: str, schema_specialties: list[str], remediation_type: str = "") -> str:
    """Deterministic clean-up of a drafted block (3.3 / 3.4 / 3.5): medicalSpecialty values are
    replaced by the valid list (or the property removed when there is none); tracking
    parameters are stripped from every URL; Wikidata 'P… — N/A' lines are dropped."""
    if not draft:
        return draft
    from .specialties import SCHEMA_MEDICAL_SPECIALTIES
    from .data.places import canonical_url
    out = draft

    def _spec(m):
        raw = m.group(2)
        vals = re.findall(r'"([^"]*)"', raw)
        if vals and all(v in SCHEMA_MEDICAL_SPECIALTIES for v in vals):
            return m.group(0)
        if not schema_specialties:
            return m.group(1) + "null"
        return m.group(1) + ('"' + schema_specialties[0] + '"' if len(schema_specialties) == 1
                             else "[" + ", ".join(f'"{v}"' for v in schema_specialties) + "]")
    out = _SPEC_VALUE.sub(_spec, out)
    out = re.sub(r',?\s*"medicalSpecialty"\s*:\s*null', "", out)     # property removed, not invented
    out = _URL.sub(lambda m: canonical_url(m.group(0)) or m.group(0), out)
    if remediation_type == "wikidata_edit":
        out = _NA_LINE.sub("", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out
