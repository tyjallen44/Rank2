"""Physician resolver — every doctor of a practice, from sources that can be checked.

Order of trust:
  1. The practice's own provider directory (Doctors / Providers / Our Team pages, schema.org
     Physician markup, bio links, paginated and per-specialty index pages) — the names the
     practice itself publishes, with credentials and bio URLs.
  2. The NPPES registry — the cascading organization lookup (`physician_discovery._nppes_lookup`)
     that returns MD/DO records registered at the practice's addresses; website names without a
     registry hit are looked up individually by name.
  3. Model recall — only when the website gave nothing AND the registry returned nothing.

Every physician carries `sources` (website / nppes / model) and gap flags that become findings
on the report: `npi_missing` (on the website, no NPI record could be matched) and
`website_missing` (in the registry at the practice's addresses but not on the website).

Specialty practices are never sampled: every physician found is verified, rated and printed.
Hospital reports keep a cap (HOSPITAL_CAP) because a system can have thousands.
"""
from __future__ import annotations

import re
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

HOSPITAL_CAP = 50
_MAX_DIRECTORY_PAGES = 60          # runaway guard on index / pagination pages, not a sample

_DIRECTORY_PATHS = ("/doctors", "/providers", "/physicians", "/our-providers", "/our-doctors", "/our-physicians", "/our-team",
                    "/team", "/find-a-doctor", "/find-a-provider", "/find-a-physician", "/meet-our-team", "/meet-the-team",
                    "/meet-our-doctors", "/staff", "/surgeons", "/our-surgeons", "/specialists", "/medical-staff", "/about/providers",
                    "/about/our-team")
_DIR_LINK = re.compile(r"provider|physician|doctor|surgeon|our-team|meet-the|meet-our|staff|find-a|specialist|clinician", re.I)
_BIO_PATH = re.compile(r"(/dr-|/team/|/staff/|/physician[s]?/|/provider[s]?/|/doctor[s]?/|/surgeon[s]?/|/bio[s]?/|/people/|/our-team/|/meet-)", re.I)
_SKIP_LINK = re.compile(r"\.(pdf|jpg|jpeg|png|gif|svg|zip)$|mailto:|tel:|javascript:", re.I)
_PHYS_CREDS = ("MD", "DO", "DPM", "MBBS", "MBCHB", "MD PHD", "DO PHD")
_MID_CREDS = ("PA-C", "PA", "NP", "FNP", "FNP-C", "FNP-BC", "APRN", "ANP", "AGNP", "CRNA", "CNP", "DNP", "RN", "BSN", "MSN",
              "DPT", "PT", "MPT", "OT", "OTR", "ATC", "LAT", "PSYD", "LCSW", "RD", "CPO", "CP", "CO", "PHD", "DC", "LPN", "MA", "CMA",
              "CNM", "PA-S", "PTA", "OTA", "MSPT", "DDS", "DMD", "OD", "AUD")
