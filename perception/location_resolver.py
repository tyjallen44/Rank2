"""Location resolver — every office of a practice, from sources that can be checked.

Order of trust:
  1. The practice's own website (Locations / Offices / Contact page, schema.org Place markup,
     one-office-per-page sub-pages) — the authoritative list of offices with street addresses.
  2. The NPPES registry — the cities where the organization holds a type-2 NPI; cross-check
     and search seeds.
  3. Google Places — each website office pinned to one listing by address; then a brand-name
     snowball search seeded by the website and NPPES cities finds listings the website omits.
  4. Model recall (`discover_practice_siblings`) — only when the website gave nothing AND the
     Google search found nothing; never the sole source when a verified one exists.

Every location carries `sources` (website / nppes / google / model) and two gap flags that
become findings on the report: `google_missing` (an office on the website with no Google
listing) and `website_missing` (a Google listing for the practice that is not on the website).

Nothing is sampled: a specialty practice has every office found, pinned and printed. The
only limit is a runaway guard on how many sub-pages are read.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

_LOC_PATHS = ("/locations", "/our-locations", "/location", "/offices", "/our-offices", "/clinics", "/our-clinics",
              "/find-a-location", "/locations-and-hours", "/locations-hours", "/contact", "/contact-us", "/visit-us", "/directions")
_LOC_LINK = re.compile(r"location|office|clinic|contact|visit|find-us|direction|where-we-are|hours", re.I)
_SKIP_LINK = re.compile(r"\.(pdf|jpg|jpeg|png|gif|svg|zip)$|mailto:|tel:|#", re.I)
_MAX_LOCATION_PAGES = 80          # runaway guard, not a sample — practices have < 30 offices
_STREET_TYPES = {"st", "street", "ave", "avenue", "rd", "road", "blvd", "boulevard", "dr", "drive", "ln", "lane", "pkwy", "parkway",
                 "hwy", "highway", "way", "ct", "court", "pl", "place", "cir", "circle", "trl", "trail", "pike", "loop", "ter", "terrace",
                 "sq", "square", "plz", "plaza", "expy", "expressway", "n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east",
                 "west", "suite", "ste", "unit", "bldg", "building", "floor", "fl"}
_LABELS = re.compile(r"^(address|phone|fax|hours|directions|get directions|map|location|locations|office|our locations|contact|contact us|"
                     r"call|tel|telephone|email|appointments?|schedule|book|view map|learn more|read more|details|visit|home|menu)[:.]?$", re.I)
# Street is greedy within its line (a comma or line break ends it); the city is the capitalized
# word(s) just before ", ST 12345". Without a comma a multi-word city may lose its first word —
# the address still resolves; the Google pin works on street + ZIP.
_ADDR_RE = re.compile(
    r"(?P<street>\b\d{1,6}(?:-\d{1,4})?\s+[A-Za-z0-9'.#&/-]+(?:[ \t]+[A-Za-z0-9'.#&/-]+){0,7})"
    r"(?:[,\s]*(?:Suite|Ste\.?|Unit|Bldg\.?|Building|Floor|Fl\.?|#)[ \t]*[\w-]+)?"
    r"(?:,[ \t]*|[ \t]*\n[ \t]*|[ \t]+)(?P<city>[A-Z][A-Za-z.'\-]+(?:[ \t][A-Z][A-Za-z.'\-]+){0,3})"
    r"[,\s]+(?P<state>[A-Z]{2})[,\s]+(?P<zip>\d{5})(?:-\d{4})?\b")
_PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_STOP = {"the", "of", "and", "at", "for", "&", "in", "a", "an", "llc", "pc", "pa", "inc", "ltd", "md", "group", "center", "centre",
         "clinic", "clinics", "associates", "specialists", "institute", "medical", "health", "healthcare", "care"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _brand_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in _STOP and len(t) > 1}


def _acronym(name: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", name or "") if w.lower() not in {"the", "of", "and", "at", "for"}]
    return "".join(w[0] for w in words).lower() if len(words) >= 3 else ""


def brand_matches(brand: str, listing_name: str, *, min_overlap: float = 0.5) -> bool:
    """Does a listing name belong to the brand? Token overlap, or the brand's acronym, or the
    listing name starting with the brand's first distinctive word (DBAs like 'OrthoSouth Germantown')."""
    bt, lt = _brand_tokens(brand), _brand_tokens(listing_name)
    if bt and lt and len(bt & lt) / len(bt) >= min_overlap:
        return True
    acr = _acronym(brand)
    if acr and re.search(r"\b" + re.escape(acr) + r"\b", (listing_name or "").lower()):
        return True
    squashed_b = re.sub(r"[^a-z0-9]", "", (brand or "").lower())
    squashed_l = re.sub(r"[^a-z0-9]", "", (listing_name or "").lower())
    return bool(squashed_b) and len(squashed_b) >= 6 and squashed_b in squashed_l


