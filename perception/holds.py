"""Pending-verification hold list for physician records.

Physicians on this list are rendered as 'Pending verification' in reports
rather than showing rating data that may be unreliable due to identity
ambiguity (e.g. two records with the same surname but unclear attribution).

The hold list is JSON-based (holds.json in the same directory) so it can
be updated without a code change or YAML dependency.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_HOLDS_PATH = os.path.join(os.path.dirname(__file__), "holds.json")

_holds_cache: Optional[list[dict]] = None


def _load_holds() -> list[dict]:
    global _holds_cache
    if _holds_cache is None:
        try:
            with open(_HOLDS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _holds_cache = data.get("physician_holds") or []
        except Exception:
            _holds_cache = []
    return _holds_cache


def is_held(physician_name: str, entity: str = "") -> bool:
    """Return True if this physician record is on the pending-verification hold list.

    Matching is case-insensitive on physician_name.  If entity is provided,
    it must also match (case-insensitive) for the hold to apply — this lets the
    same physician name appear at different entities without false positives.
    """
    name_upper = physician_name.strip().upper()
    entity_lower = entity.strip().lower()
    for h in _load_holds():
        if h.get("physician", "").strip().upper() != name_upper:
            continue
        hold_entity = h.get("entity", "").strip().lower()
        if hold_entity and entity_lower and hold_entity != entity_lower:
            continue
        return True
    return False