_CRED_TOKEN = r"(?:M\.?D\.?|D\.?O\.?|D\.?P\.?M\.?|Ph\.?D\.?|D\.?D\.?S\.?|D\.?M\.?D\.?|PA-?C|PA|NP|FNP(?:-[BC])?|APRN|CRNA|DNP|CNP|DPT|PT|OT|OTR|ATC|RN|BSN|MSN|LAT|PsyD|LCSW|DC|O\.?D\.?|MBBS|FAAOS|FACS|FAAP|FACC|FACP|FAANS|FAOA|MPH|MBA|MMSPAS|CAQSM|CAQ)"
_TRAILING_CRED = re.compile(r"(?:[,\s]+(?:" + _CRED_TOKEN + r"))+\s*$")
_CRED_LIST = re.compile(r"^\s*((?:" + _CRED_TOKEN + r")(?:\s*[,/&]?\s*(?:" + _CRED_TOKEN + r"))*)\s*$")
_WORD = r"[A-Z][a-z'’\-]+(?:[A-Z][a-z'’\-]+)?"
_NAME_CORE = r"(?:" + _WORD + r")(?:\s+(?:[A-Z]\.?|" + _WORD + r")){0,2}\s+(?:" + _WORD + r")(?:\s+(?:Jr|Sr|II|III|IV)\.?)?"
_NAME_WITH_CRED = re.compile(r"(?:Dr\.?\s+)?(?P<name>" + _NAME_CORE + r")\s*,\s*(?P<cred>(?:" + _CRED_TOKEN + r")(?:\s*[,/&]?\s*(?:" + _CRED_TOKEN + r"))*)(?![A-Za-z])")
_DR_NAME = re.compile(r"\bDr\.?\s+(?P<name>" + _NAME_CORE + r")")
_NOT_NAME = re.compile(r"\b(Center|Clinic|Hospital|Institute|Orthop|Medical|Health|Surgery|Surgical|Physical|Therapy|Sports|Spine|Hand|Foot|"
                       r"Care|Group|Associates|Specialists|Appointment|Patient|Portal|Locations?|Services?|Contact|About|Request|Schedule|"
                       r"Read|More|View|Profile|Learn|Meet|Our|Team|Welcome|Privacy|Policy|Terms|Login|Pay|Bill|Careers?|News|Blog|Home)\b", re.I)
_STOP_FIRST = {"the", "a", "an", "our", "new", "all", "dr", "doctor", "meet", "view", "read", "more", "learn", "find", "request", "schedule",
               "call", "contact", "about", "patient", "physical", "sports", "orthopedic", "orthopaedic", "spine", "hand", "foot", "joint",
               "book", "pay", "online", "urgent", "walk", "total", "north", "south", "east", "west", "main", "general"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _clean_cred(cred: str) -> str:
    toks = [t.strip().upper().replace(".", "") for t in re.split(r"[,/&\s]+", cred or "") if t.strip()]
    return ", ".join(dict.fromkeys(toks))


def is_physician_cred(cred: str) -> Optional[bool]:
    """True for MD/DO/DPM-type credentials, False for advanced practitioners / allied staff, None if unknown."""
    toks = [t.strip().upper() for t in (cred or "").split(",") if t.strip()]
    if not toks:
        return None
    if any(t in _PHYS_CREDS for t in toks):
        return True
    if any(t in _MID_CREDS for t in toks):
        return False
    return None


def name_key(name: str) -> str:
    """'smith j' — last name + first initial; nicknames and middle names do not split a person."""
    parts = [p for p in re.sub(r"[^A-Za-z \-']", " ", name or "").split()
             if p and not re.fullmatch(r"(dr|md|do|mr|ms|mrs|jr|sr|ii|iii|iv|phd|dpm|pa|np|pa-c)\.?", p, re.I)]
    if len(parts) < 2:
        return (name or "").strip().lower()
    return f"{parts[-1].lower()} {parts[0][0].lower()}"


def _clean_name(raw: str) -> str:
    n = re.sub(r"^\s*(Dr\.?|Doctor)\s+", "", (raw or "").strip())
    n = re.sub(r"\s*\(.*?\)\s*", " ", n)
    n = re.sub(r"\s+", " ", n).strip(" ,.-–—")
    n = _TRAILING_CRED.sub("", n).strip(" ,")                    # "Luke H. Balsamo MD" → "Luke H. Balsamo"
    if n.isupper():                                              # registry names arrive upper-case
        n = " ".join(w.capitalize() if not re.fullmatch(r"(II|III|IV)", w) else w for w in n.split())
        n = re.sub(r"\b(Mc|Mac|O')([a-z])", lambda m: m.group(1) + m.group(2).upper(), n)
    words = n.split()
    # "Beach Bradley T. Butkovich" — a city or label glued to the front: four tokens where the
    # last is not a suffix means the first one is not part of the name.
    if len(words) >= 4 and not re.fullmatch(r"(Jr|Sr|II|III|IV)\.?", words[-1]):
        words = words[1:]
        n = " ".join(words)
    return n


def _looks_like_person(name: str) -> bool:
    if not name or len(name) > 48 or re.search(r"\d|@|http", name):
        return False
    words = name.split()
    if not (2 <= len(words) <= 5):
        return False
    if words[0].lower().rstrip(".") in _STOP_FIRST or _NOT_NAME.search(name):
        return False
    return all(re.match(r"^[A-Z][a-zA-Z'’\-\.]*$", w) or re.fullmatch(r"(Jr|Sr|II|III|IV)\.?", w) for w in words)


# ── 1. website ────────────────────────────────────────────────────────────────

def _jsonld_people(soup) -> list[dict]:
    import json
    out: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
            return
        if not isinstance(node, dict):
            return
        t = node.get("@type")
        types = {t} if isinstance(t, str) else set(t or [])
        if types & {"Physician", "Person", "Dentist", "MedicalBusiness"} and node.get("name") and _looks_like_person(_clean_name(str(node["name"]).split(",")[0])):
            nm = str(node["name"])
            cred = node.get("honorificSuffix") or (nm.split(",", 1)[1] if "," in nm else "")
            sp = node.get("medicalSpecialty") or node.get("jobTitle") or ""
            if isinstance(sp, list):
                sp = ", ".join(str(x) for x in sp[:2])
            out.append({"name": _clean_name(nm.split(",")[0]), "credential": _clean_cred(str(cred)), "specialty": str(sp)[:60],
                        "bio_url": node.get("url") or node.get("@id") or ""})
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v)

    for s in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            walk(json.loads(s.string or s.get_text() or ""))
        except Exception:
            continue
    return out