def street_key(address: str) -> str:
    """'1234 main' — street number + first street word, for matching one office across sources."""
    m = re.match(r"\s*(\d{1,6})\s+([A-Za-z0-9'.-]+)", address or "")
    return f"{m.group(1)} {m.group(2).lower().rstrip('.')}" if m else ""


def _zip(address: str) -> str:
    m = re.search(r"\b(\d{5})(?:-\d{4})?\s*(?:,\s*)?(?:USA|United States)?\s*$", address or "")
    return m.group(1) if m else ""


def same_office(addr_a: str, addr_b: str) -> bool:
    """Two address strings describe the same office when the street number + first street word agree
    (and the ZIPs agree when both are present)."""
    ka, kb = street_key(addr_a), street_key(addr_b)
    if not ka or not kb or ka != kb:
        return False
    za, zb = _zip(addr_a), _zip(addr_b)
    return not (za and zb) or za == zb


def _city_state_of(address: str) -> tuple[str, str]:
    m = re.search(r",\s*([A-Za-z.'\- ]+?),\s*([A-Z]{2})\s+\d{5}", address or "")
    return (m.group(1).strip(), m.group(2)) if m else ("", "")


# ── 1. website ────────────────────────────────────────────────────────────────

class _Fetcher:
    """httpx first, headless browser when the plain client is refused or the page is script-rendered."""

    def __init__(self):
        from .content_analyzer import _client, _BrowserFetcher
        self.client = _client()
        self._browser_cls = _BrowserFetcher
        self.browser = None
        self.fetched = 0

    def html(self, url: str, *, allow_browser: bool = True) -> Optional[str]:
        self.fetched += 1
        try:
            r = self.client.get(url)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                return r.text
            blocked = r.status_code in (401, 403, 406, 429, 503)
        except Exception:
            blocked = True
        if blocked and allow_browser:
            if self.browser is None:
                self.browser = self._browser_cls()
            try:
                html, _ = self.browser.fetch_html(url)
                return html
            except Exception:
                return None
        return None

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass


def _jsonld_locations(soup) -> list[dict]:
    """Every schema.org object with a street address: Place / LocalBusiness / MedicalClinic /
    Physician / Organization.location / department / subOrganization — walked recursively."""
    out: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
            return
        if not isinstance(node, dict):
            return
        addr = node.get("address")
        if isinstance(addr, list):
            addr = addr[0] if addr else None
        if isinstance(addr, dict) and addr.get("streetAddress"):
            city, st, z = addr.get("addressLocality") or "", addr.get("addressRegion") or "", addr.get("postalCode") or ""
            full = ", ".join(p for p in (addr.get("streetAddress"), city, f"{st} {z}".strip()) if p)
            tel = node.get("telephone")
            out.append({"name": (node.get("name") or "").strip(), "address": full, "city": city, "state": st, "zip": str(z)[:5],
                        "phone": (tel[0] if isinstance(tel, list) and tel else tel) or ""})
        for k, v in node.items():
            if k != "address" and isinstance(v, (dict, list)):
                walk(v)

    for s in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            walk(json.loads(s.string or s.get_text() or ""))
        except Exception:
            continue
    return out


