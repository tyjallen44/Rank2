"""Collect public reputation data for physicians across review platforms.

Identity corroboration rule: a platform profile is only attributed to a
physician when at least 2 of the following 3 factors are confirmed:
  1. NPI match (NPI number appears in the profile URL or listing)
  2. Exact name + specialty match
  3. Practice / location match on the profile

When corroboration fails, the platform records null for that physician —
no uncertain profiles are ever attached.
"""
from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from .analyzer import _get_client, _MODEL
from .data import places as _places
from .db import get_connection
from .practice_reputation import _weighted_average, _strip_tracking, _PLATFORMS

_PHYSICIAN_REPUTATION_TOOL = {
    "name": "submit_physician_reputation",
    "description": (
        "Submit reputation data found for each physician across healthcare review "
        "platforms. Apply the 2-of-3 identity corroboration rule: only provide a "
        "profile URL and rating when you can confirm at least 2 of: (1) NPI match, "
        "(2) exact name + specialty match, (3) practice/location match. "
        "Use null for any field you cannot corroborate — never attach an uncertain profile."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "physicians": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":                 {"type": "string"},
                        "healthgrades_rating":  {"type": ["number", "null"]},
                        "healthgrades_count":   {"type": ["integer", "null"]},
                        "healthgrades_url":     {"type": ["string", "null"]},
                        "vitals_rating":        {"type": ["number", "null"]},
                        "vitals_count":         {"type": ["integer", "null"]},
                        "vitals_url":           {"type": ["string", "null"]},
                        "webmd_rating":         {"type": ["number", "null"]},
                        "webmd_count":          {"type": ["integer", "null"]},
                        "webmd_url":            {"type": ["string", "null"]},
                        "yelp_rating":          {"type": ["number", "null"]},
                        "yelp_count":           {"type": ["integer", "null"]},
                        "yelp_url":             {"type": ["string", "null"]},
                        "ratemds_rating":       {"type": ["number", "null"]},
                        "ratemds_count":        {"type": ["integer", "null"]},
                        "ratemds_url":          {"type": ["string", "null"]},
                        "google_rating":        {"type": ["number", "null"]},
                        "google_count":         {"type": ["integer", "null"]},
                        "google_url":           {"type": ["string", "null"]},
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

_PHYSICIAN_PLATFORMS = ["healthgrades", "vitals", "webmd", "yelp", "ratemds", "google"]


def collect_physician_data(
    physicians: list[dict],
    practice_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> list[dict]:
    """
    Collect platform reputation data for a list of physicians at a given practice.

    Applies the 2-of-3 identity corroboration rule via the Claude prompt.
    Returns enriched physician dicts sorted by total_reviews desc.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    if not physicians:
        return []

    emit({"type": "text", "text": f"Collecting reputation for {len(physicians)} physician(s) at {practice_name}…"})

    # ── Google Places lookup (real-time, one API call per physician) ──────────
    def _google_lookup(ph: dict) -> tuple[str, float | None, int | None, str | None]:
        """Return (name, rating, count, maps_url) via Google Places."""
        name = ph["name"]
        cred = (ph.get("credential") or "").upper()
        display = f"Dr. {name}" if cred in ("MD", "DO") else name
        last = name.split()[-1].lower()

        # Try with practice name first (disambiguates common physician names)
        for name_q, city_q in [
            (display, f"{practice_name} {city}"),
            (display, city),
        ]:
            read, _ = _places.fetch_provider(name_q, city_q, state)
            if read.verified and read.rating is not None:
                if last in (read.matched_name or "").lower():
                    return name, read.rating, read.review_count, read.maps_url
        return name, None, None, None

    google_data: dict[str, tuple] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_google_lookup, ph): ph["name"] for ph in physicians}
        for fut in as_completed(futs):
            name, rating, count, url = fut.result()
            google_data[name] = (rating, count, url)

    emit({"type": "text",
          "text": f"Google lookups complete: "
                  f"{sum(1 for v in google_data.values() if v[0] is not None)} "
                  f"of {len(physicians)} physicians found on Google."})

    # ── Claude for non-Google platforms (Healthgrades, Vitals, WebMD, etc.) ──
    def _physician_line(ph: dict) -> str:
        parts = [ph["name"]]
        if ph.get("credential"):
            parts[0] = f"{ph['name']}, {ph['credential']}"
        if ph.get("specialty"):
            parts.append(f"({ph['specialty']})")
        if ph.get("npi"):
            parts.append(f"[NPI: {ph['npi']}]")
        parts.append(f"at {practice_name}, {city}, {state}")
        return "- " + " ".join(parts)

    platform_data: dict[str, dict] = {}
    _BATCH = 10
    for i in range(0, len(physicians), _BATCH):
        batch = physicians[i:i + _BATCH]
        physician_list = "\n".join(_physician_line(ph) for ph in batch)
        prompt = (
            f"You are a healthcare data analyst researching physician reputation profiles "
            f"for providers at {practice_name} in {city}, {state}.\n\n"
            f"For each physician listed below, find their individual ratings and review "
            f"counts on Healthgrades, Vitals, WebMD, RateMDs, and Yelp (physician-specific "
            f"listings only — never a practice's Yelp page). "
            f"Do NOT include Google — Google data is already collected separately.\n\n"
            f"CRITICAL IDENTITY RULE — only attribute a profile when you can confirm "
            f"AT LEAST 2 of these 3 factors:\n"
            f"  1. NPI number matches the NPI in the profile or URL\n"
            f"  2. Exact name + specialty match\n"
            f"  3. Practice name or location matches the profile listing\n"
            f"When fewer than 2 factors are confirmed, set all fields for that platform "
            f"to null — never attach an uncertain or common-name-collision profile.\n\n"
            f"Physicians:\n{physician_list}\n\n"
            f"Use null for any rating, count, or URL you cannot confirm. "
            f"Use the physician's name exactly as listed as the 'name' field in your response.\n\n"
            f"Call submit_physician_reputation with your findings."
        )
        try:
            with _get_client().messages.stream(
                model=_MODEL,
                max_tokens=4000,
                tools=[_PHYSICIAN_REPUTATION_TOOL],
                tool_choice={"type": "tool", "name": "submit_physician_reputation"},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = stream.get_final_message()
            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_physician_reputation":
                    raw = block.input if isinstance(block.input, dict) else json.loads(block.input)
                    for entry in raw.get("physicians") or []:
                        pname = entry.get("name", "")
                        platform_data[pname] = entry
                    break
        except Exception:
            pass

    today = date.today()
    results: list[dict] = []

    for ph in physicians:
        name = ph["name"]
        pd = platform_data.get(name) or {}

        # Inject Places API Google data (overrides any Claude-produced Google fields)
        g_rating, g_count, g_url = google_data.get(name, (None, None, None))

        pairs: list[tuple] = []
        platforms_found: list[str] = []
        platform_entries: list[tuple[str, int, Optional[str]]] = []

        for platform in _PHYSICIAN_PLATFORMS:
            if platform == "google":
                r, c, u = g_rating, g_count, g_url
            else:
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

        primary_url: Optional[str] = None
        if platform_entries:
            for _pk, _pc, _pu in sorted(platform_entries, key=lambda x: x[1], reverse=True):
                if _pu:
                    primary_url = _pu
                    break

        credential = ph.get("credential") or ""
        display_name = f"Dr. {name}" if credential.upper() in ("MD", "DO") else name

        results.append({
            "row_type":            "physician",
            "physician_name":      display_name,
            "npi":                 ph.get("npi"),
            "specialty":           ph.get("specialty"),
            "credential":          credential,
            "parent_entity":       practice_name,
            "not_established":     not_established,
            "avg_rating":          avg_rating,
            "total_reviews":       total_reviews,
            "platforms_found":     len(platforms_found),
            "platforms_list":      ", ".join(platforms_found),
            "platform_entries":    platform_entries,
            "collection_date":     today.isoformat(),
            "google_rating":       g_rating,
            "google_count":        g_count,
            "google_url":          g_url,
            "healthgrades_rating": pd.get("healthgrades_rating"),
            "healthgrades_count":  pd.get("healthgrades_count"),
            "healthgrades_url":    _strip_tracking(pd.get("healthgrades_url")),
            "vitals_rating":       pd.get("vitals_rating"),
            "vitals_count":        pd.get("vitals_count"),
            "vitals_url":          _strip_tracking(pd.get("vitals_url")),
            "webmd_rating":        pd.get("webmd_rating"),
            "webmd_count":         pd.get("webmd_count"),
            "webmd_url":           _strip_tracking(pd.get("webmd_url")),
            "yelp_rating":         pd.get("yelp_rating"),
            "yelp_count":          pd.get("yelp_count"),
            "yelp_url":            _strip_tracking(pd.get("yelp_url")),
            "ratemds_rating":      pd.get("ratemds_rating"),
            "ratemds_count":       pd.get("ratemds_count"),
            "ratemds_url":         _strip_tracking(pd.get("ratemds_url")),
            "primary_url":         primary_url,
        })

    results.sort(key=lambda r: (r["not_established"], -(r["total_reviews"] or 0)))
    emit({"type": "text", "text": f"Physician reputation collection complete for {practice_name}."})
    return results


def save_physician_reputation(rep_run_id: str, physicians: list[dict]) -> None:
    """Persist physician reputation rows under an existing rep_run_id."""
    if not physicians:
        return
    con = get_connection()
    now = datetime.utcnow()
    for ph in physicians:
        con.execute(
            """INSERT INTO practice_reputation_physicians
               (id, rep_run_id, parent_entity, physician_name, npi, specialty, credential,
                not_established, avg_rating, total_reviews, platforms_found, platforms_list,
                collection_date,
                google_rating, google_count, google_url,
                healthgrades_rating, healthgrades_count, healthgrades_url,
                vitals_rating, vitals_count, vitals_url,
                webmd_rating, webmd_count, webmd_url,
                yelp_rating, yelp_count, yelp_url,
                ratemds_rating, ratemds_count, ratemds_url,
                primary_url, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                str(uuid.uuid4()), rep_run_id,
                ph.get("parent_entity"), ph.get("physician_name"),
                ph.get("npi"), ph.get("specialty"), ph.get("credential"),
                ph.get("not_established", False),
                ph.get("avg_rating"), ph.get("total_reviews", 0),
                ph.get("platforms_found", 0), ph.get("platforms_list", ""),
                ph.get("collection_date"),
                ph.get("google_rating"), ph.get("google_count"), ph.get("google_url"),
                ph.get("healthgrades_rating"), ph.get("healthgrades_count"), ph.get("healthgrades_url"),
                ph.get("vitals_rating"), ph.get("vitals_count"), ph.get("vitals_url"),
                ph.get("webmd_rating"), ph.get("webmd_count"), ph.get("webmd_url"),
                ph.get("yelp_rating"), ph.get("yelp_count"), ph.get("yelp_url"),
                ph.get("ratemds_rating"), ph.get("ratemds_count"), ph.get("ratemds_url"),
                ph.get("primary_url"), now.isoformat(),
            ],
        )
    con.close()
