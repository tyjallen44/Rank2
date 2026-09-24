"""Entity graph (heart-surgery item 5, first increment): one stored record per organization.

  org_graph      — the organization: type, market, specialty, flagship Google listing, website,
                   who confirmed it and when, and where the record came from
  org_locations  — every confirmed location with its Google place id (soft-deleted on removal)
  org_physicians — physicians with NPIs (from a confirmed physician roster)

Written wherever a user confirms a roster today (Deep Diagnostic form, Trends add, Manage
roster add/remove) and from finished runs. Read BEFORE discovery: the Deep Diagnostic and
Trends forms show the confirmed roster instantly and only discover when nothing is stored
(or on request). Discovery stays the fallback; the graph never blocks a run.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from .db import get_connection

SOURCES = ("deep_diagnostic", "trends", "roster_edit", "analysis", "seed")


def org_key(name: str, city: str | None, state: str | None) -> str:
    n = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    n = re.sub(r"\b(the|of|and|inc|llc|llp|pc|pa)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return f"{n}|{(city or '').strip().lower()}|{(state or '').strip().upper()}"


def init_graph(con=None) -> None:
    own = con is None
    con = con or get_connection()
    con.execute("""
        CREATE TABLE IF NOT EXISTS org_graph (
            org_key       VARCHAR PRIMARY KEY,
            name          VARCHAR NOT NULL,
            entity_type   VARCHAR NOT NULL DEFAULT 'practice',
            city          VARCHAR,
            state         VARCHAR,
            specialty     VARCHAR,
            anchor        VARCHAR,
            website       VARCHAR,
            source        VARCHAR,
            confirmed_by  VARCHAR,
            confirmed_at  TIMESTAMP,
            updated_at    TIMESTAMP NOT NULL
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS org_locations (
            id            VARCHAR PRIMARY KEY,
            org_key       VARCHAR NOT NULL,
            place_id      VARCHAR,
            name          VARCHAR NOT NULL,
            city          VARCHAR,
            state         VARCHAR,
            address       VARCHAR,
            rating        DOUBLE PRECISION,
            review_count  INTEGER,
            maps_url      VARCHAR,
            website       VARCHAR,
            source        VARCHAR,
            confirmed_by  VARCHAR,
            confirmed_at  TIMESTAMP NOT NULL,
            removed_at    TIMESTAMP,
            removed_by    VARCHAR
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS org_physicians (
            id            VARCHAR PRIMARY KEY,
            org_key       VARCHAR NOT NULL,
            npi           VARCHAR,
            name          VARCHAR NOT NULL,
            specialty     VARCHAR,
            credential    VARCHAR,
            source        VARCHAR,
            confirmed_by  VARCHAR,
            confirmed_at  TIMESTAMP NOT NULL,
            removed_at    TIMESTAMP
        )""")
    for stmt in ("CREATE INDEX IF NOT EXISTS idx_org_locations_key ON org_locations (org_key)",
                 "CREATE INDEX IF NOT EXISTS idx_org_physicians_key ON org_physicians (org_key)"):
        try:
            con.execute(stmt)
        except Exception:
            pass
    if own:
        con.close()


def _loc_key(loc: dict) -> str:
    if loc.get("place_id"):
        return "pid:" + str(loc["place_id"])
    return "nm:" + re.sub(r"\s+", " ", str(loc.get("original_name") or loc.get("name") or "").lower()).strip() + "|" + str(loc.get("city") or "").lower()


def upsert_org(name: str, city: str, state: str, entity_type: str, *, specialty: str | None = None,
               anchor: dict | None = None, website: str | None = None, locations: list | None = None,
               physicians: list | None = None, source: str = "deep_diagnostic", by: str | None = None,
               replace_locations: bool = False) -> str:
    """Create or refresh the organization; add/refresh the given locations. With
    replace_locations=True the given list becomes the roster (others are soft-removed) —
    that is the confirmation semantics of the Deep Diagnostic and Trends forms. Physicians
    are added/refreshed by NPI or name. Returns the org_key."""
    key = org_key(name, city, state)
    now = datetime.utcnow()
    con = get_connection()
    try:
        init_graph(con)
        row = con.execute("SELECT org_key, confirmed_at FROM org_graph WHERE org_key = ?", [key]).fetchone()
        confirmed = source in ("deep_diagnostic", "trends", "roster_edit")
        # A finished analysis may refresh details of a confirmed roster but never add to it;
        # it may only add locations while the organization is still unconfirmed (candidates).
        allow_insert = confirmed or not (row and row[1])
        if row:
            sets = ["updated_at = ?"]; vals = [now]
            if specialty: sets.append("specialty = ?"); vals.append(specialty)
            if anchor: sets.append("anchor = ?"); vals.append(json.dumps(anchor))
            if website: sets.append("website = ?"); vals.append(website)
            if confirmed:
                sets += ["source = ?", "confirmed_by = ?", "confirmed_at = ?"]; vals += [source, by or "", now]
            if entity_type: sets.append("entity_type = ?"); vals.append(entity_type)
            con.execute(f"UPDATE org_graph SET {', '.join(sets)} WHERE org_key = ?", vals + [key])
        else:
            con.execute("""INSERT INTO org_graph (org_key, name, entity_type, city, state, specialty, anchor, website,
                                                  source, confirmed_by, confirmed_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        [key, name, entity_type or "practice", city, state, specialty, json.dumps(anchor) if anchor else None,
                         website, source, (by or "") if confirmed else None, now if confirmed else None, now])
        if locations is not None:
            existing = {}
            for r in con.execute("SELECT id, place_id, name, city, removed_at FROM org_locations WHERE org_key = ?", [key]).fetchall():
                existing[_loc_key({"place_id": r[1], "name": r[2], "city": r[3]})] = (r[0], r[4])
            seen = set()
            for loc in locations:
                if not (loc.get("name") or loc.get("original_name")):
                    continue
                lk = _loc_key(loc); seen.add(lk)
                vals = [loc.get("place_id") or None, loc.get("original_name") or loc.get("name"), loc.get("city") or city, loc.get("state") or state,
                        loc.get("address") or None, loc.get("rating"), loc.get("review_count"), loc.get("maps_url") or None, loc.get("website") or None]
                if lk in existing:
                    con.execute("""UPDATE org_locations SET place_id = COALESCE(?, place_id), name = ?, city = ?, state = ?,
                                       address = COALESCE(?, address), rating = COALESCE(?, rating), review_count = COALESCE(?, review_count),
                                       maps_url = COALESCE(?, maps_url), website = COALESCE(?, website), removed_at = NULL, removed_by = NULL
                                   """ + (", source = ?, confirmed_by = ?, confirmed_at = ?" if confirmed else "") + " WHERE id = ?",
                                vals + ([source, by or "", now] if confirmed else []) + [existing[lk][0]])
                elif allow_insert:
                    con.execute("""INSERT INTO org_locations (id, org_key, place_id, name, city, state, address, rating, review_count,
                                                             maps_url, website, source, confirmed_by, confirmed_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                [uuid.uuid4().hex[:12], key] + vals + [source, by or "", now])
            if replace_locations:
                for lk, (lid, removed) in existing.items():
                    if lk not in seen and removed is None:
                        con.execute("UPDATE org_locations SET removed_at = ?, removed_by = ? WHERE id = ?", [now, by or source, lid])
        if physicians:
            have = {(r[1] or "") + "|" + (r[2] or "").lower(): r[0] for r in
                    con.execute("SELECT id, npi, name FROM org_physicians WHERE org_key = ?", [key]).fetchall()}
            for ph in physicians:
                nm = (ph.get("name") or "").strip()
                if not nm:
                    continue
                pk = (ph.get("npi") or "") + "|" + nm.lower()
                if pk in have:
                    con.execute("UPDATE org_physicians SET specialty = COALESCE(?, specialty), credential = COALESCE(?, credential), removed_at = NULL WHERE id = ?",
                                [ph.get("specialty"), ph.get("credential"), have[pk]])
                else:
                    con.execute("""INSERT INTO org_physicians (id, org_key, npi, name, specialty, credential, source, confirmed_by, confirmed_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                [uuid.uuid4().hex[:12], key, ph.get("npi") or None, nm, ph.get("specialty"), ph.get("credential"), source, by or "", now])
    finally:
        con.close()
    return key


def get_org(name: str, city: str, state: str, *, confirmed_only: bool = True) -> dict | None:
    """The stored record, or None. Locations are returned in the shape the Deep Diagnostic and
    Trends forms use for a discovered sibling, so they drop straight into the roster."""
    key = org_key(name, city, state)
    con = get_connection()
    try:
        init_graph(con)
        row = con.execute("""SELECT name, entity_type, city, state, specialty, anchor, website, source, confirmed_by, confirmed_at, updated_at
                             FROM org_graph WHERE org_key = ?""", [key]).fetchone()
        if not row:
            return None
        org = {"org_key": key, "name": row[0], "entity_type": row[1], "city": row[2], "state": row[3], "specialty": row[4],
               "anchor": json.loads(row[5]) if row[5] else None, "website": row[6], "source": row[7],
               "confirmed_by": row[8], "confirmed_at": str(row[9])[:19] if row[9] else None, "updated_at": str(row[10])[:19] if row[10] else None}
        if confirmed_only and not org["confirmed_at"]:
            return None
        locs = con.execute("""SELECT place_id, name, city, state, address, rating, review_count, maps_url, website, source, confirmed_by, confirmed_at
                              FROM org_locations WHERE org_key = ? AND removed_at IS NULL ORDER BY confirmed_at ASC, name ASC""", [key]).fetchall()
        org["locations"] = [{"name": r[1], "original_name": r[1], "entity_type": "clinic", "city": r[2] or "", "state": r[3] or "",
                             "address": r[4] or "", "place_id": r[0], "rating": r[5], "review_count": r[6], "maps_url": r[7],
                             "website": r[8], "source": r[9], "confirmed_by": r[10], "confirmed_at": str(r[11])[:10] if r[11] else None,
                             "from_graph": True} for r in locs]
        phys = con.execute("SELECT npi, name, specialty, credential FROM org_physicians WHERE org_key = ? AND removed_at IS NULL ORDER BY name", [key]).fetchall()
        org["physicians"] = [{"npi": r[0], "name": r[1], "specialty": r[2], "credential": r[3]} for r in phys]
        return org
    finally:
        con.close()


def remove_location(name: str, city: str, state: str, loc: dict, by: str | None = None) -> bool:
    key = org_key(name, city, state)
    con = get_connection()
    try:
        init_graph(con)
        lk = _loc_key(loc)
        for r in con.execute("SELECT id, place_id, name, city FROM org_locations WHERE org_key = ? AND removed_at IS NULL", [key]).fetchall():
            if _loc_key({"place_id": r[1], "name": r[2], "city": r[3]}) == lk:
                con.execute("UPDATE org_locations SET removed_at = ?, removed_by = ? WHERE id = ?", [datetime.utcnow(), by or "", r[0]])
                return True
        return False
    finally:
        con.close()


def stats() -> dict:
    con = get_connection()
    try:
        init_graph(con)
        orgs = con.execute("SELECT COUNT(*), COUNT(confirmed_at) FROM org_graph").fetchone()
        locs = con.execute("SELECT COUNT(*) FROM org_locations WHERE removed_at IS NULL").fetchone()
        phys = con.execute("SELECT COUNT(*) FROM org_physicians WHERE removed_at IS NULL").fetchone()
        return {"organizations": orgs[0], "confirmed": orgs[1], "locations": locs[0], "physicians": phys[0]}
    finally:
        con.close()