def _text_locations(text: str, brand: str) -> list[dict]:
    """Addresses in page text, each named by the nearest short heading-like line above it."""
    lines = [ln.strip() for ln in text.split("\n")]
    joined = "\n".join(lines)
    out: list[dict] = []
    for m in _ADDR_RE.finditer(joined):
        if m.end() - m.start() > 180:
            continue
        street, city, st, z = m.group("street").strip(), m.group("city").strip(), m.group("state"), m.group("zip")
        if not re.search(r"[A-Za-z]{2}", street.split(" ", 1)[-1]):      # "901 555 0100" is a phone, not a street
            continue
        # 'Main Street Memphis' with no comma: leading street-type words belong to the street
        parts = city.split()
        while len(parts) > 1 and parts[0].lower().rstrip(".") in _STREET_TYPES:
            street += " " + parts.pop(0)
        city = " ".join(parts)
        start_line = joined.count("\n", 0, m.start())
        end_line = joined.count("\n", 0, m.end())
        name = ""
        for i in range(start_line - 1, max(-1, start_line - 7), -1):
            cand = lines[i] if 0 <= i < len(lines) else ""
            if (2 <= len(cand) <= 70 and not _LABELS.match(cand) and not _ADDR_RE.search(cand) and not _PHONE_RE.search(cand)
                    and "@" not in cand and "http" not in cand.lower() and not re.match(r"^\d", cand)):
                name = cand
                break
        phone = ""
        for i in range(start_line, min(len(lines), end_line + 5)):
            pm = _PHONE_RE.search(lines[i])
            if pm and not re.search(r"fax", lines[i], re.I):
                phone = pm.group(0)
                break
        out.append({"name": name, "address": f"{street}, {city}, {st} {z}", "city": city, "state": st, "zip": z, "phone": phone})
    return out


