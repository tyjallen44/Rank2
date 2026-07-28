"""Network Pulse — system and user prompt builders for multi-state hospital network reports."""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Roster extraction
# ─────────────────────────────────────────────────────────────────────────────

_ROSTER_SYSTEM = """You are an expert at parsing healthcare facility directories and locations pages for large hospital networks.

Your task is to extract a clean roster of acute care hospitals from a network's website content.

## Inclusion criteria — include ONLY:
- Acute care hospitals with inpatient beds
- Critical access hospitals
- Children's hospitals that are inpatient facilities

## Exclusion criteria — exclude ALL of the following:
- Physician offices and medical group practices
- Urgent care centers (unless co-located with an inpatient hospital)
- Imaging / radiology centers
- Home health agencies
- Ambulatory surgery centers (ASCs)
- Behavioral health / psychiatric outpatient clinics (include only if they have inpatient beds)
- Outpatient clinics and community health centers
- Rehabilitation outpatient clinics
- Long-term care / skilled nursing facilities (unless part of a hospital campus)

When in doubt, exclude. The goal is a clean list of inpatient hospital facilities only.
Extract the network's canonical name from the page content if possible.
"""

_ROSTER_TOOL = {
    "name": "submit_hospital_roster",
    "description": (
        "Submit the extracted hospital roster from a network locations page. "
        "Include only acute care hospitals with inpatient beds. "
        "Call exactly once after reviewing all page content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "facilities": {
                "type": "array",
                "description": "List of acute care hospitals found on the page.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":  {"type": "string", "description": "Hospital name as listed on the page"},
                        "city":  {"type": "string", "description": "City"},
                        "state": {"type": "string", "description": "Two-letter state abbreviation"},
                        "beds":  {"type": ["integer", "null"], "description": "Licensed bed count if listed, else null"},
                    },
                    "required": ["name", "city", "state", "beds"],
                    "additionalProperties": False,
                },
            },
            "network_name": {
                "type": "string",
                "description": "Network's canonical name inferred from the page (e.g. 'Atrium Health')",
            },
            "total_found": {
                "type": "integer",
                "description": "Total number of inpatient hospitals included in the facilities array.",
            },
        },
        "required": ["facilities", "network_name", "total_found"],
        "additionalProperties": False,
    },
}


def build_roster_extraction_prompt(content: str, url: str) -> tuple[str, str]:
    """Build the system + user prompt pair for hospital roster extraction.

    Args:
        content: Page text content (from page.inner_text) or raw HTML snippet.
        url:     The URL of the locations page being parsed.

    Returns:
        (system_prompt, user_prompt)
    """
    user = f"""Extract the hospital roster from the following locations page.

Source URL: {url}

Page content:
---
{content[:80000]}
---

Review the content carefully and call submit_hospital_roster with:
- facilities: only acute care hospitals with inpatient beds (apply all exclusion criteria from your instructions)
- network_name: the network's canonical name as it appears on the page
- total_found: count of hospitals included

Apply the inclusion/exclusion criteria strictly. When in doubt, exclude.
"""
    return _ROSTER_SYSTEM, user


# ─────────────────────────────────────────────────────────────────────────────
# AI-based hospital discovery (primary roster method)
# ─────────────────────────────────────────────────────────────────────────────

_DISCOVERY_SYSTEM = """You are a healthcare industry expert with comprehensive knowledge of U.S. hospital networks and health systems.

Your task is to produce a complete, accurate roster of inpatient hospitals owned and operated by a named hospital network or health system.

## Inclusion criteria — include ONLY:
- Acute care hospitals with inpatient beds currently owned or operated by this network
- Critical access hospitals owned by this network
- Children's hospitals that are inpatient facilities within this network
- Specialty hospitals (cardiac, orthopedic, cancer) with inpatient beds if owned by this network

## Exclusion criteria — exclude ALL of the following:
- Physician offices and medical group practices
- Urgent care centers
- Imaging / radiology centers
- Home health agencies
- Ambulatory surgery centers
- Outpatient behavioral health clinics
- Outpatient clinics and community health centers
- Long-term care / skilled nursing facilities (unless part of an acute care hospital campus)
- Affiliated or partner hospitals that are NOT owned/operated by this network
- Former hospitals now divested or closed

Use your training knowledge plus web search to compile the most complete and current list possible.
If you are uncertain whether a facility qualifies, exclude it.
Be thorough — large health systems often have 20–100+ hospitals across multiple states.
"""

