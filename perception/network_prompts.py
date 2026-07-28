"""Network Pulse — system and user prompt builders for multi-state healthcare network reports."""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Facility type configuration
# ─────────────────────────────────────────────────────────────────────────────

FACILITY_TYPE_CONFIGS: dict[str, dict] = {
    "hospital": {
        "label":              "Hospital Network",
        "singular":           "hospital",
        "plural":             "hospitals",
        "local_query":        "[city] hospital",
        "network_query":      "What hospitals does [network] have?",
        "analysis_context":   "patients and referring physicians asking about hospital services",
        "quality_cols":       ["google", "cms", "leapfrog"],
        "discovery_include":  "acute care hospitals with inpatient beds currently owned or operated by this network",
        "discovery_exclude":  (
            "- Physician offices and medical group practices\n"
            "- Urgent care centers\n"
            "- Imaging / radiology centers\n"
            "- Home health agencies\n"
            "- Ambulatory surgery centers (ASCs)\n"
            "- Outpatient behavioral health clinics\n"
            "- Outpatient clinics and community health centers\n"
            "- Long-term care / skilled nursing facilities (unless part of an acute care hospital campus)\n"
            "- Affiliated or partner hospitals NOT owned/operated by this network\n"
            "- Former hospitals now divested or closed"
        ),
        "methodology_quality": (
            "Google Rating — verified via Google Places API (review count in parentheses).\n"
            "CMS Stars — CMS Overall Hospital Quality Star Rating (1–5★) from CMS Care Compare, "
            "fetched live via the public CMS Provider Data API.\n"
            "Leapfrog — Hospital Safety Grade (A–F) from The Leapfrog Group, published semi-annually; "
            "\"—\" indicates the facility did not participate in the current survey cycle.\n"
            "A facility with strong quality credentials (Leapfrog A, CMS 5★) but a low AI Score "
            "represents a high-priority visibility opportunity."
        ),
    },
    "asc": {
        "label":              "Ambulatory Surgery Centers",
        "singular":           "ASC",
        "plural":             "ambulatory surgery centers",
        "local_query":        "outpatient surgery center [city]",
        "network_query":      "What surgery centers does [network] operate?",
        "analysis_context":   "patients and referring physicians seeking outpatient surgical care",
        "quality_cols":       ["google"],
        "discovery_include":  "ambulatory surgery centers (ASCs) currently owned, operated, or managed by this company",
        "discovery_exclude":  (
            "- Inpatient hospitals\n"
            "- Urgent care walk-in clinics\n"
            "- Physician offices and independent practices\n"
            "- Imaging-only or radiology-only centers\n"
            "- Former or divested facilities"
        ),
        "methodology_quality": (
            "Google Rating — verified via Google Places API (review count in parentheses). "
            "For ASCs, Google review volume and rating are the primary available public quality signal "
            "and directly reflect patient-perceived experience."
        ),
    },
    "urgent_care": {
        "label":              "Urgent Care Network",
        "singular":           "urgent care location",
        "plural":             "urgent care locations",
        "local_query":        "urgent care [city]",
        "network_query":      "What urgent care locations does [network] have?",
        "analysis_context":   "patients seeking same-day non-emergency care",
        "quality_cols":       ["google"],
        "discovery_include":  "urgent care centers currently owned, operated, or managed by this company",
        "discovery_exclude":  (
            "- Inpatient hospitals and ERs\n"
            "- Physician offices and independent practices\n"
            "- ASCs and surgical centers\n"
            "- Former or divested locations"
        ),
        "methodology_quality": (
            "Google Rating — verified via Google Places API (review count in parentheses). "
            "For urgent care networks, Google rating and review volume are the primary patient-facing "
            "quality signals and the most common input to AI responses about local care access."
        ),
    },
    "imaging": {
        "label":              "Imaging / Radiology Network",
        "singular":           "imaging center",
        "plural":             "imaging centers",
        "local_query":        "MRI imaging center [city]",
        "network_query":      "What imaging centers does [network] operate?",
        "analysis_context":   "patients and physicians seeking diagnostic imaging services",
        "quality_cols":       ["google"],
        "discovery_include":  "imaging and radiology centers (MRI, CT, X-ray, mammography) currently owned, operated, or managed by this company",
        "discovery_exclude":  (
            "- Inpatient hospitals\n"
            "- ASCs and surgical centers\n"
            "- Physician offices without imaging equipment\n"
            "- Former or divested locations"
        ),
        "methodology_quality": (
            "Google Rating — verified via Google Places API (review count in parentheses). "
            "For imaging networks, Google rating reflects patient experience with scheduling, "
            "wait times, and staff quality — all factors AI assistants surface when recommending imaging centers."
        ),
    },
    "behavioral_health": {
        "label":              "Behavioral Health Network",
        "singular":           "behavioral health location",
        "plural":             "behavioral health locations",
        "local_query":        "mental health treatment [city]",
        "network_query":      "What mental health facilities does [network] operate?",
        "analysis_context":   "patients and families seeking mental health or substance use treatment",
        "quality_cols":       ["google"],
        "discovery_include":  "behavioral health, mental health, and substance use treatment facilities currently owned, operated, or managed by this company",
        "discovery_exclude":  (
            "- Inpatient hospitals (unless specifically behavioral health)\n"
            "- General primary care clinics\n"
            "- Former or divested locations"
        ),
        "methodology_quality": (
            "Google Rating — verified via Google Places API (review count in parentheses). "
            "For behavioral health networks, Google rating and review presence reflect both patient "
            "experience and digital discoverability — key factors in AI recommendation responses."
        ),
    },
    "other": {
        "label":              "Healthcare Network",
        "singular":           "location",
        "plural":             "locations",
        "local_query":        "[network] near [city]",
        "network_query":      "What locations does [network] operate?",
        "analysis_context":   "patients and healthcare consumers",
        "quality_cols":       ["google"],
        "discovery_include":  "healthcare facilities and locations currently owned, operated, or managed by this company",
        "discovery_exclude":  (
            "- Affiliated or partner locations not directly owned/operated\n"
            "- Former or divested locations"
        ),
        "methodology_quality": (
            "Google Rating — verified via Google Places API (review count in parentheses)."
        ),
    },
}