def _people_from_page(soup, origin: str, host: str) -> list[dict]:
    """Physician names on a directory page: schema.org first, then bio links, then text patterns."""
    people: list[dict] = _jsonld_people(soup)
    for a in soup.find_all("a", href=True):
        href = urljoin(origin, a["href"].strip())
        if urlparse(href).netloc.lower().replace("www.", "") != host or _SKIP_LINK.search(href):
            continue
        text = a.get_text(" ", strip=True)
        if not text or len(text) > 80:
            continue
        m = _NAME_WITH_CRED.search(text)
        if m:
            nm, cred = _clean_name(m.group("name")), _clean_cred(m.group("cred"))
        else:
            m2 = _DR_NAME.search(text)
            nm, cred = (_clean_name(m2.group("name")), "") if m2 else (_clean_name(text), "")
            if not m2 and not _BIO_PATH.search(urlparse(href).path):
                continue
        if _looks_like_person(nm):
            people.append({"name": nm, "credential": cred, "specialty": "", "bio_url": href})
    for t in soup(["script", "style", "noscript", "nav", "footer"]):
        t.decompose()
    text = soup.get_text("\n")
    for m in _NAME_WITH_CRED.finditer(text):
        nm = _clean_name(m.group("name"))
        if _looks_like_person(nm):
            people.append({"name": nm, "credential": _clean_cred(m.group("cred")), "specialty": "", "bio_url": ""})
    for m in _DR_NAME.finditer(text):
        nm = _clean_name(m.group("name"))
        if _looks_like_person(nm):
            people.append({"name": nm, "credential": "", "specialty": "", "bio_url": ""})
    return _dedupe_people(people)