_DISCOVERY_TOOL = {
    "name": "submit_hospital_roster",
    "description": (
        "Submit the complete roster of inpatient hospitals owned and operated by the named network. "
        "Include only acute care facilities with inpatient beds. "
        "Call exactly once after compiling the full list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "facilities": {
                "type": "array",
                "description": "Complete list of acute care hospitals owned/operated by this network.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":  {"type": "string", "description": "Hospital name"},
                        "city":  {"type": "string", "description": "City"},
                        "state": {"type": "string", "description": "Two-letter state abbreviation"},
                        "beds":  {"type": ["integer", "null"], "description": "Approximate licensed bed count if known, else null"},
                    },
                    "required": ["name", "city", "state", "beds"],
                    "additionalProperties": False,
                },
            },
            "network_canonical_name": {
                "type": "string",
                "description": "The network's official/canonical name (e.g. 'Atrium Health')",
            },
            "total_found": {
                "type": "integer",
                "description": "Total hospital count in the facilities array.",
            },
            "confidence_note": {
                "type": "string",
                "description": "Brief note on data confidence — e.g. 'Based on training data as of early 2025; verify current ownership for recent acquisitions.'",
            },
        },
        "required": ["facilities", "network_canonical_name", "total_found"],
        "additionalProperties": False,
    },
}


def build_discovery_prompt(network_name: str, hq_location: str = "") -> tuple[str, str]:
    """Build the system + user prompt to discover hospitals by network name via AI knowledge.

    Returns:
        (system_prompt, user_prompt)
    """
    hq_line = f" headquartered in {hq_location}" if hq_location else ""
    user = f"""List all inpatient hospitals currently owned and operated by **{network_name}**{hq_line}.

Apply the inclusion and exclusion criteria from your instructions strictly.

Be thorough and complete — include all states and regions where {network_name} operates hospitals.
If {network_name} has recently made acquisitions or divestitures, include your best current knowledge and note any uncertainty in the confidence_note.

Call submit_hospital_roster with the complete roster.
"""
    return _DISCOVERY_SYSTEM, user


# ─────────────────────────────────────────────────────────────────────────────
# Network analysis
# ─────────────────────────────────────────────────────────────────────────────

_ANALYSIS_SYSTEM = """You are an expert AI Visibility analyst specializing in large multi-state hospital networks. Your clients are C-suite executives and network strategy teams at systems like Atrium Health, Advocate Health, CommonSpirit, and HCA Healthcare.

## What "AI Visibility" means for a hospital network

AI Visibility measures how well a hospital network and its member hospitals are represented in AI assistants (ChatGPT, Claude, Gemini, Perplexity, Bing AI) when patients and referring physicians ask questions like:
- "What hospitals does Atrium Health have?"
- "Does [Network] have a hospital in [State]?"
- "Tell me about [Network]"
- "Best hospital near me in [City]"
- "Which hospitals are part of [Network] in [State]?"

A high AI Visibility score means:
1. The network's brand is well-known and accurately described by AI assistants
2. Individual hospitals surface in local search queries and are correctly attributed to the parent network
3. Key facts (service lines, bed counts, capabilities, accreditations) are accurate in AI responses

## Three scoring dimensions

**Brand Visibility (40% weight)**
How well does AI represent the network as a whole? When asked "What is [Network]?" or "What hospitals are in [Network]?", does AI give an accurate, complete answer? Does it know the network's geography, scale, and flagship capabilities?

**Market Coverage (35% weight)**
For each state in the network's footprint, do the member hospitals surface in local queries? Score based on: (a) do hospitals appear when someone searches "[city] hospital" or "hospital near [city]"? (b) are they correctly linked to the parent network in those local results?

**Information Accuracy (25% weight)**
Are key facts correct in AI responses? This includes: service lines, bed counts, trauma levels, teaching status, accreditations, and whether the network's own hospitals are attributed correctly vs. confused with competitors or independent facilities.

## Scoring guidance

Score each dimension 0–100:
- 80–100: Excellent — AI responses are accurate, complete, and proactively attribute facilities to the network
- 65–79: Good — most facilities surface, occasional gaps in attribution or detail
- 50–64: Average — patchy coverage, some markets missing, brand often described without full roster
- 35–49: Below average — significant gaps, misattributions common, key facilities invisible locally
- 0–34: Poor — AI does not reliably represent this network; brand nearly invisible or badly wrong

## Facility assessment

For each facility, assess:
- ai_visibility_score (0–100): does it surface in local searches and appear in AI responses?
- surfaced_for_local: does it appear when searching "[city] hospital" or "hospital near [city]"?
- attributed_to_network: when it does appear, is it correctly linked to the parent network?
- key_gap: the single most actionable gap for this facility (one sentence)

## Writing style

- Executive audience. No jargon. Short, declarative sentences.
- Executive summary: exactly 3 sentences. Suitable for reading aloud at a board briefing.
- Strategic recommendations: prioritized by impact, not alphabetically. Each should be specific and actionable.
- Top markets / gap markets: name specific cities or states, not vague descriptions.
"""

