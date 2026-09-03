"""Discover practices and facilities associated with a hospital/health system."""
from __future__ import annotations

import json
from typing import Callable, Optional

from .analyzer import _get_client, _MODEL, _clean

_DISCOVER_TOOL = {
    "name": "submit_practice_roster",
    "description": (
        "Submit the discovered roster of practices and facilities associated "
        "with this hospital or health system."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "system_name": {
                "type": "string",
                "description": "Canonical name of the health system.",
            },
            "practices": {
                "type": "array",
                "description": "Associated practices and facilities.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "entity_type": {
                            "type": "string",
                            "enum": ["practice", "clinic", "hospital"],
                        },
                        "city": {"type": ["string", "null"]},
                        "state": {"type": ["string", "null"]},
                    },
                    "required": ["name", "entity_type"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["system_name", "practices"],
        "additionalProperties": False,
    },
}


_DETECT_TOOL = {
    "name": "submit_service_line_detection",
    "description": (
        "Report whether this entity is a specialty department / service line "
        "operated by a larger hospital or academic health system."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "is_service_line": {
                "type": "boolean",
                "description": (
                    "True only if this entity is a specialty department/service "
                    "line run by a LARGER hospital or academic health system "
                    "(e.g. 'Duke Orthopaedics' within Duke Health). An independent "
                    "single-specialty group with no hospital parent is False."
                ),
            },
            "parent_system": {"type": ["string", "null"],
                              "description": "The larger hospital/health system, e.g. 'Duke Health'."},
            "service_line": {"type": ["string", "null"],
                            "description": "The clinical service line/specialty, e.g. 'Orthopedics'."},
            "service_line_brand": {"type": ["string", "null"],
                                  "description": "Patient-facing brand of the service line, e.g. 'Duke Orthopaedics'."},
        },
        "required": ["is_service_line", "parent_system", "service_line", "service_line_brand"],
        "additionalProperties": False,
    },
}


