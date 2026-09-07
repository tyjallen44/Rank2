"""Service-line discovery + normalization — Phase A of the service-line listing
analysis (see docs/service-line-listing-analysis.md).

From a system name + website, enumerate the clinical service lines it offers,
each with a validated landing URL, normalized to a fixed canonical taxonomy.

Design commitments:
- Reuse the existing crawler/browser (content_analyzer), don't re-implement fetch.
- Mirror the proven Playwright->text->Claude tool_use extraction pattern used by
  network_analyzer for hospital rosters.
- NO SILENT FAILURE: low-confidence / unmatched ("other") lines are surfaced and
  flagged, never dropped; a blocked hub sets coverage=partial; the function never
  raises and always returns whatever it enumerated.
"""
from __future__ import annotations

import json
from urllib.parse import urljoin, urlparse

import anthropic
from bs4 import BeautifulSoup

from .models import ServiceLine, ServiceLineSet
from .content_analyzer import _BrowserFetcher, _norm_url, _origin

client = anthropic.Anthropic()
_MODEL = "claude-opus-4-8"

_MAX_HUBS = 4
_MAX_LINKS_PER_HUB = 400        # cap link extraction work per page

# Anchor text / href hints that mark a "services / specialties / centers" hub.
_HUB_HINTS = ("services", "specialties", "specialty", "centers", "center",
              "institute", "institutes", "conditions", "treatments",
              "areas-of-care", "medical-services", "health-services",
              "centers-of-excellence", "find-care", "our-services",
              "clinical-services", "care/")

# ── Canonical taxonomy (fixed v1) ─────────────────────────────────────────────
# key -> (label, aliases). Alias match is token/substring against a raw name.
_CANONICAL_SERVICE_LINES: dict = {
    "cardiology": ("Cardiology / Heart & Vascular",
                   ["cardiology", "heart", "cardiac", "cardiovascular", "vascular", "sanger heart"]),
    "orthopedics": ("Orthopedics",
                    ["orthopedic", "orthopaedic", "ortho", "bone & joint", "bone and joint",
                     "musculoskeletal", "spine", "joint replacement", "sports medicine"]),
    "oncology": ("Cancer / Oncology",
                 ["oncology", "cancer", "hematology", "tumor", "radiation oncology"]),
    "neuroscience": ("Neurology & Neurosurgery",
                     ["neurology", "neuroscience", "neuro", "brain & spine", "brain and spine",
                      "stroke", "neurosurgery"]),
    "womens_health": ("Women's Health",
                      ["women's health", "womens health", "ob/gyn", "obgyn", "obstetrics",
                       "gynecology", "maternity", "midwifery", "women's"]),
    "pediatrics": ("Pediatrics",
                   ["pediatric", "pediatrics", "children's", "childrens", "peds"]),
    "primary_care": ("Primary Care",
                     ["primary care", "family medicine", "internal medicine", "general practice"]),
    "gastroenterology": ("Gastroenterology",
                         ["gastroenterology", "gastro", "digestive", "gi"]),
    "urology": ("Urology", ["urology", "urologic", "urological"]),
    "pulmonology": ("Pulmonology", ["pulmonology", "pulmonary", "lung", "respiratory"]),
    "nephrology": ("Nephrology", ["nephrology", "kidney", "renal"]),
    "endocrinology": ("Endocrinology", ["endocrinology", "diabetes", "hormone", "thyroid"]),
    "ent": ("ENT / Otolaryngology",
            ["ent", "otolaryngology", "ear nose", "ear, nose", "head & neck", "head and neck"]),
    "ophthalmology": ("Ophthalmology", ["ophthalmology", "eye", "vision"]),
    "dermatology": ("Dermatology", ["dermatology", "skin"]),
    "rheumatology": ("Rheumatology", ["rheumatology", "arthritis"]),
    "general_surgery": ("Surgery", ["general surgery", "surgical services", "surgery"]),
    "transplant": ("Transplant", ["transplant"]),
    "behavioral_health": ("Behavioral Health",
                          ["behavioral", "psychiatry", "mental health", "psychiatric"]),
    "rehabilitation": ("Rehabilitation",
                       ["rehabilitation", "rehab", "physical therapy", "pm&r", "physiatry"]),
    "emergency": ("Emergency & Trauma", ["emergency", "trauma"]),
    "urgent_care": ("Urgent Care", ["urgent care", "walk-in", "walk in"]),
    "imaging": ("Imaging / Radiology", ["imaging", "radiology", "diagnostic imaging"]),
    "bariatrics": ("Bariatrics / Weight Loss", ["bariatric", "weight loss", "metabolic surgery"]),
    "pain_management": ("Pain Management", ["pain management", "pain medicine", "pain center"]),
    "wound_care": ("Wound Care", ["wound care", "wound", "hyperbaric"]),
    "infectious_disease": ("Infectious Disease", ["infectious disease"]),
    "sleep_medicine": ("Sleep Medicine", ["sleep medicine", "sleep center", "sleep disorder"]),
}