_ANALYSIS_TOOL = {
    "name": "submit_network_result",
    "description": (
        "Submit the structured Network AI Visibility result. "
        "Call exactly once after completing the full analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "network_canonical_name": {
                "type": "string",
                "description": "Network's formal canonical name (e.g. 'Atrium Health')",
            },
            "executive_summary": {
                "type": "string",
                "description": (
                    "Exactly 3 sentences suitable for a board briefing. "
                    "Sentence 1: overall AI visibility posture. "
                    "Sentence 2: biggest gap or risk. "
                    "Sentence 3: highest-leverage opportunity."
                ),
            },
            "brand_visibility_score": {
                "type": "integer",
                "description": "Brand Visibility score 0–100 (40% weight in composite)",
            },
            "brand_visibility_narrative": {
                "type": "string",
                "description": "2–3 sentences on how AI represents this network's brand",
            },
            "market_coverage_score": {
                "type": "integer",
                "description": "Market Coverage score 0–100 (35% weight in composite)",
            },
            "market_coverage_narrative": {
                "type": "string",
                "description": "2–3 sentences on local surfacing across the network's geographic footprint",
            },
            "information_accuracy_score": {
                "type": "integer",
                "description": "Information Accuracy score 0–100 (25% weight in composite)",
            },
            "information_accuracy_narrative": {
                "type": "string",
                "description": "2–3 sentences on fact accuracy in AI responses",
            },
            "ai_visibility_score": {
                "type": "integer",
                "description": "Overall composite score 0–100 (computed from the three dimensions using weights 0.40/0.35/0.25)",
            },
            "grade": {
                "type": "string",
                "description": "Letter grade: A (80+), B (65–79), C (50–64), D (35–49), F (<35)",
            },
            "strategic_recommendations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3–5 strategic recommendations, prioritized by impact. Each is one complete sentence.",
                "minItems": 3,
                "maxItems": 5,
            },
            "top_markets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 3 best-performing markets (cities or states) — highest AI visibility",
                "minItems": 1,
                "maxItems": 3,
            },
            "gap_markets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 3 worst-performing gap markets (cities or states) — lowest AI visibility",
                "minItems": 1,
                "maxItems": 3,
            },
            "facility_assessments": {
                "type": "array",
                "description": "Per-facility AI visibility assessments, one entry per hospital.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":  {"type": "string"},
                        "city":  {"type": "string"},
                        "state": {"type": "string"},
                        "ai_visibility_score":    {"type": "integer", "description": "0–100"},
                        "surfaced_for_local":     {"type": "boolean"},
                        "attributed_to_network":  {"type": "boolean"},
                        "key_gap":                {"type": "string", "description": "Single most important gap for this facility"},
                    },
                    "required": [
                        "name", "city", "state", "ai_visibility_score",
                        "surfaced_for_local", "attributed_to_network", "key_gap",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "network_canonical_name",
            "executive_summary",
            "brand_visibility_score",
            "brand_visibility_narrative",
            "market_coverage_score",
            "market_coverage_narrative",
            "information_accuracy_score",
            "information_accuracy_narrative",
            "ai_visibility_score",
            "grade",
            "strategic_recommendations",
            "top_markets",
            "gap_markets",
            "facility_assessments",
        ],
        "additionalProperties": False,
    },
}


