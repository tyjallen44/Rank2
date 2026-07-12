"""Collect public reputation data for hospital-associated practices across review platforms."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

from .analyzer import _get_client, _MODEL
from .data import places
from .db import get_connection

_PLATFORMS = ["healthgrades", "vitals", "webmd", "yelp", "ratemds"]

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "fbclid", "gclid", "msclkid",
    "mc_cid", "mc_eid", "ref", "referrer",
})


def _strip_tracking(url: Optional[str]) -> Optional[str]:
    """Remove tracking query parameters from a URL; return None if url is falsy."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        clean_qs = urlencode(
            [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _TRACKING_PARAMS]
        )
        return urlunparse(parsed._replace(query=clean_qs))
    except Exception:
        return url

_REPUTATION_TOOL = {
    "name": "submit_reputation_data",
    "description": (
        "Submit the reputation data found for each practice across healthcare "
        "review platforms. Use null for any rating or count you cannot confirm."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "practices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "healthgrades_rating": {"type": ["number", "null"]},
                        "healthgrades_count":  {"type": ["integer", "null"]},
                        "healthgrades_url":    {"type": ["string", "null"]},
                        "vitals_rating":       {"type": ["number", "null"]},
                        "vitals_count":        {"type": ["integer", "null"]},
                        "vitals_url":          {"type": ["string", "null"]},
                        "webmd_rating":        {"type": ["number", "null"]},
                        "webmd_count":         {"type": ["integer", "null"]},
                        "webmd_url":           {"type": ["string", "null"]},
                        "yelp_rating":         {"type": ["number", "null"]},
                        "yelp_count":          {"type": ["integer", "null"]},
                        "yelp_url":            {"type": ["string", "null"]},
                        "ratemds_rating":      {"type": ["number", "null"]},
                        "ratemds_count":       {"type": ["integer", "null"]},
                        "ratemds_url":         {"type": ["string", "null"]},
                        "affiliation_verified": {"type": "boolean"},
                    },
                    "required": ["name", "affiliation_verified"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["practices"],
        "additionalProperties": False,
    },
}


def _weighted_average(
    pairs: list[tuple[float, int]],
) -> tuple[Optional[float], int]:
    """Review-count-weighted average rating. Returns (avg, total_count)."""
    valid = [(r, c) for r, c in pairs if r is not None and c and c > 0]
    if not valid:
        return None, 0
    total = sum(c for _, c in valid)
    avg = round(sum(r * c for r, c in valid) / total, 1)
    return avg, total