def get_facility_config(facility_type: str) -> dict:
    return FACILITY_TYPE_CONFIGS.get(facility_type, FACILITY_TYPE_CONFIGS["other"])


# ─────────────────────────────────────────────────────────────────────────────
# Roster extraction (from URL — facility type passed as context to AI)
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
    """Build the system + user prompt pair for hospital roster extraction."""
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
# AI-based facility discovery (primary roster method)
# ─────────────────────────────────────────────────────────────────────────────

_DISCOVERY_TOOL = {
    "name": "submit_hospital_roster",
    "description": (
        "Submit the complete roster of facilities owned and operated by the named network. "
        "Call exactly once after compiling the full list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "facilities": {
                "type": "array",
                "description": "Complete list of facilities owned/operated by this network.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":  {"type": "string", "description": "Facility name"},
                        "city":  {"type": "string", "description": "City"},
                        "state": {"type": "string", "description": "Two-letter state abbreviation"},
                        "beds":  {"type": ["integer", "null"], "description": "Bed count if applicable and known, else null"},
                    },
                    "required": ["name", "city", "state", "beds"],
                    "additionalProperties": False,
                },
            },
            "network_canonical_name": {
                "type": "string",
                "description": "The network's official/canonical name (e.g. 'SCA Health')",
            },
            "total_found": {
                "type": "integer",
                "description": "Total facility count in the facilities array.",
            },
            "confidence_note": {
                "type": "string",
                "description": "Brief note on data confidence — e.g. 'Based on training data as of early 2025; verify for recent acquisitions.'",
            },
        },
        "required": ["facilities", "network_canonical_name", "total_found"],
        "additionalProperties": False,
    },
}


def _build_discovery_system(cfg: dict) -> str:
    plural = cfg["plural"]
    include = cfg["discovery_include"]
    exclude = cfg["discovery_exclude"]
    return f"""You are a healthcare industry expert with comprehensive knowledge of U.S. healthcare networks and operators.

Your task is to produce a complete, accurate roster of {plural} owned and operated by a named network or company.

## Inclusion criteria — include ONLY:
- {include}

## Exclusion criteria — exclude ALL of the following:
{exclude}

Use your training knowledge to compile the most complete and current list possible.
If you are uncertain whether a facility qualifies, exclude it.
Be thorough — large operators often have 50–500+ locations across multiple states.
"""


def build_discovery_prompt(
    network_name: str,
    hq_location: str = "",
    facility_type: str = "hospital",
) -> tuple[str, str]:
    """Build the system + user prompt to discover facilities by network name via AI knowledge."""
    cfg = get_facility_config(facility_type)
    system = _build_discovery_system(cfg)
    hq_line = f" headquartered in {hq_location}" if hq_location else ""
    plural = cfg["plural"]
    user = f"""List all {plural} currently owned and operated by **{network_name}**{hq_line}.

Be thorough and complete — include all states and regions where {network_name} operates {plural}.
If {network_name} has recently made acquisitions or changes, include your best current knowledge and note any uncertainty in the confidence_note.

Call submit_hospital_roster with the complete roster.
"""
    return system, user