# ── LLM tools ─────────────────────────────────────────────────────────────────
_EXTRACT_TOOL = {
    "name": "submit_service_lines",
    "description": "Submit the TOP-LEVEL clinical service lines (major specialties / "
                   "centers / institutes) offered by this health system, e.g. Cardiology / "
                   "Heart & Vascular, Cancer, Orthopedics, Neurosciences, Women's Health, "
                   "Primary Care. EXCLUDE: virtual/telehealth visit types, individual "
                   "conditions or tests (e.g. 'COVID-19 testing'), classes/events, "
                   "provider-search links, locations, insurance/billing, careers, and site "
                   "navigation. Prefer each line's dedicated landing page URL (not a "
                   "provider-search query URL).",
    "input_schema": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Service line name as shown"},
                        "url": {"type": ["string", "null"],
                                "description": "Its landing page URL if present on the page, else null"},
                    },
                    "required": ["name"],
                },
            }
        },
        "required": ["lines"],
    },
}

_MAP_TOOL = {
    "name": "map_service_lines",
    "description": "Map each raw service-line name to the single best canonical key from "
                   "the provided list, or 'other' if none fits.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw": {"type": "string"},
                        "key": {"type": "string"},
                    },
                    "required": ["raw", "key"],
                },
            }
        },
        "required": ["mappings"],
    },
}


def _norm(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _same_domain(url: str, origin: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(origin).netloc
    except Exception:
        return False


def _find_hubs(html: str, origin: str, max_hubs: int) -> list:
    """Same-domain links that hint at a services hub, ranked so the strongest
    'services / specialties / centers' pages come first."""
    scored, seen = [], set()
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(origin, a["href"])
            if not _same_domain(href, origin) or href in seen:
                continue
            anchor = a.get_text(" ", strip=True).lower()
            hay = anchor + " " + href.lower()
            hits = [h for h in _HUB_HINTS if h in hay]
            if not hits:
                continue
            seen.add(href)
            path = urlparse(href).path.rstrip("/").lower()
            segs = [p for p in path.split("/") if p]
            base = segs[-1] if segs else ""
            # Score: keyword hits + explicit services path, and strongly prefer the
            # SHALLOW services index (e.g. /medical-services) over deep condition
            # pages (e.g. /medical-services/heart/conditions/...).
            score = len(hits)
            score += sum(2 for kw in ("service", "specialt", "institute", "center",
                                      "areas-of-care") if kw in path)
            score += max(0, 4 - len(segs))          # shallower ranks higher
            if base in ("services", "specialties", "medical-services", "our-services",
                        "centers", "conditions", "specialty-care", "clinical-services",
                        "care", "areas-of-care"):
                score += 3
            scored.append((score, href))
    except Exception:
        pass
    scored.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in scored[:max_hubs]]


def _page_links(html: str, origin: str) -> list:
    """Same-domain (anchor_text, href) pairs — the candidate landing URLs."""
    out = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(origin, a["href"])
            if _same_domain(href, origin):
                t = a.get_text(" ", strip=True)
                if t:
                    out.append((t, href))
            if len(out) >= _MAX_LINKS_PER_HUB:
                break
    except Exception:
        pass
    return out


def _extract_from_hub(hub_url: str, text: str, links: list) -> list:
    """Claude tool_use: pull clinical service lines from one hub page. Returns
    [{name, url}]. Fail-soft -> [] on any error."""
    link_lines = "\n".join(f"- {t} -> {h}" for t, h in links[:200])
    prompt = (f"Health-system services page: {hub_url}\n\n"
              f"PAGE TEXT (truncated):\n{text[:6000]}\n\n"
              f"SAME-DOMAIN LINKS (anchor -> url):\n{link_lines}\n\n"
              "Return the clinical service lines this system offers. Use the links to "
              "fill each url when one matches; otherwise null.")
    try:
        resp = client.messages.create(
            model=_MODEL, max_tokens=4096, tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "submit_service_lines"},
            system="You extract patient-facing clinical service lines from a hospital "
                   "system's website. Be precise: never invent lines or URLs.",
            messages=[{"role": "user", "content": prompt}])
        for block in resp.content:
            if block.type == "tool_use" and block.name == "submit_service_lines":
                d = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return d.get("lines", []) or []
    except Exception:
        return []
    return []


def _compiled_aliases():
    """Per-key word-boundary regex so short aliases (ent, gi, eye) don't match
    inside other words ('urgent', 'surgery', 'eyewear')."""
    import re
    out = {}
    for key, (label, aliases) in _CANONICAL_SERVICE_LINES.items():
        pat = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
        out[key] = (label, re.compile(r"\b(?:" + pat + r")\b", re.I))
    return out


_ALIAS_RE = _compiled_aliases()


