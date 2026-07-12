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


def discover_practices(
    entity_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> list[dict]:
    """
    Use Claude to discover practices and facilities associated with a health system.
    Returns a list of dicts: {name, entity_type, city, state}.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

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

    emit({"type": "text", "text": f"Found {len(practices)} associated practices."})
    return practices