# ─────────────────────────────────────────────────────────────────────────────
# Network analysis
# ─────────────────────────────────────────────────────────────────────────────

def _build_analysis_system(cfg: dict) -> str:
    plural = cfg["plural"]
    singular = cfg["singular"]
    local_q = cfg["local_query"]
    network_q_template = cfg["network_query"]
    analysis_context = cfg["analysis_context"]
    has_cms_lf = "cms" in cfg["quality_cols"]

    quality_guidance = ""
    if has_cms_lf:
        quality_guidance = """
**Factor in the external quality signals above.** Strong Google review volume signals digital brand presence. High CMS star ratings and Leapfrog A/B grades represent quality achievements that patients and referring physicians actively seek in AI queries — if AI isn't surfacing these credentials, it's a brand visibility gap. Consider:
- Are quality-strong facilities (Leapfrog A, CMS 4–5★) well-represented in AI when queried?
- Does AI proactively surface the network's quality achievements or omit them?
- Which facilities have strong quality credentials but low digital visibility (high opportunity)?
"""
    else:
        quality_guidance = """
**Factor in Google review signals.** Strong review volume and high Google ratings signal digital brand presence and patient satisfaction — if AI isn't surfacing facilities with strong Google reputations, that's a brand visibility gap.
"""

    article = "an" if singular[0].lower() in "aeiou" else "a"
    return f"""You are an expert AI Visibility analyst specializing in large multi-state healthcare networks. Your clients are C-suite executives and network strategy teams.

## What "AI Visibility" means for {article} {singular} network

AI Visibility measures how well a network and its member {plural} are represented in AI assistants (ChatGPT, Claude, Gemini, Perplexity, Bing AI) when {analysis_context} ask questions like:
- "{network_q_template}"
- "Does [Network] have a {singular} in [State]?"
- "Tell me about [Network]"
- "Best {singular} near me in [City]"
- "{local_q.replace('[city]', '[City]')}"

A high AI Visibility score means:
1. The network's brand is well-known and accurately described by AI assistants
2. Individual {plural} surface in local search queries and are correctly attributed to the parent network
3. Key facts (service lines, capabilities, accreditations) are accurate in AI responses

## Three scoring dimensions

**Brand Visibility (40% weight)**
How well does AI represent the network as a whole? When asked about [Network] by name, does AI give an accurate, complete answer? Does it know the network's geography, scale, and capabilities?

**Market Coverage (35% weight)**
For each state in the network's footprint, do the member {plural} surface in local queries? Score based on: (a) do {plural} appear when someone searches "{local_q}"? (b) are they correctly linked to the parent network in those local results?

**Information Accuracy (25% weight)**
Are key facts correct in AI responses? This includes: service lines, capabilities, and whether the network's own {plural} are attributed correctly vs. confused with competitors or independent facilities.

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
- surfaced_for_local: does it appear when searching "{local_q}"?
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
                "description": "Network's formal canonical name (e.g. 'SCA Health')",
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
                "description": "Per-facility AI visibility assessments, one entry per facility.",
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
    facility_data: dict | None = None,
    facility_type: str = "hospital",
) -> tuple[str, str]:
    """Build the system + user prompt pair for network AI visibility analysis."""
    cfg = get_facility_config(facility_type)
    facility_data = facility_data or {}
    plural   = cfg["plural"]
    singular = cfg["singular"]
    local_q  = cfg["local_query"]
    has_cms  = "cms" in cfg["quality_cols"]
    has_lf   = "leapfrog" in cfg["quality_cols"]

    system_prompt = _build_analysis_system(cfg)

    if facilities:
        fac_lines = []
        for i, f in enumerate(facilities, 1):
            beds_str = f" ({f['beds']} beds)" if f.get("beds") else ""
            fd = facility_data.get(f.get("name", "").lower(), {})

            g_rating = fd.get("google_rating")
            g_count  = fd.get("google_review_count")
            google_str = (f"Google {g_rating:.1f}★ ({g_count or 0} reviews)"
                          if g_rating is not None else "Google —")

            line = f"  {i}. {f['name']} — {f['city']}, {f['state']}{beds_str} | {google_str}"

            if has_cms:
                cms = fd.get("cms_star_rating")
                line += f" | CMS {cms}★" if cms is not None else " | CMS —"
            if has_lf:
                lf = fd.get("leapfrog_grade")
                line += f" | Leapfrog {lf}" if lf else " | Leapfrog —"

            fac_lines.append(line)
        facilities_block = "\n".join(fac_lines)
        states = sorted({f["state"] for f in facilities if f.get("state")})
        states_str = ", ".join(states)
        total = len(facilities)
    else:
        facilities_block = f"(No facilities provided — assess from general knowledge of this network)"
        states_str = "unknown"
        total = 0

    rated_g = [fd for fd in facility_data.values() if fd.get("google_rating") is not None]
    if rated_g:
        avg_g     = sum(fd["google_rating"] for fd in rated_g) / len(rated_g)
        total_rev = sum(fd.get("google_review_count") or 0 for fd in rated_g)
        google_summary = (
            f"{len(rated_g)}/{total} facilities have verified Google listings — "
            f"avg {avg_g:.2f}★, {total_rev:,} total reviews"
        )
    else:
        google_summary = "No verified Google listings found"

    quality_block = f"- Google presence: {google_summary}"

    if has_cms:
        rated_c = [fd for fd in facility_data.values() if fd.get("cms_star_rating") is not None]
        if rated_c:
            avg_c = sum(fd["cms_star_rating"] for fd in rated_c) / len(rated_c)
            quality_block += f"\n- CMS quality: {len(rated_c)}/{total} facilities rated by CMS — avg {avg_c:.1f}★"
        else:
            quality_block += "\n- CMS quality: No CMS star ratings retrieved"

    if has_lf:
        rated_lf = [fd for fd in facility_data.values() if fd.get("leapfrog_grade") is not None]
        if rated_lf:
            from collections import Counter
            grade_counts = Counter(fd["leapfrog_grade"] for fd in rated_lf)
            grade_str = ", ".join(f"{cnt}×{g}" for g, cnt in sorted(grade_counts.items()))
            quality_block += f"\n- Leapfrog safety: {len(rated_lf)}/{total} facilities rated — {grade_str}"
        else:
            quality_block += "\n- Leapfrog safety: No Leapfrog grades retrieved"

    col_header = "Google rating"
    if has_cms:
        col_header += " | CMS overall star rating"
    if has_lf:
        col_header += " | Leapfrog Hospital Safety Grade"

    quality_acc_note = ""
    if has_cms or has_lf:
        quality_acc_note = f"\n- **CMS star ratings and Leapfrog safety grades** — does AI correctly state or omit these?"

    user = f"""Conduct a Network AI Visibility analysis for **{network_name}**.