def _dedupe_offices(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for it in items:
        k = street_key(it.get("address", "")) + "|" + (it.get("zip") or "")
        if not street_key(it.get("address", "")):
            continue
        if k in seen:
            if not seen[k].get("name") and it.get("name"):
                seen[k]["name"] = it["name"]
            if not seen[k].get("phone") and it.get("phone"):
                seen[k]["phone"] = it["phone"]
            continue
        seen[k] = dict(it)
    return list(seen.values())


def website_locations(site_url: Optional[str], brand: str, *, fetcher: Optional[_Fetcher] = None,
                      emit: Optional[Callable] = None) -> dict:
    """{"status": measured|none|unreachable|skipped, "url", "page", "pages_read", "locations": [...]}.
    Reads the homepage, follows its location-ish links and the common paths, parses schema.org and
    text addresses, and — when the index lists offices as links without addresses — reads each
    office's own page (runaway guard, not a sample)."""
    if not (site_url or "").strip():
        return {"status": "skipped", "url": None, "locations": []}
    from bs4 import BeautifulSoup
    from .content_analyzer import _origin, _norm_url
    own = fetcher is None
    f = fetcher or _Fetcher()
    url = _norm_url(site_url)
    origin = _origin(url)
    host = urlparse(origin).netloc.lower().replace("www.", "")
    found: list[dict] = []
    pages_read = 0
    best_page = None
    try:
        # Start at the site root even when the listing links a deep page (a location or doctor
        # page): the root's navigation is where the Locations page is linked from.
        home = f.html(origin + "/")
        if home is None and urlparse(url).path not in ("", "/"):
            home = f.html(url)
        if home is None:
            return {"status": "unreachable", "url": url, "locations": []}
        pages_read += 1
        soup = BeautifulSoup(home, "html.parser")
        found += _jsonld_locations(soup)

        def same_site(href: str) -> bool:
            return urlparse(href).netloc.lower().replace("www.", "") == host

        # Candidate location pages: homepage links that look like locations, then common paths.
        cands: list[str] = []
        for a in soup.find_all("a", href=True):
            href = urljoin(origin, a["href"].strip())
            if not same_site(href) or _SKIP_LINK.search(href):
                continue
            path, text = urlparse(href).path or "/", a.get_text(" ", strip=True)
            if path != "/" and (_LOC_LINK.search(path) or _LOC_LINK.search(text)) and href not in cands:
                cands.append(href)
        # Links whose own path says "location" come first; then the common paths.
        cands.sort(key=lambda h: 0 if re.search(r"location|office|clinic", urlparse(h).path, re.I) else 1)
        for p in _LOC_PATHS:
            if origin + p not in cands:
                cands.append(origin + p)
        if urlparse(url).path not in ("", "/") and url not in cands:
            cands.insert(0, url)          # the deep page the listing pointed at may itself be an office page
        seen = {_norm_url(url)}
        index_pages = 0
        for href in cands:
            if index_pages >= 6 or pages_read >= _MAX_LOCATION_PAGES:
                break
            if href in seen:
                continue
            seen.add(href)
            html = f.html(href)
            if not html:
                continue
            pages_read += 1
            index_pages += 1
            s2 = BeautifulSoup(html, "html.parser")
            for t in s2(["script", "style", "noscript"]):
                if not (t.name == "script" and "ld+json" in (t.get("type") or "")):
                    t.decompose()
            locs = _jsonld_locations(s2) + _text_locations(s2.get_text("\n"), brand)
            locs = _dedupe_offices(locs)
            # Office-per-page index: many same-section links, few addresses on the index itself.
            base = urlparse(href).path.rstrip("/")
            subs = []
            if base:
                for a in s2.find_all("a", href=True):
                    sub = urljoin(origin, a["href"].strip())
                    sp = urlparse(sub).path.rstrip("/")
                    if same_site(sub) and sp.startswith(base + "/") and sp != base and sub not in seen and not _SKIP_LINK.search(sub):
                        subs.append(sub)
            subs = list(dict.fromkeys(subs))
            if len(subs) >= 2 and len(locs) < max(2, len(subs) // 2):
                if emit:
                    emit({"type": "text", "text": f"Website lists {len(subs)} office pages under {base}/ — reading each one"})
                for sub in subs:
                    if pages_read >= _MAX_LOCATION_PAGES:
                        break
                    seen.add(sub)
                    sh = f.html(sub, allow_browser=False)
                    if not sh:
                        continue
                    pages_read += 1
                    s3 = BeautifulSoup(sh, "html.parser")
                    for t in s3(["script", "style", "noscript"]):
                        if not (t.name == "script" and "ld+json" in (t.get("type") or "")):
                            t.decompose()
                    sub_locs = _dedupe_offices(_jsonld_locations(s3) + _text_locations(s3.get_text("\n"), brand))
                    h1 = s3.find(["h1", "h2"])
                    title = h1.get_text(" ", strip=True) if h1 else ""
                    for sl in sub_locs[:2]:
                        if not sl.get("name") and title and len(title) <= 80:
                            sl["name"] = title
                    locs += sub_locs[:2]
                locs = _dedupe_offices(locs)
            if len(locs) > len([x for x in found if street_key(x.get("address", ""))]):
                found, best_page = locs, href
            if len(locs) >= 2:
                break      # a real locations page — done
        found = _dedupe_offices(found)
    finally:
        if own:
            f.close()
    for loc in found:
        nm = re.sub(r"[\s\-–—|:,]+$", "", (loc.get("name") or "").strip())
        loc["name"] = nm
        if not nm or _LABELS.match(nm):
            loc["name"] = f"{brand} – {loc.get('city') or 'Office'}"
    status = "measured" if found else "none"
    return {"status": status, "url": url, "page": best_page, "pages_read": pages_read, "locations": found}


# ── 2. NPPES ──────────────────────────────────────────────────────────────────

def nppes_cities(org_name: str, state: str) -> list[tuple[str, str]]:
    try:
        from .data.nppes import enumerate_org_locations
        return enumerate_org_locations(org_name, state) or []
    except Exception:
        return []


# ── 3. Google ─────────────────────────────────────────────────────────────────

def _search(name: str, city: Optional[str], state: Optional[str], n: int = 10) -> list[dict]:
    from .data.places import search_entity_candidates
    try:
        return search_entity_candidates(name, city, state, max_results=n) or []
    except TypeError:
        return search_entity_candidates(name, city, state) or []
    except Exception:
        return []


def pin_office_to_google(brand: str, office: dict, state: str, *, exclude: set[str]) -> Optional[dict]:
    """One Google listing for a website office: same street (and ZIP), name belongs to the brand."""
    addr, city = office.get("address", ""), office.get("city") or ""
    street = re.sub(r"[,\s]*(Suite|Ste\.?|Unit|#)\s*[\w-]+.*$", "", addr.split(",")[0]).strip()
    tried: list[dict] = []
    for q in (f"{brand} {street}", brand):
        for c in _search(q, city, office.get("state") or state, 8):
            if c.get("place_id") in exclude or c in tried:
                continue
            tried.append(c)
            if same_office(addr, c.get("address", "")) and brand_matches(brand, c.get("name", ""), min_overlap=0.34):
                return c
    return None


def google_snowball(brand: str, city: str, state: str, *, seeds: Optional[list[str]] = None, exclude: Optional[set[str]] = None,
                    max_queries: int = 14) -> dict:
    """Brand-name listings around the market: brand variants, then every city seen in a found address
    (and the given seed cities) becomes its own query. Returns {"candidates", "queries"}."""
    btoks = _brand_tokens(brand)
    acronym = _acronym(brand)
    seen = set(exclude or ())
    out: list[dict] = []

    def _city_of(addr: str) -> str:
        parts = [p.strip() for p in str(addr or "").split(",")]
        return parts[-3] if len(parts) >= 3 else ""

    def _take(cands):
        for c in cands:
            pid = c.get("place_id")
            if not pid or pid in seen:
                continue
            if not brand_matches(brand, c.get("name", "")):
                continue
            seen.add(pid)
            out.append(c)

    variants = [(brand, None, state), (brand, city, state), (f"{brand} clinic", None, state), (f"{brand} near {city}", None, state)]
    if acronym:
        variants += [(acronym.upper(), None, state), (acronym.upper(), city, state), (f"{acronym.upper()} doctors office", None, state)]
    for name, c, st in variants:
        _take(_search(name, c, st, 20))
    queried = {str(city).strip().lower()}
    n = 0
    frontier = [s for s in (seeds or []) if s] + [_city_of(c.get("address")) for c in out]
    while frontier and n < max_queries:
        nxt = []
        for cty in frontier:
            key = (cty or "").strip().lower()
            if not key or key in queried:
                continue
            queried.add(key)
            n += 1
            if n > max_queries:
                break
            before = len(out)
            _take(_search(brand, cty, state, 20))
            if acronym:
                _take(_search(acronym.upper(), cty, state, 20))
            nxt.extend(_city_of(c.get("address")) for c in out[before:])
        frontier = nxt
    return {"candidates": out, "queries": n + len(variants)}


def display_name(brand: str, google_name: Optional[str], website_name: Optional[str], city: str) -> str:
    """A name that tells offices apart. Google's name when it adds a locality or descriptor
    ("OrthoSouth Germantown"); otherwise the website's office name; otherwise 'Brand – City'.
    Google's '| Orthopedic Clinic' style suffixes are dropped."""
    g = re.split(r"\s+[|•·]\s+", (google_name or "").strip())[0].strip()
    distinct = bool(g) and bool(_brand_tokens(g) - _brand_tokens(brand)) or (bool(g) and bool(re.search(r"[-–—:]", g)))
    if distinct and g.lower() != brand.lower():
        return g
    w = (website_name or "").strip()
    if w and w.lower() != brand.lower():
        return w if (_brand_tokens(w) & _brand_tokens(brand)) else f"{brand} – {w}"
    return f"{brand} – {city}" if city else (g or brand)


def merge_confirmed_locations(confirmed: list[dict], resolved: list[dict]) -> tuple[list[dict], dict]:
    """The confirmed roster (entity graph) is the roster; the resolver's findings enrich it (place_id,
    rating, sources, gap flags) and tell the coordinator what drifted: offices the resolver found
    that are not confirmed (`new`) and confirmed offices it could not find (`missing`)."""
    def _match(c: dict, r: dict) -> bool:
        if c.get("place_id") and r.get("place_id"):
            return c["place_id"] == r["place_id"]
        if c.get("address") and r.get("address") and same_office(c["address"], r["address"]):
            return True
        return (c.get("name") or "").strip().lower() == (r.get("name") or "").strip().lower() and \
               (c.get("city") or "").strip().lower() == (r.get("city") or "").strip().lower()
    out: list[dict] = []
    used: set[int] = set()
    missing: list[str] = []
    for c in confirmed:
        hit = next((i for i, r in enumerate(resolved) if i not in used and _match(c, r)), None)
        s = {"name": c.get("original_name") or c.get("name"), "entity_type": "practice", "city": c.get("city") or "", "state": c.get("state") or "",
             "address": c.get("address") or "", "phone": "", "place_id": c.get("place_id"), "rating": c.get("rating"), "review_count": c.get("review_count"),
             "maps_url": c.get("maps_url"), "sources": ["confirmed"], "google_missing": False, "website_missing": False}
        if hit is not None:
            used.add(hit)
            r = resolved[hit]
            for k in ("address", "phone", "place_id", "rating", "review_count", "maps_url"):
                if r.get(k) not in (None, "") and s.get(k) in (None, ""):
                    s[k] = r[k]
            s["sources"] = ["confirmed"] + [x for x in (r.get("sources") or []) if x != "confirmed"]
            s["google_missing"] = bool(r.get("google_missing")) and not s.get("place_id")
            s["website_missing"] = bool(r.get("website_missing"))
        else:
            missing.append(s["name"])
        out.append(s)
    new = [r.get("name") for i, r in enumerate(resolved) if i not in used]
    return out, {"new": new, "missing": missing}


# ── 4. resolve ────────────────────────────────────────────────────────────────

def resolve_locations(entity_name: str, city: str, state: str, *, website: Optional[str] = None,
                      anchor_listing: Optional[dict] = None, emit: Optional[Callable] = None,
                      force_rerun: bool = False, allow_model: bool = True, use_registry: bool = True) -> dict:
    """Every office of the practice except the anchor, each with its sources and gap flags.

    Returns {"siblings": [...], "parent_org_name": str, "resolution": {...}} where each sibling is
    {name, entity_type, city, state, address, phone, place_id, rating, review_count, maps_url,
     sources: [...], google_missing: bool, website_missing: bool}."""
    from .entity_registry import get_registry_siblings, save_registry_siblings, expire_registry

    def _emit(text: str) -> None:
        if emit:
            emit({"type": "text", "text": text})

    if use_registry:
        if force_rerun:
            expire_registry(entity_name, city, state)
        else:
            cached = get_registry_siblings(entity_name, city, state)
            if cached is not None and any(s.get("sources") for s in cached):
                _emit(f"Using registry: {len(cached)} locations for {entity_name} (resolved earlier)")
                return {"siblings": cached, "parent_org_name": "",
                        "resolution": {"cached": True, "total": len(cached),
                                       "website_only": [s["name"] for s in cached if s.get("google_missing")],
                                       "google_only": [s["name"] for s in cached if s.get("website_missing")]}}

    brand = entity_name.strip()
    # Anchor: pin it so no sibling can be the same office.
    anchor = dict(anchor_listing or {})
    if not anchor.get("place_id") or not anchor.get("address"):
        try:
            from .data import places
            read, _ = places.fetch_provider(entity_name, city, state)
            if read is not None and getattr(read, "place_id", None):
                anchor.setdefault("place_id", read.place_id)
                anchor.setdefault("address", read.formatted_address or "")
                if not website and getattr(read, "website", None):
                    website = places.clean_website(read.website)
        except Exception:
            pass
    anchor_pid = anchor.get("place_id")
    anchor_addr = anchor.get("address") or ""

    # 1. website
    _emit(f"Reading {brand}'s website for its office list…" if website else "No website known — skipping the website office list")
    web = website_locations(website, brand, emit=emit) if website else {"status": "skipped", "url": None, "locations": []}
    offices = [o for o in web.get("locations") or [] if not (anchor_addr and same_office(o["address"], anchor_addr))]
    if web.get("status") == "measured":
        _emit(f"Website lists {len(web['locations'])} office{'s' if len(web['locations']) != 1 else ''}"
              + (f" ({len(web['locations']) - len(offices)} is the analyzed listing)" if len(web["locations"]) != len(offices) else ""))
    elif web.get("status") in ("none", "unreachable"):
        _emit("No office list could be read from the website" if web["status"] == "none" else "The website could not be reached for its office list")

    # 2. NPPES
    npp = nppes_cities(brand, state)
    npp_cities = {c.lower() for c, _ in npp}
    if npp:
        _emit(f"NPPES registry: {brand} holds organizational NPIs in {len(npp)} {'cities' if len(npp) != 1 else 'city'}")

    # 3. Google — pin each website office, then snowball for listings the website omits
    siblings: list[dict] = []
    used: set[str] = {anchor_pid} if anchor_pid else set()
    if offices:
        _emit(f"Pinning {len(offices)} website office{'s' if len(offices) != 1 else ''} to Google listings…")
    for o in offices:
        c = pin_office_to_google(brand, o, state, exclude=used)
        _cty = o.get("city") or _city_state_of((c or {}).get("address", ""))[0] or city
        s = {"name": display_name(brand, (c or {}).get("name"), o["name"], _cty), "entity_type": "practice",
             "city": _cty,
             "state": o.get("state") or state, "address": (c or {}).get("address") or o["address"], "phone": o.get("phone") or "",
             "sources": ["website"], "google_missing": c is None, "website_missing": False,
             "website_name": o["name"]}
        if c:
            used.add(c["place_id"])
            s.update({"place_id": c["place_id"], "rating": c.get("rating"), "review_count": c.get("review_count"),
                      "maps_url": c.get("maps_url")})
            s["sources"].append("google")
        if (s["city"] or "").lower() in npp_cities:
            s["sources"].append("nppes")
        siblings.append(s)
    seeds = sorted({(o.get("city") or "") for o in offices} | {c for c, _ in npp})
    snow = google_snowball(brand, city, state, seeds=seeds, exclude=used)
    extra = [c for c in snow["candidates"] if not (anchor_addr and same_office(c.get("address", ""), anchor_addr))]
    for c in extra:
        # already matched to a website office by address under a different place_id? treat as the same office
        if any(same_office(c.get("address", ""), s["address"]) for s in siblings):
            continue
        cty, st = _city_state_of(c.get("address", ""))
        s = {"name": display_name(brand, c.get("name"), None, cty or city), "entity_type": "practice", "city": cty or city, "state": st or state,
             "address": c.get("address") or "", "phone": "", "place_id": c["place_id"], "rating": c.get("rating"),
             "review_count": c.get("review_count"), "maps_url": c.get("maps_url"),
             "sources": ["google"], "google_missing": False, "website_missing": web.get("status") == "measured"}
        if (cty or "").lower() in npp_cities:
            s["sources"].append("nppes")
        siblings.append(s)
    if snow["candidates"]:
        _emit(f"Google: {len(extra)} brand listing{'s' if len(extra) != 1 else ''} found by search"
              + (f", {sum(1 for s in siblings if s['website_missing'])} not on the website" if web.get("status") == "measured" else ""))

    # 4. model — last resort only
    parent_org_name = ""
    model_used = False
    if not siblings and allow_model:
        _emit("No verified office list from the website or Google — asking the model as a last resort")
        try:
            from .practice_discovery import discover_practice_siblings
            recalled, parent_org_name = discover_practice_siblings(entity_name, city, state, on_event=emit, force_rerun=True)
        except Exception:
            recalled = []
        for r in recalled or []:
            siblings.append({**r, "address": r.get("address") or "", "phone": "", "sources": ["model"],
                             "google_missing": False, "website_missing": False})
        model_used = bool(recalled)

    for s in siblings:
        if "nppes" not in s["sources"] and (s.get("city") or "").lower() in npp_cities:
            s["sources"].append("nppes")
    resolution = {
        "cached": False, "total": len(siblings),
        "website": {"status": web.get("status"), "url": web.get("url"), "page": web.get("page"), "offices": len(web.get("locations") or []),
                    "pages_read": web.get("pages_read", 0)},
        "nppes": {"cities": len(npp)},
        "google": {"pinned": sum(1 for s in siblings if "website" in s["sources"] and "google" in s["sources"]),
                   "extra": sum(1 for s in siblings if s["sources"][0] == "google"), "queries": snow["queries"]},
        "model": {"used": model_used, "count": len(siblings) if model_used else 0},
        "website_only": [s["name"] for s in siblings if s.get("google_missing")],
        "google_only": [s["name"] for s in siblings if s.get("website_missing")],
    }
    if use_registry:
        try:
            save_registry_siblings(entity_name, city, state, siblings)
        except Exception:
            pass
    _emit(f"Location roster: {1 + len(siblings)} location{'s' if len(siblings) != 0 else ''} — "
          + ", ".join(f"{k}: {v}" for k, v in (("website", resolution['website']['offices']), ("Google", resolution['google']['pinned'] + resolution['google']['extra']),
                                              ("NPPES cities", len(npp))) if v))
    return {"siblings": siblings, "parent_org_name": parent_org_name, "resolution": resolution}
