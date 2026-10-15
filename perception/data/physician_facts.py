"""Physician facts verified from the NPI registry and the practice's own physician pages
(heart-surgery item 3, increment 2).

Replaces two model estimates that drive the practice score:
  • linkage_integrity_pct  — share of the practice's physicians whose NPPES LOCATION postal
                             code matches one of the practice's confirmed locations
  • board_cert_unverifiable — True when NO sampled physician has a board-certification
                             statement on a crawlable page of the practice's own site

Physicians come from the confirmed physician roster when one exists, otherwise from the
NPPES organization cascade already used for the physician composite. Anything the registry
cannot find or the site does not state is reported as unverified, never guessed.
"""
from __future__ import annotations

import re
from typing import Optional

_CERT_RE = re.compile(
    r"\b(board[- ]certified|board certification|diplomate of the american board|fellow of the american (?:academy|college|board)|"
    r"american board of [a-z ]+|abms|abos|abim|abfm|abp\b|abog|abd\b|abns|abu\b|abem)\b", re.I)
_SAMPLE = 12


def _zip5(v: str) -> str:
    return re.sub(r"[^0-9]", "", v or "")[:5]


def _nppes_lookup_physician(name: str, state: str) -> list:
    """NPI-1 records for an individual by name in a state (fail-soft)."""
    from ..physician_discovery import _nppes_get, _is_physician
    parts = [p for p in re.sub(r"[^A-Za-z \-']", " ", name or "").split() if p and not re.fullmatch(r"(dr|md|do|mr|ms|jr|sr|ii|iii)\.?", p, re.I)]
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    recs = _nppes_get({"enumeration_type": "NPI-1", "first_name": first, "last_name": last, "state": (state or "").upper()[:2], "limit": 10})
    return [r for r in recs if _is_physician(r)]


def _nppes_by_number(npi: str) -> list:
    """The NPI-1 record for a known number (fail-soft)."""
    from ..physician_discovery import _nppes_get
    try:
        return [r for r in _nppes_get({"number": str(npi), "limit": 1}) if str(r.get("number")) == str(npi)]
    except Exception:
        return []


def _registry_zips(rec: dict) -> set:
    """Every postal code the registry holds for the physician: LOCATION and MAILING addresses plus
    the secondary practice locations (NPPES `practiceLocations`)."""
    zips = {_zip5(a.get("postal_code") or "") for a in (rec.get("addresses") or []) if a.get("address_purpose") in ("LOCATION", "MAILING")}
    zips |= {_zip5(a.get("postal_code") or "") for a in (rec.get("practiceLocations") or [])}
    return {z for z in zips if len(z) == 5}


def _org_registry_zips(org_name: Optional[str], state: str) -> set:
    """Postal codes of the organization's own NPI-2 LOCATION addresses (fail-soft)."""
    if not (org_name or "").strip():
        return set()
    try:
        from ..listing_quality import nppes_org_addresses
        return {a["zip"] for a in nppes_org_addresses(org_name, state) if len(a.get("zip") or "") == 5}
    except Exception:
        return set()