## Network Profile
- Network name: {network_name}
- Facility type: {cfg['label']}
- Headquarters: {hq_location}
- Roster source: {source_url}
- Total {plural} assessed: {total}
- States in footprint: {states_str}
{quality_block}

## {cfg['label']} Roster (with verified external quality data)
Each row shows: {col_header}
{facilities_block}

## Analysis Instructions

Assess AI Visibility across three dimensions:

### 1. Brand Visibility (40% weight)
Evaluate: when AI assistants are asked about {network_name} by name — "{cfg['network_query'].replace('[network]', network_name)}", "Tell me about {network_name}", "Does {network_name} have a {singular} in [state]?" — how accurately and completely do they respond?
- Does AI know the network's scale and geographic footprint?
- Does it correctly enumerate key member {plural}?
- Is the network's brand positioning accurately reflected?

Factor in Google review signals: strong review volume and high ratings signal digital brand presence.

### 2. Market Coverage (35% weight)
For each state in the footprint ({states_str}), evaluate whether {network_name}'s {plural} surface in local queries:
- When people search "{local_q.replace('[city]', '[city]')}", do {network_name} facilities appear?
- Are they correctly attributed to {network_name}?
- Which states/markets have strong coverage vs. significant gaps?

### 3. Information Accuracy (25% weight)
Are key facts correct when AI discusses {network_name} or its {plural}?
- Service lines and clinical capabilities
- Correct affiliation (facilities attributed to {network_name} vs. confused with competitors){quality_acc_note}

### 4. Facility Assessments
For each of the {total} {plural} listed above, provide:
- ai_visibility_score (0–100): overall AI visibility for this specific facility
- surfaced_for_local: does it appear when searching "{local_q}"?
- attributed_to_network: when it appears, is it correctly linked to {network_name}?
- key_gap: the single most important improvement for this facility's AI visibility

### 5. Synthesis
- Write a 3-sentence executive_summary suitable for a board briefing
- Identify 3–5 strategic_recommendations prioritized by impact
- Name the top 3 best-performing markets (top_markets)
- Name the top 3 worst gap markets (gap_markets)

After completing your analysis, call submit_network_result with all structured fields.
"""
    return system_prompt, user
