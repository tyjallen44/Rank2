"""Discover physicians associated with a practice entity."""
from __future__ import annotations

import json
from typing import Callable, Optional

from .analyzer import _get_client, _MODEL, _clean

_PHYSICIAN_DISCOVER_TOOL = {
    "name": "submit_physician_roster",
    "description": (
        "Submit the roster of physicians associated with this practice. "
        "Only include individual physicians you can corroborate via NPI registry "
        "or official provider directory listings. Do not include physicians whose "
        "association with this specific practice you cannot confirm."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "physicians": {
                "type": "array",
                "description": "Physicians affiliated with this practice.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":       {"type": "string", "description": "Full name without title prefix"},
                        "npi":        {"type": ["string", "null"], "description": "10-digit NPI if known"},
                        "specialty":  {"type": ["string", "null"]},
                        "credential": {"type": ["string", "null"], "description": "e.g. MD, DO, NP, PA"},
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


def discover_physicians(
    practice_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> list[dict]:
    """
    Use Claude to discover physicians affiliated with a practice.

    Returns list of dicts: {name, npi, specialty, credential}.
    Returns [] if no physicians can be confirmed.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    prompt = (
        f"You are researching the physician roster for '{practice_name}' "
        f"based in {city}, {state}.\n\n"
        "Using NPPES NPI registry records and publicly available provider "
        "directory information, list the physicians (MDs, DOs) and advanced "
        "practitioners (NPs, PAs) who practice at this location.\n\n"
        "For each provider include:\n"
        "- Full name (without title — the credential field captures that)\n"
        "- NPI number if you can confirm it from NPI registry data\n"
        "- Primary specialty\n"
        "- Credential (MD, DO, NP, PA, etc.)\n\n"
        "Only include providers you can confirm are affiliated with THIS specific "
        "practice location via NPI records or their public provider directory. "
        "If you cannot confirm the association, omit the provider.\n\n"
        "Call submit_physician_roster with your findings. "
        "An empty list is acceptable if you cannot confirm any providers."
    )

    emit({"type": "text", "text": f"Discovering physicians for {practice_name}…"})

    try:
        with _get_client().messages.stream(
            model=_MODEL,
            max_tokens=4000,
            tools=[_PHYSICIAN_DISCOVER_TOOL],
            tool_choice={"type": "tool", "name": "submit_physician_roster"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    except Exception:
        emit({"type": "text", "text": f"Physician discovery unavailable for {practice_name}."})
        return []

    roster_data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_physician_roster":
            roster_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    raw = roster_data.get("physicians") or []
    physicians: list[dict] = []
    for ph in raw:
        name = _clean(ph.get("name", ""))
        if not name:
            continue
        physicians.append({
            "name":       name,
            "npi":        ph.get("npi") or None,
            "specialty":  ph.get("specialty") or None,
            "credential": ph.get("credential") or None,
        })

    emit({"type": "text", "text": f"Found {len(physicians)} physician(s) for {practice_name}."})
    return physicians
