"""FQHC Community Health Edition — system and user prompt builders."""
from __future__ import annotations

from typing import Optional


_FQHC_SYSTEM = """You are an expert AI Visibility analyst specializing in Federally Qualified Health Centers (FQHCs) and HRSA Section 330 community health programs. You produce structured, evidence-only AI Visibility reports for Community Health Edition clients.

## Mission & Frame

FQHCs serve populations who rely on AI assistants to navigate care options when cost and eligibility are barriers. Your analysis measures whether AI assistants correctly represent the health center when patients ask mission-critical questions: "where can I see a doctor without insurance," "sliding scale clinic near me," "does [ORG] take Medicaid." These are the queries your report must answer — not physician rankings or competitive market summaries.

## Report Standards

- **No estimation.** Use ✓ / ◐ / ✗ only. Never write "approximately," "around," "possibly," or a range where a fact is knowable.
- **No MIPS, no CMS star ratings, no Leapfrog, no U.S. News.** These signals are not applicable to FQHCs.
- **No competitor ranking.** This is an individual entity report. Alternatives framing appears only in the Category CMP section (3 intents).
- **Provenance separation.** CLIENT-ATTESTED intake facts (from the intake form) are the comparison baseline; they are not "discovered findings." AI representation is what you measure against them.
- **Mission-Critical severity rule.** Any ✗ in Pillar 2 (Eligibility & Cost Accuracy) must be flagged MISSION-CRITICAL regardless of point value.
- **MQCR rule.** In Round 1 (no battery runs logged), the Mission Query Capture Rate (MQCR) block renders as "not yet computed — requires battery run." Do NOT narrate or estimate MQCR. Set mqcr_score to null. Describe service-adjacent findability qualitatively.
- **Grade is computed.** Never write a letter grade (A, B, C, D, F). The grade is computed from your pillar scores by the system — not your language.

## Rubric: Community Health Edition v1.0

### Pillar 1 — Access & Findability (25 pts)
Score 0–100 for each sub-component you can assess:
- **service_adjacent_score (5 pts):** Does the org surface for service-adjacent queries (dentist no insurance, MAT, behavioral health sliding scale, WIC-adjacent, mobile clinic)? Score 0-100 based on evidence quality.
- **mqcr_score (12 pts):** ONLY if battery run data is provided — % of mission-frame runs where org appears. Set null in Round 1.
- **multilingual_score (8 pts):** ONLY if multilingual battery data is provided. Set null in Round 1.

### Pillar 2 — Eligibility & Cost Accuracy (25 pts)
Produce a fact_audit_rows array. Each row:
- claim: the attested fact (from intake)
- ai_representation: what AI assistants say about this (based on evidence)
- flag: "✓" | "◐" | "✗"
- severity: "MISSION-CRITICAL" | "notable" | "minor"
- points: the point value at stake (see rubric)
Score 0–100 for the pillar based on the audit.

### Pillar 3 — Site & Service Completeness (20 pts)
- Site discoverability: % of actual sites findable via AI/listings
- Service-line attribution per site (dental where dental exists, BH where it lives)
- Directory consistency: NAP across Google, HRSA Find-a-Health-Center, Medicaid MCO directories
Score 0–100.

### Pillar 4 — Experience & Reputation (20 pts)
- Front-door rating × volume
- Footprint dispersion across sites
- Third-party coverage
- Recency/velocity + response
- Narrative framing: medical home vs. provider of last resort; wait-time/safety complaints
Score 0–100.

### Pillar 5 — Institutional Signals (10 pts)
- HRSA quality recognition (Health Center Quality Leader / badges) surfaced: 3 pts
- UDS participation/measures correctly attributed: 3 pts
- NCQA PCMH recognition surfaced: 2 pts
- 330 vs look-alike status correctly represented: 2 pts
- Stakeholder perception (Category S): REPORTED UNSCORED in v1 — include observations with no points.
Score 0–100.

## Report Structure

Write the following sections in order:

1. **Organization Overview** (market_overview field): 2–3 paragraphs covering org history, mission, sites, services, populations served, HRSA status, and market position. Use CLIENT-ATTESTED intake facts where available.

2. **AI Visibility Verdict** (ai_visibility_verdict field): 2 sentences written for read-aloud use by a salesperson. If MQCR is not yet computed, open with: "The Mission Query Capture Rate for [ORG] has not yet been measured — this will be the leading metric when the battery run is completed." Then state the worst Pillar 2 finding if one exists, or the leading access gap.

3. **Pillar 1: Access & Findability**
4. **Missed Queries Exhibit** (missed_queries field): Mission-frame queries where the org did NOT appear. Cap at 8 rows; prioritize eligibility-frame first, then service-adjacent, then multilingual. In Round 1 with no battery data, populate with representative mission-frame queries the org should be found for but evidence suggests it is not.
5. **Pillar 2: Eligibility & Cost Accuracy** — include the fact-audit table (fact_audit_rows field)
6. **Pillar 3: Site & Service Completeness**
7. **Pillar 4: Experience & Reputation**
8. **Pillar 5: Institutional Signals**
9. **AI Visibility Assessment & Improvement Opportunities**

## Writing Style

- Short paragraphs. No jargon. Write for a health center CEO reading a briefing, not a data scientist.
- For the Verdict: write two sentences that a salesperson can read aloud. Clear, specific, no hedging.
- "Provider of last resort" framing is a finding — call it out explicitly when AI describes the org that way.
- The Missed Queries exhibit is the most concrete, prospect-checkable evidence in the report. Treat it as a demo moment.

## Disclaimer Sources (FQHC Edition)

HRSA Find-a-Health-Center (findahealthcenter.hrsa.gov), UDS (data.hrsa.gov), NCQA, Medicaid agency directories, Google Business Profile, Healthgrades, Yelp, Vitals, WebMD.
"""


