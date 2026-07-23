"""Web-search enrichment for FQHC intake prefill.

Makes a targeted Claude Haiku call with the native web-search tool to look up
publicly-available facts about a community health center that the HRSA locator
API does not carry: service lines, languages served, insurance acceptance,
enrollment assistance, and new-patient status.

Returns a dict suitable for merging into the /api/hrsa-prefill response.
Falls back gracefully to an empty dict on any error.
"""
from __future__ import annotations

import json
from typing import Any

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
_MAX_SEARCH_USES = 4
_MAX_TURNS = 6

_RECORD_TOOL: dict = {
    "name": "record_fqhc_facts",
    "description": (
        "Record verified facts about the community health center. "
        "Only include facts you found in the search results — leave fields null if not found."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "service_lines": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Services offered, e.g. ['Primary Care', 'Dental', 'Behavioral Health']",
            },
            "languages_served": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Languages served beyond English, e.g. ['Spanish', 'Somali']",
            },
            "accepts_medicaid": {"type": ["boolean", "null"]},
            "accepts_medicare": {"type": ["boolean", "null"]},
            "accepts_uninsured": {"type": ["boolean", "null"]},
            "enrollment_assistance": {
                "type": ["boolean", "null"],
                "description": "True if they help patients enroll in Medicaid/CHIP/marketplace plans",
            },
            "new_patients_accepted": {"type": ["boolean", "null"]},
        },
        "required": ["service_lines", "languages_served"],
    },
}

_SEARCH_TOOL: dict = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": _MAX_SEARCH_USES,
}

_SYSTEM = (
    "You are a research assistant helping to populate a health center intake form. "
    "Search the web to find verified, publicly-stated facts about the given community health center. "
    "Focus on their official website and HRSA resources. "
    "Only record facts clearly stated in the sources — do not infer or estimate. "
    "Call record_fqhc_facts once you have gathered what you can find."
)


def _build_user_prompt(entity_name: str, city: str, state: str) -> str:
    return (
        f"Look up {entity_name} in {city}, {state}.\n\n"
        "Find and record:\n"
        "1. Service lines (e.g. primary care, dental, behavioral health, WIC, vision, pharmacy)\n"
        "2. Languages served beyond English\n"
        "3. Whether they accept Medicaid\n"
        "4. Whether they accept Medicare\n"
        "5. Whether they accept uninsured patients\n"
        "6. Whether they offer enrollment assistance (Medicaid/CHIP/marketplace sign-up help)\n"
        "7. Whether they are currently accepting new patients\n\n"
        "Search their official website first. Then call record_fqhc_facts with what you found."
    )


def fetch(entity_name: str, city: str, state: str) -> dict:
    """Return a dict of FQHC facts gathered via web search.

    Keys (all optional / may be None):
        service_lines, languages_served, accepts_medicaid, accepts_medicare,
        accepts_uninsured, enrollment_assistance, new_patients_accepted
    """
    try:
        import anthropic
        from ..config import settings

        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None
        )
    except Exception:
        return {}

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_user_prompt(entity_name, city, state)}
    ]
    tools = [_SEARCH_TOOL, _RECORD_TOOL]

    for _ in range(_MAX_TURNS):
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                tools=tools,
                messages=messages,
            )
        except Exception:
            return {}

        # Collect any record_fqhc_facts call — that's our result
        tool_uses = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                if block.name == "record_fqhc_facts":
                    data = block.input if isinstance(block.input, dict) else {}
                    return _clean(data)
                tool_uses.append(block)

        if resp.stop_reason == "end_turn":
            break

        # For web_search tool results, the API handles them server-side and we
        # just continue the loop by appending the assistant turn and an empty
        # user acknowledgement so the model can proceed.
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in tool_uses:
                # web_search results are handled server-side; for any other
                # tool_use we return an empty result to unblock the model.
                if block.name != "web_search":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "{}",
                    })
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                # web_search only — model should continue on its own
                messages.append({"role": "user", "content": "Continue."})

    return {}


def _clean(data: dict) -> dict:
    """Normalize the LLM output — strip empties, coerce types."""
    result: dict = {}

    svc = data.get("service_lines")
    if isinstance(svc, list):
        result["service_lines"] = [str(s).strip() for s in svc if s]

    langs = data.get("languages_served")
    if isinstance(langs, list):
        result["languages_served"] = [str(l).strip() for l in langs if l]

    for bool_key in ("accepts_medicaid", "accepts_medicare", "accepts_uninsured",
                     "enrollment_assistance", "new_patients_accepted"):
        val = data.get(bool_key)
        if val is not None:
            result[bool_key] = bool(val)

    return result
