"""Account access: indicators and presets.

An *indicator* is one named capability — either a report type the account may run or a
run option on a form. A *preset* is a named bundle: the report indicators the account has,
and for every option whether it starts ON, OFF or is HIDDEN on the form (the user can still
toggle ON/OFF options). Accounts with no preset and no indicator assignment are
unrestricted, exactly as before this existed — nobody existing loses access.

Admin → Users assigns a preset and/or ticks individual report indicators per account.
Per-user indicators replace the preset's report list; options always come from the preset.
"""
from __future__ import annotations

import json
from typing import Optional

# Report-type indicators (id, label, group). "history" is always available.
REPORTS: list[tuple[str, str, str]] = [
    ("deep_hospital",      "Deep Diagnostic — Hospital",                 "Deep Diagnostic"),
    ("deep_practice",      "Deep Diagnostic — Specialty Practice",       "Deep Diagnostic"),
    ("deep_service_line",  "Deep Diagnostic — Hospital Service Line",    "Deep Diagnostic"),
    ("deep_community",     "Deep Diagnostic — Community Health (FQHC)",  "Deep Diagnostic"),
    ("network",            "Hospital Network",                           "Reports"),
    ("compare",            "Compare Two",                                "Reports"),
    ("rankings",           "Competitors Rankings",                       "Reports"),
    ("student_health",     "Student Health Clinics (Rankings)",          "Reports"),
    ("events",             "Event Preparation (bulk from a spreadsheet)", "Batch"),
    ("ai_access_scan",     "AI Website Access Scan",                     "Batch"),
    ("trends",             "Trends",                                     "Tracking"),
]
REPORT_IDS = [r[0] for r in REPORTS]

# Run-option indicators (id, label, which forms carry them).
OPTIONS: list[tuple[str, str, str]] = [
    ("spotcheck",           "Ask the AI assistants what they actually say",      "Deep Diagnostic"),
    ("physician_composite", "Physician composite (hospital Practice Composite)",  "Deep Diagnostic — Hospital"),
    ("practice_composite",  "Practice Composite (hospital)",                      "Deep Diagnostic — Hospital"),
    ("owner_facts",         "Owner-attested facts",                               "Deep Diagnostic — Practice"),
    ("custom_title",        "Custom report title",                                "Deep Diagnostic"),
    ("teaser",              "Teaser report",                                      "Every report"),
    ("zip_search",          "Search by ZIP code",                                 "Deep Diagnostic"),
    ("skip_pdf",            "Skip PDF (data pull)",                               "Deep Diagnostic (admin)"),
    ("force_rerun",         "Force re-run (ignore cache)",                        "Every report (admin)"),
]
OPTION_IDS = [o[0] for o in OPTIONS]
OPTION_STATES = ("on", "off", "hidden")

PRESETS: dict[str, dict] = {
    "association_specialty_practice": {
        "label": "Association — Specialty Practice",
        "description": ("Specialty Practice Deep Diagnostics only, every report produced the same way: AI assistant "
                        "spot check on, physician table on, content analysis with drafted prescriptions, teaser and "
                        "ZIP search hidden. History shows the association's own reports."),
        "reports": ["deep_practice"],
        "options": {
            "spotcheck": "on", "physician_composite": "on", "owner_facts": "off", "custom_title": "off",
            "teaser": "hidden", "zip_search": "hidden", "practice_composite": "hidden",
            "skip_pdf": "hidden", "force_rerun": "hidden",
        },
        "history_scope": "preset",
    },
    "association_hospital_network": {
        "label": "Association — Hospital Network",
        "description": "Hospital Network reports only (base + Full Detail), teaser hidden. History scoped to the association.",
        "reports": ["network"],
        "options": {"teaser": "hidden", "force_rerun": "hidden"},
        "history_scope": "preset",
    },
}


def catalog() -> dict:
    """What Admin → Users shows: every indicator and every preset."""
    return {
        "reports": [{"id": i, "label": l, "group": g} for i, l, g in REPORTS],
        "options": [{"id": i, "label": l, "forms": f} for i, l, f in OPTIONS],
        "presets": [{"id": k, **v} for k, v in PRESETS.items()],
    }


def _parse_indicators(raw) -> Optional[list[str]]:
    if raw is None or raw == "":
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(v, list):
        return None
    return [str(x) for x in v if str(x) in REPORT_IDS]


def capabilities(user: Optional[dict]) -> dict:
    """The access an account has. `user` is the users-table row (or None for a password
    session). Unrestricted unless a preset or indicator list is assigned."""
    preset_id = (user or {}).get("preset") or None
    preset = PRESETS.get(preset_id) if preset_id else None
    indicators = _parse_indicators((user or {}).get("indicators_json"))
    groups = _parse_groups((user or {}).get("groups_json"))
    # Assigned groups: the account sees those groups only, cannot create groups, and lands on its group.
    # Report access is still the preset's / indicators'.
    scope = {"groups": groups, "groups_only": bool(groups)}
    if preset is None and indicators is None:
        return {"preset": None, "unrestricted": True, "reports": {r: True for r in REPORT_IDS},
                "options": {}, "history_scope": "all", **scope}
    allowed = set(indicators if indicators is not None else (preset or {}).get("reports", []))
    return {
        "preset": preset_id if preset else None,
        "preset_label": (preset or {}).get("label"),
        "unrestricted": False,
        "reports": {r: (r in allowed) for r in REPORT_IDS},
        "options": dict((preset or {}).get("options", {})),
        "history_scope": (preset or {}).get("history_scope", "all"),
        **scope,
    }


def _parse_groups(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


def report_allowed(caps: dict, report_id: str) -> bool:
    return bool(caps.get("unrestricted")) or bool((caps.get("reports") or {}).get(report_id))


def deep_report_id(entity_type: Optional[str], service_line: Optional[str] = None) -> str:
    et = (entity_type or "hospital")
    if et == "community_health":
        return "deep_community"
    if et == "service_line" or (et == "practice" and service_line):
        return "deep_service_line"
    if et == "practice":
        return "deep_practice"
    return "deep_hospital"
