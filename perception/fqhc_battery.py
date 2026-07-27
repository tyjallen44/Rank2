"""FQHC Mission Query Capture Rate (MQCR) battery engine.

Runs a 10-query standard battery through Claude + web_search to measure what
fraction of mission-frame patient queries surface the health center.

MQCR = surfaced_count / total_queries  (0.0–1.0)

Usage:
    result = run_battery(fqhc_run_id, entity_name, city, state, on_event=emit)
    # result.mqcr, result.surfaced_count, result.rows
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
_MAX_SEARCH_USES = 5
_MAX_TURNS = 8

# ── Stop-words excluded from entity-name significance scoring ─────────────────

_STOP = {
    "health", "center", "centers", "clinic", "clinics", "care", "medical",
    "community", "family", "primary", "services", "service", "associates",
    "group", "practice", "physicians", "medicine", "county", "city",
    "wellness", "institute", "foundation",
}

# ── Standard 10-query battery ─────────────────────────────────────────────────

STANDARD_BATTERY: list[dict] = [
    {
        "n": 1,
        "category": "general_affordable",
        "language": "en",
        "template": "Where can I find affordable primary care in {city}, {state}?",
    },
    {
        "n": 2,
        "category": "sliding_fee",
        "language": "en",
        "template": "Is there a sliding scale fee clinic in {city}, {state}?",
    },
    {
        "n": 3,
        "category": "uninsured",
        "language": "en",
        "template": "I don't have health insurance. Where can I get medical care in {city}?",
    },
    {
        "n": 4,
        "category": "low_cost",
        "language": "en",
        "template": "What are low-cost health clinics near {city}, {state}?",
    },
    {
        "n": 5,
        "category": "free_clinic",
        "language": "en",
        "template": "Are there any free or low-cost medical clinics in {city}, {state}?",
    },
    {
        "n": 6,
        "category": "medicaid",
        "language": "en",
        "template": "I just got Medicaid. What primary care doctors accept it in {city}?",
    },
    {
        "n": 7,
        "category": "community_health",
        "language": "en",
        "template": "Is there a community health center in {city}, {state}?",
    },
    {
        "n": 8,
        "category": "new_patient",
        "language": "en",
        "template": "I need a doctor accepting new patients who offers affordable care in {city}.",
    },
    {
        "n": 9,
        "category": "low_income_healthcare",
        "language": "en",
        "template": "What healthcare options are available for low-income residents in {city}, {state}?",
    },
    {
        "n": 10,
        "category": "behavioral_health",
        "language": "en",
        "template": "What are low-cost mental health services available in {city}, {state}?",
    },
]

_SYSTEM = (
    "You are a helpful AI assistant answering a patient's question about healthcare. "
    "Search the web and provide a specific, helpful answer listing real healthcare "
    "providers or clinics in the patient's area that match their needs. "
    "Include names, brief descriptions, and any relevant details about cost or eligibility."
)

# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class BatteryRow:
    id: str
    fqhc_run_id: str
    query: str
    language: str
    category: str
    assistant: str
    response_text: str
    surfaced: bool
    created_at: datetime


@dataclass
class BatteryResult:
    fqhc_run_id: str
    mqcr: float
    surfaced_count: int
    total: int
    rows: list[BatteryRow] = field(default_factory=list)


# ── Surfacing detection ───────────────────────────────────────────────────────


def _significant_tokens(entity_name: str) -> list[str]:
    """Return lowercase tokens from entity_name that are distinctive (not in stop set)."""
    words = re.findall(r"[a-z]+", entity_name.lower())
    return [w for w in words if len(w) >= 4 and w not in _STOP]


def is_surfaced(response_text: str, entity_name: str,
                aliases: list[str] | None = None) -> bool:
    """Return True if response_text appears to mention entity_name or any alias.

    Detection is intentionally broad: a facility counts as surfaced if ANY
    distinctive word from its name (or an alias) appears in the response.
    FQHCs often have a public brand name that differs from the HRSA canonical
    name; passing both as aliases catches either form.
    """
    text = response_text.lower()

    def _check_name(name: str) -> bool:
        name_lower = name.lower()

        # Exact substring
        if name_lower in text:
            return True

        # Any distinctive token (>= 4 chars, not generic) present in response
        tokens = _significant_tokens(name)
        if tokens and any(tok in text for tok in tokens):
            return True

        # Acronym match (e.g. "NHC" for "Nevada Health Centers")
        words = re.findall(r"[a-z]+", name_lower)
        if len(words) >= 3:
            acronym = "".join(w[0] for w in words)
            if len(acronym) >= 3 and re.search(rf"\b{re.escape(acronym)}\b", text):
                return True

        return False

    if _check_name(entity_name):
        return True
    for alias in (aliases or []):
        if alias and _check_name(alias):
            return True
    return False


# ── Query runner ──────────────────────────────────────────────────────────────


def _run_query(client, query: str) -> str:
    """Run a single patient query through Claude + web_search. Returns response text."""
    from anthropic import APIError

    search_tool = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": _MAX_SEARCH_USES,
    }

    messages = [{"role": "user", "content": query}]

    for _ in range(_MAX_TURNS):
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                tools=[search_tool],
                messages=messages,
            )
        except APIError:
            return ""

        text_parts = [
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ]

        if resp.stop_reason == "end_turn":
            return "\n".join(text_parts)

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            # web_search is handled server-side; just continue
            messages.append({"role": "user", "content": "Continue."})
        else:
            return "\n".join(text_parts)

    return ""


# ── Battery runner ────────────────────────────────────────────────────────────


def run_battery(
    fqhc_run_id: str,
    entity_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable[[dict], None]] = None,
    aliases: list[str] | None = None,
) -> BatteryResult:
    """Run the 10-query MQCR battery and persist results.

    aliases: additional name forms to check for surfacing (e.g. HRSA canonical
    name, common abbreviations, site-level names). Any match counts.

    Emits events via on_event:
      {"type": "battery_query", "n": 1, "total": 10, "query": "...", "surfaced": bool}
      {"type": "battery_done",  "mqcr": 0.7, "surfaced_count": 7, "total": 10}

    Returns BatteryResult with mqcr, surfaced_count, total, and rows.
    """
    from .config import settings
    from .db import get_connection, init_db
    import anthropic

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    init_db()

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    assistant_label = "claude-haiku+web_search"

    rows: list[BatteryRow] = []
    surfaced_count = 0
    total = len(STANDARD_BATTERY)

    for spec in STANDARD_BATTERY:
        query = spec["template"].format(city=city, state=state)
        n = spec["n"]

        emit({"type": "battery_query_start", "n": n, "total": total, "query": query})

        response_text = _run_query(client, query)
        surfaced = is_surfaced(response_text, entity_name, aliases=aliases)
        if surfaced:
            surfaced_count += 1

        row = BatteryRow(
            id=str(uuid.uuid4()),
            fqhc_run_id=fqhc_run_id,
            query=query,
            language=spec["language"],
            category=spec["category"],
            assistant=assistant_label,
            response_text=response_text,
            surfaced=surfaced,
            created_at=datetime.utcnow(),
        )
        rows.append(row)

        with get_connection() as con:
            con.execute(
                """
                INSERT INTO fqhc_battery_runs
                    (id, fqhc_run_id, query, language, category, assistant, response_text, surfaced, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.id,
                    row.fqhc_run_id,
                    row.query,
                    row.language,
                    row.category,
                    row.assistant,
                    row.response_text,
                    row.surfaced,
                    row.created_at,
                ],
            )

        emit({
            "type": "battery_query",
            "n": n,
            "total": total,
            "query": query,
            "surfaced": surfaced,
            "category": spec["category"],
        })

    mqcr = surfaced_count / total if total > 0 else 0.0

    # Persist MQCR back to analysis_runs
    with get_connection() as con:
        con.execute(
            "UPDATE analysis_runs SET mqcr = ? WHERE run_id = ?",
            [mqcr, fqhc_run_id],
        )

    emit({
        "type": "battery_done",
        "mqcr": mqcr,
        "surfaced_count": surfaced_count,
        "total": total,
    })

    return BatteryResult(
        fqhc_run_id=fqhc_run_id,
        mqcr=mqcr,
        surfaced_count=surfaced_count,
        total=total,
        rows=rows,
    )