def detect_service_line(
    entity_name: str, city: str, state: str,
    on_event: Optional[Callable] = None,
    specialty_hint: str = "",
) -> dict:
    """Detect whether `entity_name` is a specialty department / service line of a
    larger hospital or academic health system.

    `specialty_hint` (optional): the intended clinical service line (e.g.
    "Orthopedics"). When provided — e.g. from a CSV specialty column — it anchors
    the detection: treat `entity_name` as that service line of its parent hospital
    system if it plausibly operates one.

    Returns {is_service_line, parent_system, service_line, service_line_brand}.
    Fields other than is_service_line are "" when is_service_line is False.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    client = _get_client()
    _hint = (specialty_hint or "").strip()
    hint_line = (
        f"\n\nThe intended clinical service line is '{_hint}'. Treat '{entity_name}' as the "
        f"'{_hint}' service line of its parent hospital/health system when it plausibly operates "
        f"one — resolve the parent system and use '{_hint}' as the service line. Only set "
        f"is_service_line false if '{entity_name}' is an INDEPENDENT group with no larger hospital "
        f"parent, or a whole standalone hospital.\n"
        if _hint else ""
    )
    prompt = (
        f"Entity: '{entity_name}' in {city}, {state}.\n\n"
        "Determine whether this entity is a SPECIALTY DEPARTMENT or SERVICE LINE "
        "operated by a larger hospital or academic health system. For example, "
        "'Duke Orthopaedics' is the orthopedics service line of Duke Health, and "
        "'UNC Cardiology' is the cardiology service line of UNC Health. An "
        "INDEPENDENT single-specialty group (e.g. 'OrthoCarolina') is NOT a service "
        "line — it has no larger hospital parent. A whole standalone hospital is "
        "also NOT a service line."
        f"{hint_line}\n"
        "If it IS a service line, provide the parent system, the clinical service "
        "line, and the patient-facing brand of that service line. Otherwise set "
        "is_service_line false and the rest null.\n\n"
        "Call submit_service_line_detection."
    )
    with client.messages.stream(
        model=_MODEL,
        max_tokens=500,
        tools=[_DETECT_TOOL],
        tool_choice={"type": "tool", "name": "submit_service_line_detection"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_service_line_detection":
            data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    is_sl = bool(data.get("is_service_line"))
    emit({"type": "text",
          "text": (f"Detected service line: {data.get('service_line_brand')}"
                   if is_sl else "Not a hospital service line.")})
    return {
        "is_service_line": is_sl,
        "parent_system": _clean(data.get("parent_system") or "") if is_sl else "",
        "service_line": _clean(data.get("service_line") or "") if is_sl else "",
        "service_line_brand": _clean(data.get("service_line_brand") or "") if is_sl else "",
    }


def discover_service_line_siblings(
    entity_name: str, parent_system: str, service_line: str,
    city: str, state: str,
    on_event: Optional[Callable] = None,
    force_rerun: bool = False,
) -> tuple[list[dict], str]:
    """Discover ONLY the clinic locations of a specific service line within a
    parent hospital system (e.g. every orthopedic clinic of Duke Health) —
    excluding the parent hospital(s) and every OTHER service line.

    Returns (siblings, service_line_brand). Siblings EXCLUDE the anchor.
    Cached in the entity registry (90-day TTL) under a service-line-scoped key.
    """
    from .entity_registry import (
        get_registry_siblings, save_registry_siblings, expire_registry,
    )

    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    _cache_name = f"[service-line:{service_line}] {parent_system}"
    if force_rerun:
        expire_registry(_cache_name, city, state)
    else:
        cached = get_registry_siblings(_cache_name, city, state)
        if cached is not None:
            emit({"type": "text",
                  "text": f"Using registry: {len(cached)} {service_line} locations for {parent_system}"})
            return cached, ""

    client = _get_client()
    prompt = (
        f"Parent system: '{parent_system}'. Service line: '{service_line}'. "
        f"Known anchor location: '{entity_name}' in {city}, {state}.\n\n"
        f"List ALL patient-facing {service_line} clinic locations operated by "
        f"{parent_system} in the local market (within ~50 miles of {city}, {state}) "
        f"— DO NOT include '{entity_name}' itself.\n\n"
        "STRICT SCOPE:\n"
        f"- Include ONLY {service_line} clinics/offices of {parent_system}.\n"
        "- EXCLUDE the parent hospital(s) and medical center(s) themselves.\n"
        f"- EXCLUDE every OTHER service line (cardiology, primary care, oncology, "
        f"etc.) — only {service_line}.\n"
        "- Set entity_type to 'clinic' for each location.\n"
        f"- Set system_name to the patient-facing brand of this service line "
        f"(e.g. '{parent_system} {service_line}').\n\n"
        "If you cannot confidently identify additional locations, return an empty "
        "practices list. Call submit_practice_roster with your findings."
    )

    emit({"type": "text", "text": f"Discovering {service_line} locations for {parent_system}…"})
    with client.messages.stream(
        model=_MODEL,
        max_tokens=3000,
        tools=[_DISCOVER_TOOL],
        tool_choice={"type": "tool", "name": "submit_practice_roster"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    roster_data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_practice_roster":
            roster_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    brand = _clean(roster_data.get("system_name") or f"{parent_system} {service_line}")
    raw = roster_data.get("practices") or []
    siblings: list[dict] = []
    for p in raw:
        if not p.get("name"):
            continue
        siblings.append({
            "name": _clean(p["name"]),
            "entity_type": p.get("entity_type", "clinic"),
            "city": p.get("city") or city,
            "state": p.get("state") or state,
        })

    save_registry_siblings(_cache_name, city, state, siblings)
    emit({"type": "text", "text": f"Found {len(siblings)} {service_line} locations for {brand}."})
    return siblings, brand


def discover_practices(
    entity_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
    force_rerun: bool = False,
) -> list[dict]:
    """
    Use Claude to discover practices and facilities associated with a health system.
    Returns a list of dicts: {name, entity_type, city, state}.

    Results are cached in the entity registry for 90 days (same TTL as sibling
    discovery) so that hospital composite enumeration is stable across same-day
    runs.  Pass force_rerun=True to bypass the cache and call the LLM fresh.
    """
    from .entity_registry import get_registry_siblings, save_registry_siblings, expire_registry

    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    # Use a prefixed anchor_key to avoid collision with practice-sibling entries
    _cache_name = f"[hospital-composite] {entity_name}"

    if force_rerun:
        expire_registry(_cache_name, city, state)
    else:
        cached = get_registry_siblings(_cache_name, city, state)
        if cached is not None:
            emit({"type": "text",
                  "text": f"Using registry: {len(cached)} affiliated practices for {entity_name}"})
            return cached

    client = _get_client()
    prompt = (
        f"You are researching '{entity_name}' based in {city}, {state}.\n\n"
        "Using your knowledge of health system ownership structures, identify all "
        "practices, clinics, and facilities that this organization owns or operates:\n"
        "- Owned physician practices and specialty clinics\n"
        "- Urgent care clinics operating under the system brand\n"
        "- Ambulatory surgery centers owned by the system\n"
        "- Affiliated medical group practices\n\n"
        "Focus on the local market (within ~50 miles). "
        "Do NOT include independent referral partners.\n\n"
        "Call submit_practice_roster with your findings."
    )

    emit({"type": "text", "text": f"Discovering practices for {entity_name}…"})

    with client.messages.stream(
        model=_MODEL,
        max_tokens=3000,
        tools=[_DISCOVER_TOOL],
        tool_choice={"type": "tool", "name": "submit_practice_roster"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    roster_data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_practice_roster":
            roster_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    raw_practices = roster_data.get("practices") or []
    practices: list[dict] = []
    for p in raw_practices:
        if not p.get("name"):
            continue
        practices.append({
            "name": _clean(p["name"]),
            "entity_type": p.get("entity_type", "practice"),
            "city": p.get("city") or city,
            "state": p.get("state") or state,
        })

    save_registry_siblings(_cache_name, city, state, practices)
    emit({"type": "text", "text": f"Found {len(practices)} associated practices."})
    return practices


def discover_practice_siblings(
    entity_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
    force_rerun: bool = False,
) -> tuple[list[dict], str]:
    """
    Use Claude to discover sibling practices, clinics, and hospitals that share
    the same parent organization as this specialty practice.

    Returns (siblings, parent_org_name).
    siblings: sibling entities ONLY — the anchor is NOT included.
    parent_org_name: canonical parent org brand name (e.g. "Illinois Bone & Joint
      Institute"), or "" when served from cache (caller should use its own stored value).
    Returns ([], "") if no parent organization can be established.

    Results are persisted in the entity registry (90-day TTL).  Subsequent calls
    with the same anchor return the cached roster without calling the LLM.
    Pass force_rerun=True to bypass the cache and regenerate.
    """
    from .entity_registry import (
        get_registry_siblings,
        save_registry_siblings,
        expire_registry,
    )

    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    if force_rerun:
        expire_registry(entity_name, city, state)
    else:
        cached = get_registry_siblings(entity_name, city, state)
        if cached is not None:
            emit({"type": "text",
                  "text": f"Using registry: {len(cached)} affiliated entities for {entity_name}"})
            return cached, ""  # parent_org_name not cached; caller uses frontend-provided value

    client = _get_client()
    prompt = (
        f"You are researching '{entity_name}' based in {city}, {state}.\n\n"
        "Identify the parent organization that owns or operates this specialty practice, "
        "then list all SIBLING entities under that same parent — DO NOT include "
        f"'{entity_name}' itself in your list. Include:\n"
        "- Other clinic or practice locations of the same group\n"
        "- Sibling specialty clinics under the same parent org\n"
        "- Any hospitals owned by the same parent organization\n"
        "- Ambulatory surgery centers under the same ownership\n\n"
        "Focus on the local market (within ~50 miles). "
        "If this appears to be a truly independent solo practice with no parent "
        "organization, call submit_practice_roster with an empty practices list.\n\n"
        "Call submit_practice_roster with your findings."
    )

    emit({"type": "text", "text": f"Discovering affiliated entities for {entity_name}…"})

    with client.messages.stream(
        model=_MODEL,
        max_tokens=3000,
        tools=[_DISCOVER_TOOL],
        tool_choice={"type": "tool", "name": "submit_practice_roster"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    roster_data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_practice_roster":
            roster_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    parent_org_name = _clean(roster_data.get("system_name") or "")
    raw = roster_data.get("practices") or []
    siblings: list[dict] = []
    for p in raw:
        if not p.get("name"):
            continue
        siblings.append({
            "name": _clean(p["name"]),
            "entity_type": p.get("entity_type", "practice"),
            "city": p.get("city") or city,
            "state": p.get("state") or state,
        })

    save_registry_siblings(entity_name, city, state, siblings)
    emit({"type": "text", "text": f"Found {len(siblings)} affiliated entities for {parent_org_name or entity_name}."})
    return siblings, parent_org_name
