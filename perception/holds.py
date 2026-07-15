"""Config-driven pending-verification hold list for physician records.

Held records render "◐ Partial — identity verification pending" instead of
rating data.  The hold list is JSON so it can be updated without a code change.
Matching is case-insensitive.  If entity is supplied, it must also match; this
prevents the same physician name at a different entity from being blocked.
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
    """Return True if this physician record is on the pending-verification hold list."""
    name_upper  = physician_name.strip().upper()
    entity_lower = entity.strip().lower()
    for h in _load_holds():
        if h.get("physician", "").strip().upper() != name_upper:
            continue
        hold_entity = h.get("entity", "").strip().lower()
        if hold_entity and entity_lower and hold_entity != entity_lower:
            continue
        return True
    return False