def verify_physicians(physicians: list, roster_locations: list, state: str, site_pages: list, *, sample: int = _SAMPLE,
                      site_url: Optional[str] = None, org_name: Optional[str] = None) -> dict:
    """physicians: [{name, npi?}], roster_locations: [{address?, city?, state?, zip?}], site_pages: [{url, text}].
    When site_url is given, the sampled physicians' own bio pages are fetched by name from the site's
    provider directory and added to site_pages before the certification check.
    Returns {status, checked, linked, linkage_pct, cert_stated, cert_checked, board_cert_unverifiable, rows}."""
    if not physicians:
        return {"status": "skipped", "note": "no physician roster", "rows": []}
    site_pages = list(site_pages or [])
    bio_urls = []
    if site_url:
        try:
            from .website_facts import _fetch_bio_pages
            bios = _fetch_bio_pages(site_url, [p.get("url") for p in site_pages if p.get("url")],
                                    physician_names=[p.get("name") for p in physicians[:sample]], max_bios=sample + 4)
            site_pages += bios
            bio_urls = [b["url"] for b in bios]
        except Exception:
            pass
    roster_zips = set()
    for loc in roster_locations or []:
        z = _zip5(loc.get("zip") or "")
        if len(z) != 5:
            # the last five-digit group in the address ("…, Cypress, TX 77433, USA"), never the street number
            zs = re.findall(r"\b(\d{5})(?:-\d{4})?\b", loc.get("address") or "")
            z = zs[-1] if zs else ""
        if len(z) == 5:
            roster_zips.add(z)
    # The organization's own registry addresses (NPI-2) are practice locations too — large groups
    # register physicians at an administrative site the website never lists.
    org_zips = _org_registry_zips(org_name, state)
    roster_zips |= org_zips
    n_loc = len([l for l in (roster_locations or []) if (l.get("address") or l.get("zip"))])
    thin_roster = len(roster_zips) < min(3, max(1, n_loc))
    page_text = " ".join((p.get("text") or "") for p in (site_pages or []))
    rows = []
    for ph in physicians[:sample]:
        name = (ph.get("name") or "").strip()
        if not name:
            continue
        row = {"name": name, "npi": ph.get("npi"), "registry": "not found", "linked": None, "cert_stated": None}
        for k in ("sources", "website_missing", "npi_missing", "bio_url", "credential", "specialty"):
            if ph.get(k) not in (None, "", []):
                row[k] = ph[k]
        try:
            if ph.get("npi"):
                # A known NPI is looked up by number; it never falls back to a same-name stranger.
                recs = _nppes_by_number(ph["npi"]) or [r for r in _nppes_lookup_physician(name, state) if str(r.get("number")) == str(ph["npi"])]
            else:
                recs = _nppes_lookup_physician(name, state)
            if len(recs) == 1:
                rec = recs[0]
                row["npi"] = row["npi"] or rec.get("number")
                row["registry"] = "found"
                zips = _registry_zips(rec)
                row["registry_zip"] = next(iter(sorted(zips)), None)
                row["registry_zips"] = sorted(zips)
                row["linked"] = bool(zips & roster_zips) if (roster_zips and not thin_roster) else None
            elif len(recs) > 1:
                row["registry"] = "ambiguous"
        except Exception:
            row["registry"] = "error"
        # certification statement on the practice's own pages, near the physician's last name
        last = name.split()[-1]
        if page_text and last:
            # Only the sentence run around each mention counts (≈250 chars), so one certified
            # colleague's statement on a shared directory page is not credited to a neighbour.
            near = [m.start() for m in re.finditer(re.escape(last), page_text, re.I)]
            # A physician whose name never appears on the pages we read is UNKNOWN, not "no statement":
            # the bio page was not read (directory coverage), so nothing can be said about it.
            row["cert_stated"] = any(_CERT_RE.search(page_text[max(0, i - 250): i + 250]) for i in near[:20]) if near else None
        rows.append(row)
    # Central registration: when most found physicians share one registry ZIP that is not a practice
    # location (a billing / hospital address), the registry cannot say where they practise.
    found = [r for r in rows if r["registry"] == "found" and r.get("registry_zips")]
    central = None
    if len(found) >= 5:
        from collections import Counter
        cz = Counter(z for r in found for z in r["registry_zips"] if z not in roster_zips)
        if cz:
            top, n = cz.most_common(1)[0]
            if n >= 0.8 * len(found) and not any(z in roster_zips for r in found for z in r["registry_zips"] if r.get("linked")):
                central = top
    if central or thin_roster:
        for r in rows:
            r["linked"] = None
    checked = [r for r in rows if r["linked"] is not None]
    linked = sum(1 for r in checked if r["linked"])
    cert_checked = [r for r in rows if r["cert_stated"] is not None]
    cert_stated = sum(1 for r in cert_checked if r["cert_stated"])
    return {
        "status": "measured" if rows else "skipped",
        "checked": len(checked), "linked": linked,
        "linkage_pct": round(100 * linked / len(checked)) if checked else None,
        "cert_checked": len(cert_checked), "cert_stated": cert_stated,
        "board_cert_unverifiable": (cert_stated == 0) if cert_checked else None,
        "registry_found": sum(1 for r in rows if r["registry"] == "found"),
        "bio_pages_read": len(bio_urls),
        "roster_zips": sorted(roster_zips), "org_zips": sorted(org_zips),
        "linkage_note": ("registry addresses cluster on one address that is not a practice location (central registration)" if central
                         else ("too few practice locations with a ZIP to measure linkage" if thin_roster else None)),
        "cert_unread": sum(1 for r in rows if r["cert_stated"] is None),
        "rows": rows,
    }


def evidence_lines(f: dict) -> str:
    if not f or f.get("status") != "measured":
        return ""
    lines = ["", "=== Physician facts (verified from the NPI registry and the practice's own pages — USE VERBATIM) ==="]
    if f.get("linkage_pct") is not None:
        lines.append(f"Physician↔practice linkage (NPPES location matches a confirmed practice location): {f['linked']} of {f['checked']} = {f['linkage_pct']}%. "
                     "Set linkage_integrity_pct to this value.")
    else:
        lines.append("Physician↔practice linkage: NOT MEASURABLE (" + (f.get("linkage_note") or "registry matches ambiguous or no location zips")
                     + "). Do not treat this as a linkage gap; estimate linkage_integrity_pct from naming/identity evidence as usual.")
    n_rows = len(f.get("rows") or [])
    if f.get("board_cert_unverifiable") is not None:
        lines.append(f"Board certification stated on the pages of the practice's own site that we read: {f['cert_stated']} of {f['cert_checked']} physicians whose pages were read "
                     f"({f.get('bio_pages_read', 0)} bio pages read). Treat as supporting evidence only — our read of bio pages is partial; "
                     "judge board_cert_unverifiable from all sources as usual.")
    unread = f.get("cert_unread") or 0
    if unread:
        lines.append(f"MEASUREMENT LIMIT: {unread} of {n_rows} physicians' pages were NOT read (directory coverage), so nothing is known about their "
                     "certification statements — do NOT count them as unverifiable and do NOT present this as a practice weakness.")
    miss = [r["name"] for r in f.get("rows") or [] if r.get("cert_stated") is False][:8]
    if miss:
        lines.append("Physicians whose own page was read and carries no certification statement: " + ", ".join(miss) + ".")
    return "\n".join(lines) + "\n"


def summary(f: dict) -> Optional[str]:
    if not f or f.get("status") != "measured":
        return None
    parts = []
    if f.get("linkage_pct") is not None:
        parts.append(f"registry linkage {f['linkage_pct']}% ({f['linked']} of {f['checked']})")
    if f.get("board_cert_unverifiable") is not None:
        parts.append(f"certification stated for {f['cert_stated']} of {f['cert_checked']} physicians")
    return "physician facts verified from NPI registry + site" + (": " + "; ".join(parts) if parts else "")
