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


def verify_physicians(physicians: list, roster_locations: list, state: str, site_pages: list, *, sample: int = _SAMPLE,
                      site_url: Optional[str] = None) -> dict:
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
        z = _zip5(loc.get("zip") or "") or _zip5(re.sub(r".*?(\d{5})(?:-\d{4})?\s*$", r"\1", loc.get("address") or ""))
        if len(z) == 5:
            roster_zips.add(z)
    page_text = " ".join((p.get("text") or "") for p in (site_pages or []))
    rows = []
    for ph in physicians[:sample]:
        name = (ph.get("name") or "").strip()
        if not name:
            continue
        row = {"name": name, "npi": ph.get("npi"), "registry": "not found", "linked": None, "cert_stated": None}
        try:
            recs = _nppes_lookup_physician(name, state)
            if ph.get("npi"):
                recs = [r for r in recs if str(r.get("number")) == str(ph["npi"])] or recs
            if len(recs) == 1 or (recs and ph.get("npi")):
                rec = recs[0]
                row["npi"] = row["npi"] or rec.get("number")
                row["registry"] = "found"
                zips = {_zip5(a.get("postal_code") or "") for a in rec.get("addresses", []) if a.get("address_purpose") in ("LOCATION", "MAILING")}
                row["registry_zip"] = next(iter(sorted(z for z in zips if z)), None)
                row["linked"] = bool(zips & roster_zips) if roster_zips else None
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
            row["cert_stated"] = any(_CERT_RE.search(page_text[max(0, i - 250): i + 250]) for i in near[:20]) if near else False
        rows.append(row)
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
        lines.append("Physician↔practice linkage: could not be measured (registry matches ambiguous or no location zips) — estimate as usual.")
    if f.get("board_cert_unverifiable") is not None:
        lines.append(f"Board certification stated on the pages of the practice's own site that we read: {f['cert_stated']} of {f['cert_checked']} sampled physicians "
                     f"({f.get('bio_pages_read', 0)} bio pages read). Treat as supporting evidence only — our read of bio pages is partial; "
                     "judge board_cert_unverifiable from all sources as usual.")
    miss = [r["name"] for r in f.get("rows") or [] if r.get("cert_stated") is False][:8]
    if miss:
        lines.append("Physicians without a certification statement on the site: " + ", ".join(miss) + ".")
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
