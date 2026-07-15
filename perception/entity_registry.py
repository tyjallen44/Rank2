"""Entity registry — persist and retrieve the sibling roster for a practice anchor.

Wraps the practice_entity_registry DB table so that discover_practice_siblings()
returns a stable, reproducible entity list across back-to-back runs within the
90-day validity window.

Canonical display names are updated when collect_platform_data resolves a sibling
to a Google listing with a strong name match.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from .db import get_connection

_REGISTRY_TTL_DAYS = 90


def _anchor_key(entity_name: str, city: str, state: str) -> str:
    return f"{entity_name.strip().lower()}|{city.strip().lower()}|{state.strip().lower()}"


def get_registry_siblings(
    entity_name: str,
    city: str,
    state: str,
) -> Optional[list[dict]]:
    """Return cached siblings for this anchor, or None if absent/expired."""
    key = _anchor_key(entity_name, city, state)
    now = datetime.utcnow().isoformat()
    con = get_connection()
    rows = con.execute(
        """SELECT name, entity_type, city, state, canonical_name
           FROM practice_entity_registry
           WHERE anchor_key = ? AND expires_at > ?
           ORDER BY rowid ASC""",
        [key, now],
    ).fetchall()
    con.close()
    if not rows:
        return None
    return [
        {
            "name": r[4] or r[0],   # canonical_name when available, else registered name
            "entity_type": r[1],
            "city": r[2],
            "state": r[3],
        }
        for r in rows
    ]


def save_registry_siblings(
    entity_name: str,
    city: str,
    state: str,
    siblings: list[dict],
) -> None:
    """Persist sibling roster for this anchor (replaces any prior unexpired entries)."""
    key = _anchor_key(entity_name, city, state)
    now = datetime.utcnow()
    expires = now + timedelta(days=_REGISTRY_TTL_DAYS)
    con = get_connection()
    # Delete stale entries for this anchor before inserting fresh ones.
    con.execute("DELETE FROM practice_entity_registry WHERE anchor_key = ?", [key])
    for s in siblings:
        if not s.get("name"):
            continue
        con.execute(
            """INSERT INTO practice_entity_registry
               (id, anchor_key, name, entity_type, city, state, canonical_name,
                registered_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            [
                str(uuid.uuid4()), key,
                s["name"], s.get("entity_type", "practice"),
                s.get("city", ""), s.get("state", ""),
                now.isoformat(), expires.isoformat(),
            ],
        )
    con.close()


def update_canonical_name(
    entity_name: str,
    city: str,
    state: str,
    registered_name: str,
    canonical_name: str,
) -> None:
    """Store the GBP-confirmed display name for a sibling in the registry.

    Called by collect_platform_data after a strong name match so the next
    registry read returns the listing name rather than the LLM-generated name.
    """
    key = _anchor_key(entity_name, city, state)
    con = get_connection()
    con.execute(
        """UPDATE practice_entity_registry
           SET canonical_name = ?
           WHERE anchor_key = ? AND LOWER(name) = LOWER(?)""",
        [canonical_name, key, registered_name],
    )
    con.close()


def expire_registry(entity_name: str, city: str, state: str) -> None:
    """Immediately expire registry entries for this anchor (used by force_rerun)."""
    key = _anchor_key(entity_name, city, state)
    con = get_connection()
    con.execute(
        "DELETE FROM practice_entity_registry WHERE anchor_key = ?", [key]
    )
    con.close()


def get_cached_gbp(
    entity_name: str,
    city: str,
    state: str,
    sibling_name: str,
) -> Optional[dict]:
    """Return cached GBP data for a sibling, or None if no cached binding exists.

    Returns a dict with keys: place_id, rating, count, url.
    Only returns data when place_id is set — a partial entry (canonical_name only)
    is not sufficient to reconstruct the binding.
    """
    key = _anchor_key(entity_name, city, state)
    now = datetime.utcnow().isoformat()
    con = get_connection()
    row = con.execute(
        """SELECT place_id, cached_rating, cached_count, cached_url
           FROM practice_entity_registry
           WHERE anchor_key = ? AND LOWER(name) = LOWER(?) AND expires_at > ?
             AND place_id IS NOT NULL""",
        [key, sibling_name, now],
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"place_id": row[0], "rating": row[1], "count": row[2], "url": row[3]}


def set_cached_gbp(
    entity_name: str,
    city: str,
    state: str,
    sibling_name: str,
    place_id: str,
    rating: Optional[float],
    count: Optional[int],
    url: Optional[str],
) -> None:
    """Write the GBP place_id and rating back to the registry entry for a sibling.

    Called after a confirmed strong-match GBP fetch so that subsequent runs can
    use the cached binding instead of re-fetching (durable identity, A-1).
    """
    key = _anchor_key(entity_name, city, state)
    now = datetime.utcnow().isoformat()
    con = get_connection()
    con.execute(
        """UPDATE practice_entity_registry
           SET place_id = ?, cached_rating = ?, cached_count = ?, cached_url = ?, cached_at = ?
           WHERE anchor_key = ? AND LOWER(name) = LOWER(?)""",
        [place_id, rating, count, url, now, key, sibling_name],
    )
    con.close()