def _dedupe_people(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    order: list[str] = []
    for p in items:
        k = name_key(p["name"])
        if not k or len(k) < 4:
            continue
        if k not in seen:
            seen[k] = dict(p)
            order.append(k)
            continue
        cur = seen[k]
        if not cur.get("credential") and p.get("credential"):
            cur["credential"] = p["credential"]
        if not cur.get("bio_url") and p.get("bio_url"):
            cur["bio_url"] = p["bio_url"]
        if not cur.get("specialty") and p.get("specialty"):
            cur["specialty"] = p["specialty"]
        if len(p["name"]) > len(cur["name"]) and " " in p["name"]:
            cur["name"] = p["name"]          # keep the fuller form (middle initial)
    return [seen[k] for k in order]


def website_physicians(site_url: Optional[str], *, hint_urls: Optional[list] = None, fetcher=None, emit: Optional[Callable] = None) -> dict:
    """{"status": measured|none|unreachable|skipped, "directory", "pages_read", "physicians": [...], "midlevels": [...]}.
    Reads the provider directory (hinted pages, homepage links, common paths), follows pagination
    and per-specialty index pages, and parses schema.org Physician markup, bio links and
    'Name, MD' patterns. Runaway guard, not a sample."""
    if not (site_url or "").strip():
        return {"status": "skipped", "physicians": [], "midlevels": []}
    from bs4 import BeautifulSoup
    from .content_analyzer import _origin, _norm_url
    from .location_resolver import _Fetcher
    own = fetcher is None
    f = fetcher or _Fetcher()
    url = _norm_url(site_url)
    origin = _origin(url)
    host = urlparse(origin).netloc.lower().replace("www.", "")
    pages_read = 0
    best: list[dict] = []
    best_url = None
    try:
        home = f.html(origin + "/")
        if home is None:
            return {"status": "unreachable", "physicians": [], "midlevels": []}
        pages_read += 1
        soup = BeautifulSoup(home, "html.parser")
        cands: list[str] = [u for u in (hint_urls or []) if u and urlparse(u).netloc.lower().replace("www.", "") == host]
        for a in soup.find_all("a", href=True):
            href = urljoin(origin, a["href"].strip())
            path, text = urlparse(href).path or "/", a.get_text(" ", strip=True)
            if (urlparse(href).netloc.lower().replace("www.", "") == host and path != "/" and not _SKIP_LINK.search(href)
                    and (_DIR_LINK.search(path) or _DIR_LINK.search(text)) and href not in cands):
                cands.append(href)
        cands.sort(key=lambda h: 0 if re.search(r"provider|physician|doctor|surgeon|team", urlparse(h).path, re.I) else 1)
        for p in _DIRECTORY_PATHS:
            if origin + p not in cands:
                cands.append(origin + p)
        seen = {_norm_url(url), origin + "/"}
        tried = 0
        for href in cands:
            if tried >= 8 or pages_read >= _MAX_DIRECTORY_PAGES:
                break
            if href in seen:
                continue
            seen.add(href)
            html = f.html(href)
            if not html:
                continue
            pages_read += 1
            tried += 1
            s2 = BeautifulSoup(html, "html.parser")
            people = _people_from_page(BeautifulSoup(html, "html.parser"), origin, host)
            base = urlparse(href).path.rstrip("/")
            # Pagination: follow rel=next / "Next" / ?page=N links while new names keep appearing.
            page_links = []
            for a in s2.find_all("a", href=True):
                sub = urljoin(origin, a["href"].strip())
                if urlparse(sub).netloc.lower().replace("www.", "") != host or sub in seen:
                    continue
                txt = (a.get_text(" ", strip=True) or "").lower()
                rel = " ".join(a.get("rel") or []) if isinstance(a.get("rel"), list) else str(a.get("rel") or "")
                if "next" in rel or txt in ("next", "next »", "next >", "›", "»", "load more") or re.search(r"[?&]page=\d+|/page/\d+", sub):
                    page_links.append(sub)
            for sub in list(dict.fromkeys(page_links))[:30]:
                if pages_read >= _MAX_DIRECTORY_PAGES:
                    break
                seen.add(sub)
                sh = f.html(sub, allow_browser=False)
                if not sh:
                    continue
                pages_read += 1
                more = _people_from_page(BeautifulSoup(sh, "html.parser"), origin, host)
                before = len(people)
                people = _dedupe_people(people + more)
                if len(people) == before:
                    break
            # Per-specialty / per-location index pages under the same section, when the index itself is thin.
            if len(people) < 3 and base:
                subs = []
                for a in s2.find_all("a", href=True):
                    sub = urljoin(origin, a["href"].strip())
                    sp = urlparse(sub).path.rstrip("/")
                    if (urlparse(sub).netloc.lower().replace("www.", "") == host and sp.startswith(base + "/") and sp != base
                            and sub not in seen and not _SKIP_LINK.search(sub)):
                        subs.append(sub)
                for sub in list(dict.fromkeys(subs))[:40]:
                    if pages_read >= _MAX_DIRECTORY_PAGES:
                        break
                    seen.add(sub)
                    sh = f.html(sub, allow_browser=False)
                    if not sh:
                        continue
                    pages_read += 1
                    s3 = BeautifulSoup(sh, "html.parser")
                    more = _people_from_page(s3, origin, host)
                    if not more:            # a bio page: its own title is the person
                        h1 = s3.find("h1")
                        t = _clean_name((h1.get_text(" ", strip=True) if h1 else "").split(",")[0])
                        if _looks_like_person(t):
                            m = _NAME_WITH_CRED.search(h1.get_text(" ", strip=True))
                            more = [{"name": t, "credential": _clean_cred(m.group("cred")) if m else "", "specialty": "", "bio_url": sub}]
                    people = _dedupe_people(people + more)
            if len(people) < 3 and _DIR_LINK.search(urlparse(href).path or "") and len(html) > 2000:
                # Thin result on a page that is clearly the directory: it is probably script-rendered.
                try:
                    if f.browser is None:
                        f.browser = f._browser_cls()
                    rendered, _st = f.browser.fetch_html(href)
                    if rendered and len(rendered) > len(html):
                        people = _dedupe_people(people + _people_from_page(BeautifulSoup(rendered, "html.parser"), origin, host))
                except Exception:
                    pass
            if len(people) > len(best):
                best, best_url = people, href
            if len(best) >= 3 and tried >= 2:
                break
            if emit and people:
                emit({"type": "text", "text": f"Website directory {urlparse(href).path}: {len(people)} name{'s' if len(people) != 1 else ''}"})
    finally:
        if own:
            f.close()
    phys = [p for p in best if is_physician_cred(p.get("credential", "")) is not False]
    mids = [p for p in best if is_physician_cred(p.get("credential", "")) is False]
    for p in phys:
        p["sources"] = ["website"]
    return {"status": "measured" if phys else ("none" if pages_read else "unreachable"), "directory": best_url,
            "pages_read": pages_read, "physicians": phys, "midlevels": mids}


# ── 2+3. resolve ──────────────────────────────────────────────────────────────

def resolve_physicians(entity_name: str, city: str, state: str, *, website: Optional[str] = None, hint_urls: Optional[list] = None,
                       emit: Optional[Callable] = None, cap: Optional[int] = None, allow_model: bool = True) -> dict:
    """Every physician of the practice with sources and gap flags.

    Returns {"physicians": [{name, npi, credential, specialty, bio_url, sources, website_missing, npi_missing}],
             "resolution": {...}}. `cap` is None for practices (never sampled) and HOSPITAL_CAP for hospitals."""
    def _emit(text: str) -> None:
        if emit:
            emit({"type": "text", "text": text})

    brand = entity_name.strip()
    # 1. website directory
    web = website_physicians(website, hint_urls=hint_urls, emit=emit) if website else {"status": "skipped", "physicians": [], "midlevels": []}
    web_phys = list(web.get("physicians") or [])
    if web.get("status") == "measured":
        _emit(f"Website provider directory: {len(web_phys)} physician{'s' if len(web_phys) != 1 else ''}"
              + (f" (+{len(web.get('midlevels') or [])} advanced practitioners / staff, not scored)" if web.get("midlevels") else ""))
    elif website:
        _emit("No provider directory could be read from the website" if web.get("status") == "none" else "The website could not be reached for its provider directory")

    # 2. NPPES cascade at the practice's addresses
    try:
        from .physician_discovery import _nppes_lookup
        npp = _nppes_lookup(brand, city, state) or []
    except Exception:
        npp = []
    if npp:
        _emit(f"NPI registry: {len(npp)} physician{'s' if len(npp) != 1 else ''} registered at {brand}'s addresses")
    by_key: dict[str, dict] = {}
    order: list[str] = []
    for p in web_phys:
        k = name_key(p["name"])
        by_key[k] = {"name": p["name"], "npi": None, "credential": p.get("credential") or "", "specialty": p.get("specialty") or "",
                     "bio_url": p.get("bio_url") or "", "sources": ["website"], "website_missing": False, "npi_missing": False}
        order.append(k)
    npp_keys = {}
    for r in npp:
        r = {**r, "name": _clean_name(r.get("name") or "")}      # registry names arrive upper-case
        k = name_key(r.get("name") or "")
        if not k:
            continue
        npp_keys[k] = r
        if k in by_key:
            e = by_key[k]
            e["npi"] = r.get("npi")
            e["credential"] = e["credential"] or (r.get("credential") or "")
            e["specialty"] = e["specialty"] or (r.get("specialty") or "")
            if "nppes" not in e["sources"]:
                e["sources"].append("nppes")
        else:
            by_key[k] = {"name": r.get("name"), "npi": r.get("npi"), "credential": r.get("credential") or "", "specialty": r.get("specialty") or "",
                         "bio_url": "", "sources": ["nppes"], "website_missing": web.get("status") == "measured", "npi_missing": False}
            order.append(k)
    # website names the cascade did not return: look each one up by name
    unmatched = [k for k in order if "nppes" not in by_key[k]["sources"] and "website" in by_key[k]["sources"]]
    if unmatched:
        try:
            from .data.physician_facts import _nppes_lookup_physician
        except Exception:
            _nppes_lookup_physician = None
        for k in unmatched:
            e = by_key[k]
            recs = []
            if _nppes_lookup_physician is not None:
                try:
                    recs = _nppes_lookup_physician(e["name"], state) or []
                except Exception:
                    recs = []
            if len(recs) == 1:
                rec = recs[0]
                e["npi"] = rec.get("number")
                tax = next((t for t in (rec.get("taxonomies") or []) if t.get("primary")), None) or ((rec.get("taxonomies") or [{}])[0])
                e["specialty"] = e["specialty"] or (tax.get("desc") or "")
                e["credential"] = e["credential"] or (rec.get("basic", {}).get("credential") or "").replace(".", "").upper()
                e["sources"].append("nppes")
            else:
                e["npi_missing"] = True

    physicians = [by_key[k] for k in order]
    # 3. model — last resort only
    model_used = False
    if not physicians and allow_model:
        _emit("No physician list from the website or the NPI registry — asking the model as a last resort")
        try:
            from .physician_discovery import _claude_discover
            recalled = _claude_discover(brand, city, state, on_event=emit) or []
        except Exception:
            recalled = []
        for r in recalled:
            physicians.append({"name": r.get("name"), "npi": r.get("npi"), "credential": r.get("credential") or "", "specialty": r.get("specialty") or "",
                               "bio_url": "", "sources": ["model"], "website_missing": False, "npi_missing": not r.get("npi")})
        model_used = bool(recalled)
    capped = False
    if cap and len(physicians) > cap:
        physicians = sorted(physicians, key=lambda p: 0 if "website" in p["sources"] else 1)[:cap]
        capped = True
    resolution = {
        "total": len(physicians), "capped": capped, "cap": cap,
        "website": {"status": web.get("status"), "directory": web.get("directory"), "count": len(web_phys), "pages_read": web.get("pages_read", 0),
                    "midlevels": len(web.get("midlevels") or [])},
        "nppes": {"count": len(npp)},
        "both": sum(1 for p in physicians if "website" in p["sources"] and "nppes" in p["sources"]),
        "model": {"used": model_used, "count": len(physicians) if model_used else 0},
        "website_only": [p["name"] for p in physicians if p.get("npi_missing") and "website" in p["sources"]],
        "registry_only": [p["name"] for p in physicians if p.get("website_missing")],
    }
    _emit(f"Physician roster: {len(physicians)} physician{'s' if len(physicians) != 1 else ''}"
          + (f" — {resolution['both']} on both the website and the registry" if resolution["both"] else "")
          + (f", {len(resolution['website_only'])} on the website with no NPI match" if resolution["website_only"] else "")
          + (f", {len(resolution['registry_only'])} in the registry but not on the website" if resolution["registry_only"] else "")
          + (f" (capped at {cap})" if capped else ""))
    return {"physicians": physicians, "resolution": resolution}