def _alias_match(raw_name: str):
    """Deterministic word-boundary alias match -> (key, label) or None."""
    n = _norm(raw_name)
    for key, (label, rx) in _ALIAS_RE.items():
        if rx.search(n):
            return key, label
    return None


def _llm_map(raw_names: list) -> dict:
    """Map leftover raw names to canonical keys via one batched Claude call.
    Returns {raw: key}; missing/unknown -> caller treats as 'other'."""
    if not raw_names:
        return {}
    keys = ", ".join(_CANONICAL_SERVICE_LINES.keys())
    prompt = ("Canonical keys: " + keys + ", other\n\n"
              "Map each raw service-line name to the single best key (or 'other'):\n"
              + "\n".join(f"- {r}" for r in raw_names))
    try:
        resp = client.messages.create(
            model=_MODEL, max_tokens=2048, tools=[_MAP_TOOL],
            tool_choice={"type": "tool", "name": "map_service_lines"},
            system="You map hospital service-line names to a fixed taxonomy. "
                   "Choose exactly one key per name; use 'other' if none fits.",
            messages=[{"role": "user", "content": prompt}])
        for block in resp.content:
            if block.type == "tool_use" and block.name == "map_service_lines":
                d = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return {m.get("raw", ""): m.get("key", "other")
                        for m in d.get("mappings", [])}
    except Exception:
        return {}
    return {}


def discover_service_lines(system_name: str, urls: list, hq_location: str = "",
                           on_event=None, max_hubs: int = _MAX_HUBS,
                           cache: bool = True) -> ServiceLineSet:
    """Enumerate a system's service lines from its own site. Never raises."""
    emit = on_event or (lambda e: None)
    result = ServiceLineSet(system_name=system_name)
    urls = [_norm_url(u) for u in (urls or []) if (u or "").strip()]
    if not urls:
        result.coverage = "none"
        return result

    browser = _BrowserFetcher()
    raw_candidates: list = []      # (name, url|None, source_hub)
    try:
        home_url = urls[0]
        origin = _origin(home_url)
        # Render with the browser: many system sites are SPAs whose services list
        # is client-rendered, so the raw httpx shell would be empty.
        home = browser.fetch_rendered_html(home_url)
        if not home:
            emit({"type": "text", "text": "\nCould not load the system homepage."})
            result.coverage = "none"
            return result

        emit({"type": "text", "text": "\nLocating services directory…"})
        # Prefer dedicated services/specialties hubs; the homepage is only a
        # fallback (its nav mixes in non-clinical links).
        hubs = _find_hubs(home, origin, max_hubs) or [home_url]

        for hub in hubs:
            html = home if hub == home_url else (browser.fetch_rendered_html(hub) or "")
            if not html:
                result.coverage = "partial"      # a hub we meant to read failed
                continue
            text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            links = _page_links(html, origin)
            for item in _extract_from_hub(hub, text, links):
                nm = (item.get("name") or "").strip()
                if nm:
                    raw_candidates.append((nm, item.get("url"), hub))
            result.hubs_crawled.append(hub)
        emit({"type": "text", "text": f"  Found {len(raw_candidates)} candidate service line(s)."})
    finally:
        browser.close()

    if not raw_candidates:
        if result.coverage != "partial":
            result.coverage = "none"
        return result

    # Normalize: alias match first, then one batched LLM call for the leftovers.
    emit({"type": "text", "text": "  Normalizing service lines…"})
    valid_link = lambda u: bool(u) and _same_domain(u, _origin(urls[0]))
    prelim, unmatched = [], []
    for name, url, hub in raw_candidates:
        m = _alias_match(name)
        prelim.append({"raw": name, "url": url if valid_link(url) else None,
                       "hub": hub, "key": (m[0] if m else None),
                       "label": (m[1] if m else None)})
        if not m:
            unmatched.append(name)
    mapped = _llm_map(sorted(set(unmatched))) if unmatched else {}

    # Dedupe by canonical key (keep first landing URL / best confidence).
    by_key: dict = {}
    for p in prelim:
        if p["key"]:
            key, label, conf = p["key"], p["label"], "high"
        else:
            key = mapped.get(p["raw"], "other")
            label = (_CANONICAL_SERVICE_LINES[key][0] if key in _CANONICAL_SERVICE_LINES
                     else "Other / Uncategorized")
            conf = "medium" if key != "other" else "low"
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = ServiceLine(raw_name=p["raw"], canonical_key=key,
                                      canonical_label=label, landing_url=p["url"],
                                      source_url=p["hub"], confidence=conf)
        elif existing.landing_url is None and p["url"]:
            existing.landing_url = p["url"]

    # NO SILENT FAILURE: 'other'/low-confidence lines are kept and flagged, not dropped.
    result.lines = sorted(by_key.values(),
                          key=lambda s: (s.canonical_key == "other", s.canonical_label))
    if result.coverage != "partial":
        result.coverage = "full"
    emit({"type": "text", "text": f"  Normalized to {len(result.lines)} service line(s)."})
    return result
