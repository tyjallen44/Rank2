"""Phase C — managed-presence detection + patient-attraction scoring
(see docs/service-line-listing-analysis.md).

Scores each service line (with its Phase-B flagship + location sample) on the
4-dimension rubric and derives a management status, with evidence per dimension.

Dimension weights: findability 25 · completeness 25 · reputation 30 · content 20.
Review-response rate is NOT assessed in v1 (the Places API doesn't expose owner
replies), so reputation is rating+volume+recency (renormalized) — stated in the
signals, never silently zeroed. Fail-soft throughout; never raises.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import anthropic
from bs4 import BeautifulSoup

from .models import (DimensionScore, ServiceLineScorecard, ServiceLineScorecardSet,
                     ServiceLineListingSet)
from .content_analyzer import _BrowserFetcher, _schema_types
from .service_line_locations import _brand_tokens
from .data.places import place_details

client = anthropic.Anthropic()
_AI_MODEL = "claude-opus-4-8"

_WEIGHTS = {"findability": 25, "completeness": 25, "reputation": 30, "content": 20}
_DETAILS_PER_LINE = 5      # top N locations scored for details (+ the flagship)

_MED_SCHEMA = {"MedicalOrganization", "Hospital", "MedicalClinic", "Physician",
               "MedicalBusiness", "MedicalProcedure", "MedicalSpecialty", "Dentist"}
_BOOK_KW = ("schedule", "book appointment", "book online", "request appointment",
            "make an appointment", "request an appointment", "schedule now", "book now",
            "schedule an appointment", "book a", "request a")
_PROV_KW = ("find a doctor", "find-a-doctor", "our providers", "our team", "meet our",
            "our physicians", "our doctors", "provider directory")


def _clamp(x: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(x))))


def _rating_score(r):
    return None if r is None else _clamp((r - 2.5) / 2.3 * 100)


def _volume_score(n):
    return None if n is None else _clamp(100 * math.log10(n + 1) / math.log10(300))


def _all_listings(line):
    return ([line.flagship] if line.flagship else []) + list(line.locations)


def _most_recent_review_age(details: list):
    latest = None
    for d in details:
        for rv in (d.get("reviews") or []):
            ts = rv.get("publishTime")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if latest is None or dt > latest:
                latest = dt
    if latest is None:
        return None
    return max(0, (datetime.now(timezone.utc) - latest).days)


def _score_completeness(details: list) -> DimensionScore:
    d = next((x for x in details if x), None)
    if not d:
        return DimensionScore(key="completeness", score=None, status="not_assessed",
                              signals=["No Google Business Profile details available."])
    checks = {
        "category": bool(d.get("primaryType") or d.get("types")),
        "hours": bool(d.get("regularOpeningHours")),
        "phone": bool(d.get("nationalPhoneNumber")),
        "website": bool(d.get("websiteUri")),
        "description": bool((d.get("editorialSummary") or {}).get("text")),
        "photos": len(d.get("photos") or []) >= 3,
    }
    present = [k for k, v in checks.items() if v]
    missing = [k for k, v in checks.items() if not v]
    sig = [f"GBP fields present ({len(present)}/6): {', '.join(present) or 'none'}."]
    if missing:
        sig.append(f"Missing: {', '.join(missing)}.")
    return DimensionScore(key="completeness", score=_clamp(len(present) / 6 * 100),
                          status="verified", signals=sig)


def _score_reputation(line, details: list) -> DimensionScore:
    listings = _all_listings(line)
    ratings = [l.rating for l in listings if l.rating is not None]
    counts = [l.review_count for l in listings if l.review_count]
    if not ratings:
        return DimensionScore(key="reputation", score=None, status="not_assessed",
                              signals=["No rated Google listing for this service line."])
    avg = sum(ratings) / len(ratings)
    total = sum(counts) if counts else 0
    # Score on rating + volume only (both reliable). Recency is shown as an
    # informational signal but NOT scored: the Places API returns the "most
    # relevant" reviews, not the newest, so a recency number would be misleading.
    parts = [("rating", _rating_score(avg), 40), ("volume", _volume_score(total), 30)]
    parts = [(k, s, w) for k, s, w in parts if s is not None]
    tot_w = sum(w for _, _, w in parts)
    score = _clamp(sum(s * w for _, s, w in parts) / tot_w) if tot_w else None
    days = _most_recent_review_age(details)
    sig = [f"Avg {avg:.1f}★ across {len(ratings)} listing(s); {total} total reviews.",
           (f"Sampled reviews go back ~{days}d (Google returns most-relevant, not newest)."
            if days is not None else "Review recency unavailable."),
           "Review-response rate not assessed (not exposed by the Places API)."]
    return DimensionScore(key="reputation", score=score, status="verified", signals=sig)


def _score_content(landing_url, browser) -> DimensionScore:
    if not landing_url:
        return DimensionScore(key="content", score=None, status="not_assessed",
                              signals=["No dedicated service-line page found (Phase A)."])
    html = browser.fetch_rendered_html(landing_url)
    if not html:
        return DimensionScore(key="content", score=None, status="not_assessed",
                              signals=[f"Could not load the service-line page ({landing_url})."])
    soup = BeautifulSoup(html, "html.parser")
    low = soup.get_text(" ", strip=True).lower()
    words = len(low.split())
    has_schema = bool(_schema_types(html) & _MED_SCHEMA)
    has_book = any(k in low for k in _BOOK_KW)
    has_prov = any(k in low for k in _PROV_KW)
    score = ((30 if has_schema else 0) + (30 if has_book else 0)
             + min(words, 600) / 600 * 25 + (15 if has_prov else 0))
    sig = [f"schema.org medical markup: {'yes' if has_schema else 'no'}.",
           f"online-scheduling CTA: {'yes' if has_book else 'no'}.",
           f"~{words} words; provider/team links: {'yes' if has_prov else 'no'}."]
    return DimensionScore(key="content", score=_clamp(score), status="verified", signals=sig)


def _ai_surfaces(system_brand: set, label: str, metro: str):
    """1 bounded probe: does the system surface for 'best {label} in {metro}'?
    Returns True/False, or None if not assessable (no metro / call failed)."""
    if not metro or not system_brand:
        return None
    q = (f"When someone asks for the best {label} in {metro}, which hospitals or "
         f"health systems would you name? List the top 5 by name.")
    try:
        resp = client.messages.create(model=_AI_MODEL, max_tokens=400,
                                      messages=[{"role": "user", "content": q}])
        text = " ".join(b.text for b in resp.content if b.type == "text").lower()
    except Exception:
        return None
    return any(t in text for t in system_brand)


def _score_findability(line, content_dim, ai_surfaced) -> DimensionScore:
    has_page = content_dim.status == "verified"
    has_gbp = bool(line.flagship) or bool(line.locations)
    parts = [("page", 100 if has_page else 0, 40), ("gbp", 100 if has_gbp else 0, 30)]
    if ai_surfaced is not None:
        parts.append(("ai", 100 if ai_surfaced else 0, 30))
    tot_w = sum(w for _, _, w in parts)
    score = _clamp(sum(s * w for _, s, w in parts) / tot_w) if tot_w else None
    sig = [f"dedicated page: {'yes' if has_page else 'no'}.",
           f"Google listing: {'yes' if has_gbp else 'no'}.",
           (f"AI names the system for 'best {line.canonical_label}': "
            + ('yes' if ai_surfaced else 'no')) if ai_surfaced is not None
           else "AI surfacing: not assessed."]
    return DimensionScore(key="findability", score=score,
                          status=("verified" if ai_surfaced is not None else "partial"),
                          signals=sig)


def _blend(dims: list):
    parts = [(d.score, _WEIGHTS[d.key]) for d in dims if d.score is not None and d.key in _WEIGHTS]
    tot_w = sum(w for _, w in parts)
    return _clamp(sum(s * w for s, w in parts) / tot_w) if tot_w else None


def _management_status(dims: list) -> str:
    dmap = {d.key: d for d in dims}
    comp = dmap.get("completeness").score if dmap.get("completeness") else None
    has_page = dmap.get("content") and dmap["content"].status == "verified"
    if comp is not None and comp >= 65 and has_page:
        return "managed"
    if (comp is None or comp < 35) and not has_page:
        return "unmanaged"
    return "partial"


def score_service_lines(listing_set: ServiceLineListingSet, hq_location: str = "",
                        on_event=None, details_per_line: int = _DETAILS_PER_LINE,
                        ai_probe: bool = True, cache: bool = True) -> ServiceLineScorecardSet:
    """Score every service line's patient-attraction capability. Never raises."""
    emit = on_event or (lambda e: None)
    result = ServiceLineScorecardSet(system_name=listing_set.system_name)
    metro = (hq_location or "").strip()
    system_brand = _brand_tokens(listing_set.system_name)

    browser = _BrowserFetcher()
    try:
        for line in listing_set.lines:
            emit({"type": "text", "text": f"\nScoring {line.canonical_label}…"})
            card = ServiceLineScorecard(canonical_key=line.canonical_key,
                                        canonical_label=line.canonical_label)

            if line.coverage == "none":
                card.management_status = "invisible"
                card.notes.append("No affiliated listing found — invisible service line.")
                card.dimensions = [
                    DimensionScore(key="findability", score=0, status="verified",
                                   signals=["No Google listing and no clear dedicated page."]),
                    DimensionScore(key="completeness", score=None, status="not_assessed",
                                   signals=["No listing to assess."]),
                    DimensionScore(key="reputation", score=None, status="not_assessed",
                                   signals=["No listing to assess."]),
                    _score_content(line.landing_url, browser),
                ]
                card.overall_score = _blend(card.dimensions)
                result.scorecards.append(card)
                emit({"type": "text", "text": "  invisible (no listings)"})
                continue

            listings = ([line.flagship] if line.flagship else []) + line.locations[:details_per_line]
            details = [place_details(l.place_id) for l in listings if l.place_id]

            comp = _score_completeness(details)
            rep = _score_reputation(line, details)
            content = _score_content(line.landing_url, browser)
            ai_surfaced = _ai_surfaces(system_brand, line.canonical_label, metro) if ai_probe else None
            find = _score_findability(line, content, ai_surfaced)

            card.dimensions = [find, comp, rep, content]
            card.overall_score = _blend(card.dimensions)
            card.management_status = _management_status(card.dimensions)
            card.listings_scored = len(listings)
            result.scorecards.append(card)
            emit({"type": "text",
                  "text": f"  {card.management_status} · overall {card.overall_score}"})
    finally:
        browser.close()

    return result