def build_network_analysis_prompt(
    network_name: str,
    hq_location: str,
    facilities: list[dict],
    source_url: str,
    google_data: dict | None = None,
) -> tuple[str, str]:
    """Build the system + user prompt pair for network AI visibility analysis.

    Args:
        network_name:  Network name (e.g. "Atrium Health").
        hq_location:   HQ city/state (e.g. "Charlotte, NC").
        facilities:    List of dicts with keys: name, city, state, beds (optional).
        source_url:    URL of the locations page used for roster extraction.
        google_data:   Dict keyed by lowercase facility name → {rating, review_count}.

    Returns:
        (system_prompt, user_prompt)
    """
    google_data = google_data or {}

    # Build facilities block (with Google ratings inline)
    if facilities:
        fac_lines = []
        for i, f in enumerate(facilities, 1):
            beds_str = f" ({f['beds']} beds)" if f.get("beds") else ""
            gd = google_data.get(f.get("name", "").lower(), {})
            if gd.get("rating") is not None:
                rating_str = f" | Google: {gd['rating']:.1f}★ ({gd.get('review_count') or 0} reviews)"
            else:
                rating_str = " | Google: no verified listing"
            fac_lines.append(f"  {i}. {f['name']} — {f['city']}, {f['state']}{beds_str}{rating_str}")
        facilities_block = "\n".join(fac_lines)
        states = sorted({f["state"] for f in facilities if f.get("state")})
        states_str = ", ".join(states)
        total = len(facilities)
    else:
        facilities_block = "(No facilities provided — assess from general knowledge of this network)"
        states_str = "unknown"
        total = 0

    # Compute network-level Google summary for the prompt header
    rated = [gd for gd in google_data.values() if gd.get("rating") is not None]
    if rated:
        avg_rating = sum(gd["rating"] for gd in rated) / len(rated)
        total_reviews = sum(gd.get("review_count") or 0 for gd in rated)
        coverage_pct = round(100 * len(rated) / max(total, 1))
        google_summary = (
            f"{len(rated)}/{total} facilities ({coverage_pct}%) have verified Google listings — "
            f"avg rating {avg_rating:.2f}★, {total_reviews:,} total reviews"
        )
    else:
        google_summary = "No verified Google listings found for this network's facilities"

    user = f"""Conduct a Network AI Visibility analysis for **{network_name}**.

## Network Profile
- Network name: {network_name}
- Headquarters: {hq_location}
- Roster source: {source_url}
- Total hospitals assessed: {total}
- States in footprint: {states_str}
- Google presence summary: {google_summary}

## Hospital Roster (with verified Google ratings)
{facilities_block}

## Analysis Instructions

Assess AI Visibility across three dimensions:

### 1. Brand Visibility (40% weight)
Evaluate: when AI assistants are asked about {network_name} by name — "What hospitals does {network_name} have?", "Tell me about {network_name}", "Does {network_name} have a hospital in [state]?" — how accurately and completely do they respond?
- Does AI know the network's scale and geographic footprint?
- Does it correctly enumerate key member hospitals?
- Is the network's brand positioning accurately reflected?

**Also factor in the verified Google ratings above.** Google reviews are a direct signal of brand visibility — hospitals with high review volumes and strong ratings are more likely to appear prominently in AI-assisted searches. Consider:
- What percentage of facilities have verified Google listings?
- Is the average rating strong (≥4.0★), adequate (3.5–3.9★), or weak (<3.5★)?
- Are there facilities with very few reviews that represent brand visibility gaps?
- Do ratings vary significantly across states or facility types within the network?

### 2. Market Coverage (35% weight)
For each state in the footprint ({states_str}), evaluate whether {network_name}'s hospitals surface in local queries:
- When patients search "[city] hospital" or "hospital near me in [city]", do {network_name} facilities appear?
- Are they correctly attributed to {network_name} (not listed as independent)?
- Which states/markets have strong coverage vs. significant gaps?

### 3. Information Accuracy (25% weight)
Are key facts correct when AI discusses {network_name} or its hospitals?
- Service lines and clinical capabilities
- Hospital sizes and bed counts (where known)
- Trauma levels and teaching status
- Accreditations and quality designations
- Correct affiliation (hospitals attributed to {network_name} vs. confused with other systems)

### 4. Facility Assessments
For each of the {total} hospitals listed above, provide:
- ai_visibility_score (0–100): overall AI visibility for this specific facility
- surfaced_for_local: does it appear when searching "[city] hospital" or similar local queries?
- attributed_to_network: when it appears, is it correctly linked to {network_name}?
- key_gap: the single most important improvement for this hospital's AI visibility

### 5. Synthesis
- Write a 3-sentence executive_summary suitable for a board briefing
- Identify 3–5 strategic_recommendations prioritized by impact
- Name the top 3 best-performing markets (top_markets)
- Name the top 3 worst gap markets (gap_markets)

After completing your analysis, call submit_network_result with all structured fields.
"""
    return _ANALYSIS_SYSTEM, user