def collect_platform_data(
    practices: list[dict],
    hospital_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> list[dict]:
    """
    Collect platform reputation data for each practice.

    Uses Google Places API for Google ratings and Claude structured extraction
    for Healthgrades, Vitals, WebMD, Yelp, and RateMDs.

    Returns enriched practice dicts with rating/count fields and computed averages.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    if not practices:
        return []

    emit({"type": "text", "text": f"Collecting platform reputation for {len(practices)} practice(s)…"})

    # ── Google Places for each practice ──────────────────────────────────────
    google_data: dict[str, tuple] = {}  # name → (rating, count, maps_url)
    for p in practices:
        try:
            read, _ = places.fetch_provider(p["name"], p.get("city") or city, p.get("state") or state)
            google_data[p["name"]] = (
                read.rating if read.verified else None,
                read.review_count if read.verified else None,
                _strip_tracking(read.maps_url) if read.verified else None,
            )
        except Exception:
            google_data[p["name"]] = (None, None, None)

    # ── Other platforms via Claude structured extraction ──────────────────────
    client = _get_client()
    practice_list = "\n".join(
        f"- {p['name']} ({p.get('city') or city}, {p.get('state') or state})"
        for p in practices
    )
    prompt = (
        f"You are a healthcare data analyst. For each practice listed below, "
        f"report what you know from your training data about their ratings and "
        f"review counts on Healthgrades, Vitals, WebMD, Yelp, and RateMDs.\n\n"
        f"These practices are associated with {hospital_name} in {city}, {state}.\n\n"
        f"Practices:\n{practice_list}\n\n"
        f"For each platform, provide the rating (1–5 scale, to one decimal) and "
        f"the review count if you have that data. Use null for any value you cannot "
        f"confirm. Do not fabricate data — null is always acceptable.\n\n"
        f"For affiliation_verified, set to true if you can confirm this practice is "
        f"part of {hospital_name}'s system, false if uncertain.\n\n"
        f"Call submit_reputation_data with your findings."
    )

    try:
        with client.messages.stream(
            model=_MODEL,
            max_tokens=4000,
            tools=[_REPUTATION_TOOL],
            tool_choice={"type": "tool", "name": "submit_reputation_data"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()

        platform_data: dict[str, dict] = {}
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_reputation_data":
                raw = block.input if isinstance(block.input, dict) else json.loads(block.input)
                for entry in raw.get("practices") or []:
                    name = entry.get("name", "")
                    platform_data[name] = entry
                break
    except Exception:
        platform_data = {}

    # ── Merge and compute aggregates ─────────────────────────────────────────
    today = date.today()
    results: list[dict] = []
    for p in practices:
        name = p["name"]
        pd = platform_data.get(name) or {}
        g_rating, g_count, g_url = google_data.get(name, (None, None, None))

        pairs: list[tuple] = []
        platforms_found: list[str] = []
        # Track (platform_key, count, url) to determine primary link fallback
        platform_entries: list[tuple[str, int, Optional[str]]] = []

        if g_rating is not None and g_count:
            pairs.append((g_rating, g_count))
            platforms_found.append("Google")
            platform_entries.append(("google", g_count, g_url))

        for platform in _PLATFORMS:
            r = pd.get(f"{platform}_rating")
            c = pd.get(f"{platform}_count")
            u = _strip_tracking(pd.get(f"{platform}_url"))
            if r is not None and c:
                pairs.append((float(r), int(c)))
                label = "WebMD" if platform == "webmd" else platform.capitalize()
                platforms_found.append(label)
                platform_entries.append((platform, int(c), u))

        avg_rating, total_reviews = _weighted_average(pairs)
        not_established = (len(platforms_found) == 0)

        # Primary URL: Google if available; otherwise highest-count platform with a URL
        primary_url: Optional[str] = g_url
        if primary_url is None and platform_entries:
            for _pk, _pc, _pu in sorted(platform_entries, key=lambda x: x[1], reverse=True):
                if _pu:
                    primary_url = _pu
                    break

        results.append({
            "practice_name":          name,
            "city":                   p.get("city") or city,
            "state":                  p.get("state") or state,
            "affiliation_verified":   bool(pd.get("affiliation_verified", True)),
            "google_rating":          g_rating,
            "google_count":           g_count,
            "google_url":             g_url,
            "healthgrades_rating":    pd.get("healthgrades_rating"),
            "healthgrades_count":     pd.get("healthgrades_count"),
            "healthgrades_url":       _strip_tracking(pd.get("healthgrades_url")),
            "vitals_rating":          pd.get("vitals_rating"),
            "vitals_count":           pd.get("vitals_count"),
            "vitals_url":             _strip_tracking(pd.get("vitals_url")),
            "webmd_rating":           pd.get("webmd_rating"),
            "webmd_count":            pd.get("webmd_count"),
            "webmd_url":              _strip_tracking(pd.get("webmd_url")),
            "yelp_rating":            pd.get("yelp_rating"),
            "yelp_count":             pd.get("yelp_count"),
            "yelp_url":               _strip_tracking(pd.get("yelp_url")),
            "ratemds_rating":         pd.get("ratemds_rating"),
            "ratemds_count":          pd.get("ratemds_count"),
            "ratemds_url":            _strip_tracking(pd.get("ratemds_url")),
            "primary_url":            primary_url,
            "avg_rating":             avg_rating,
            "total_reviews":          total_reviews,
            "platforms_found":        len(platforms_found),
            "platforms_list":         ", ".join(platforms_found),
            "platform_entries":       platform_entries,  # used by PDF renderer for per-platform links
            "not_established":        not_established,
            "collection_date":        today.isoformat(),
        })

    # Sort by total_reviews descending; not-established last
    results.sort(key=lambda r: (r["not_established"], -(r["total_reviews"] or 0)))
    emit({"type": "text", "text": f"Practice reputation collection complete."})
    return results


def save_practice_reputation(run_id: str, practices: list[dict]) -> str:
    """Persist practice reputation rows. Returns the rep_run_id."""
    if not practices:
        return ""
    con = get_connection()
    rep_run_id = str(uuid.uuid4())
    now = datetime.utcnow()
    expires = now + timedelta(days=90)

    con.execute(
        """INSERT INTO practice_reputation_runs
           (id, run_id, collected_at, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        [rep_run_id, run_id, now.isoformat(), expires.isoformat(), now.isoformat()],
    )

    for p in practices:
        con.execute(
            """INSERT INTO practice_reputation_practices
               (id, rep_run_id, practice_name, city, state, affiliation_verified,
                google_rating, google_count, google_url,
                healthgrades_rating, healthgrades_count, healthgrades_url,
                vitals_rating, vitals_count, vitals_url,
                webmd_rating, webmd_count, webmd_url,
                yelp_rating, yelp_count, yelp_url,
                ratemds_rating, ratemds_count, ratemds_url,
                primary_url,
                avg_rating, total_reviews, platforms_found, platforms_list,
                not_established, collection_date, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                str(uuid.uuid4()), rep_run_id,
                p["practice_name"], p.get("city"), p.get("state"),
                p.get("affiliation_verified", True),
                p.get("google_rating"), p.get("google_count"), p.get("google_url"),
                p.get("healthgrades_rating"), p.get("healthgrades_count"), p.get("healthgrades_url"),
                p.get("vitals_rating"), p.get("vitals_count"), p.get("vitals_url"),
                p.get("webmd_rating"), p.get("webmd_count"), p.get("webmd_url"),
                p.get("yelp_rating"), p.get("yelp_count"), p.get("yelp_url"),
                p.get("ratemds_rating"), p.get("ratemds_count"), p.get("ratemds_url"),
                p.get("primary_url"),
                p.get("avg_rating"), p.get("total_reviews", 0),
                p.get("platforms_found", 0), p.get("platforms_list", ""),
                p.get("not_established", False), p.get("collection_date"),
                now.isoformat(),
            ],
        )

    con.close()
    return rep_run_id