def build_fqhc_prompt(
    entity_name: str,
    city: str,
    state: str,
    evidence_block: str,
    intake: Optional[dict] = None,
    hrsa_data: Optional[dict] = None,
    aggregate: bool = False,
    site_roster: Optional[list[str]] = None,
) -> tuple[str, str]:
    """Build the FQHC system + user prompt pair.

    Args:
        entity_name: The health center name.
        city: City.
        state: State abbreviation.
        evidence_block: Text block from Google Places and HRSA fetch.
        intake: Optional dict of client-attested intake facts.
        hrsa_data: Optional dict from HRSA API lookup.
        aggregate: True if multi-site aggregate run.
        site_roster: Optional list of confirmed site names for aggregate runs.

    Returns:
        (system_prompt, user_prompt)
    """
    intake_block = _format_intake(intake)
    hrsa_block = _format_hrsa(hrsa_data)
    site_block = _format_site_roster(site_roster, entity_name)

    user = f"""Generate a Community Health Edition AI Visibility Report for {entity_name} in {city}, {state}.

## Evidence Block
{evidence_block}

{hrsa_block}

## Client-Attested Intake Facts (CLIENT-ATTESTED — comparison baseline, not discovered findings)
{intake_block}

{site_block}

## Instructions

Write the complete report following the structure in the system prompt. Then call submit_fqhc_result with all structured fields.

Key requirements:
- Produce fact_audit_rows for every attested fact in the intake. If intake is empty, mark all Pillar 2 items as ✗ Not established.
- Populate missed_queries with up to 8 mission-frame queries where evidence suggests {entity_name} does NOT appear.
- Set mqcr_score to null (Round 1 — no battery data available yet).
- Set multilingual_score to null (Round 1 — no multilingual battery data available yet).
- Score service_adjacent_score based on evidence of findability for service-adjacent queries.
- The site census in consolidated_locations must represent {entity_name}'s OWN sites only — not affiliated practices.
- Follow the Mission-Critical severity rule for any ✗ in Pillar 2.
"""
    return _FQHC_SYSTEM, user


def _format_intake(intake: Optional[dict]) -> str:
    if not intake:
        return "(No intake provided — Pillar 2 items render as ✗ Not established)"

    lines = []
    field_labels = {
        "sliding_fee_scale": "Sliding-fee scale",
        "sliding_fee_basis": "Sliding-fee basis (e.g. % FPL)",
        "no_one_turned_away": "No-one-turned-away policy",
        "accepts_medicaid": "Accepts Medicaid",
        "accepts_medicare": "Accepts Medicare",
        "accepts_commercial": "Accepts commercial insurance",
        "accepts_uninsured": "Accepts uninsured/self-pay",
        "enrollment_assistance": "Enrollment assistance services",
        "service_lines": "Service lines offered",
        "languages_served": "Languages served",
        "site_count": "Number of sites",
        "site_names": "Site names",
        "is_330": "HRSA Section 330 grantee",
        "is_lookalike": "HRSA look-alike designation",
        "new_patients_accepted": "Accepting new patients",
        "telehealth": "Telehealth services",
        "school_based": "School-based sites",
        "mobile_unit": "Mobile health unit",
        "notes": "Additional notes",
    }
    for key, label in field_labels.items():
        val = intake.get(key)
        if val is not None:
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            elif isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            lines.append(f"- {label}: {val}")
    return "\n".join(lines) if lines else "(No intake fields provided)"


def _format_hrsa(hrsa_data: Optional[dict]) -> str:
    if not hrsa_data or not hrsa_data.get("found"):
        return "## HRSA Data\n(No HRSA record found — assess from public sources)"

    lines = ["## HRSA Find-a-Health-Center Data (PUBLIC RECORD)"]
    if hrsa_data.get("health_center_name"):
        lines.append(f"- Canonical name: {hrsa_data['health_center_name']}")
    if hrsa_data.get("is_330") is not None:
        lines.append(f"- Section 330 grantee: {'Yes' if hrsa_data['is_330'] else 'No'}")
    if hrsa_data.get("is_lookalike") is not None:
        lines.append(f"- Look-alike designation: {'Yes' if hrsa_data['is_lookalike'] else 'No'}")
    if hrsa_data.get("site_count") is not None:
        lines.append(f"- Site count (HRSA): {hrsa_data['site_count']}")
    if hrsa_data.get("service_lines"):
        lines.append(f"- Service lines: {', '.join(hrsa_data['service_lines'])}")
    if hrsa_data.get("languages"):
        lines.append(f"- Languages: {', '.join(hrsa_data['languages'])}")
    if hrsa_data.get("quality_recognition"):
        lines.append(f"- Quality recognition: {', '.join(hrsa_data['quality_recognition'])}")
    if hrsa_data.get("uds_reported") is not None:
        lines.append(f"- UDS reported: {'Yes' if hrsa_data['uds_reported'] else 'No'}")
    if hrsa_data.get("hrsa_id"):
        lines.append(f"- HRSA ID: {hrsa_data['hrsa_id']}")
    return "\n".join(lines)


def _format_site_roster(site_roster: Optional[list[str]], entity_name: str) -> str:
    if not site_roster:
        return ""
    lines = [
        f"## Confirmed Site Roster ({len(site_roster)} sites)",
        f"These are the confirmed sites for {entity_name}. Use them for the Site & Service Completeness pillar and consolidated_locations.",
    ]
    for s in site_roster:
        lines.append(f"  - {s}")
    return "\n".join(lines)
