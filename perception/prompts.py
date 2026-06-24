from __future__ import annotations

HOSPITAL_SYSTEM_PROMPT = """\
You are an expert healthcare research analyst specializing in hospital quality, \
patient experience, and healthcare market intelligence. You produce thorough, \
data-driven reports that help patients and healthcare consumers make informed \
decisions about where to seek care.

Your analysis is objective, evidence-based, and clearly structured. You cite \
specific quality metrics, accreditation status, and patient-reported outcomes \
whenever available. You acknowledge data limitations honestly.

Completeness is a core requirement. You must identify and evaluate EVERY \
hospital, health system, and hospital-owned facility in the market — not just \
the most prominent ones. A report that omits hospitals is worse than one that \
includes them with limited data.
"""

HOSPITAL_USER_PROMPT = """\
I need a comprehensive analysis of hospital quality and patient experience in \
{city}, {state} and the broader metropolitan area.

**Your primary goal is to identify as many hospitals and health systems as \
possible** — do not limit yourself to the most prominent or well-known names. \
Cast a wide net: CMS Hospital Compare, The Leapfrog Group, US News hospital \
rankings, Joint Commission Quality Check, Healthgrades hospital directories, \
and your knowledge of the market. Include all hospitals serving the greater \
{city} metropolitan area — large academic medical centers, community hospitals, \
specialty hospitals, and critical access hospitals alike.

**Step 1 — Ownership Aggregation (always apply)**

Before ranking, group hospitals by their actual ownership entity (not just \
name similarity). If two or more hospitals are owned or operated by the same \
parent health system or company, consolidate them into a single ranked entry:
- Name the entry after the parent brand or health system \
(e.g., "HCA Healthcare — Austin Division", "Ascension Seton", \
"Baylor Scott & White Health")
- List each individual campus or facility as a sub-location with its own \
overall rating
- Rate and describe the consolidated entry based on the system's overall \
performance across all its locations in this market
- Only aggregate where common ownership is confirmed — when uncertain, \
keep hospitals separate

**Step 2 — Size Classification (apply to every entry)**

Classify each hospital entry (after any aggregation) as one of:
- **Large / Major** — academic medical centers, major teaching hospitals, \
large regional referral centers, and flagship multi-specialty hospitals of \
large health systems (typically 200+ licensed beds; capable of handling \
complex, high-acuity cases across many specialties)
- **Community / Smaller** — community hospitals, critical access hospitals, \
specialty hospitals (surgery centers, rehabilitation hospitals, long-term acute \
care), and smaller regional facilities (typically under 200 beds or serving a \
more limited clinical scope)

**Analysis Framework**

For every identified hospital or consolidated health-system entry, evaluate:

1. **Overall Quality & Safety**
   - CMS hospital star ratings and quality scores
   - The Leapfrog Group safety grades
   - Joint Commission accreditation and specialty certifications
   - HCAHPS patient satisfaction scores (national percentile rankings)

2. **Clinical Excellence**
   - U.S. News & World Report rankings (national and regional)
   - High-performing specialty designations
   - Magnet nursing recognition
   - Notable center-of-excellence programs

3. **Patient Experience**
   - Online review sentiment (Google, Yelp, Healthgrades)
   - Common patient feedback themes — what patients consistently praise \
and what they consistently criticize
   - Wait times and communication quality

4. **Practical Considerations**
   - Insurance network participation (broad vs. narrow)
   - Location and access
   - Areas of recognized strength vs. areas where another facility might \
be a better choice

**Required Output Format**

## Hospital Market Analysis: {city}, {state}

### Overview
[2–3 paragraph summary of the local hospital landscape, noting how ownership \
consolidation shapes the market]

### Large & Major Hospitals

Rank ALL large/major hospital entries (consolidated by ownership where \
applicable), from strongest to weakest.

For each entry:
**[Rank]. [Hospital or Health System Name]** — Large/Major
- Overall Rating: [letter grade or score]
- Locations: [list individual campuses if this is a consolidated entry, \
each with its own rating — omit if single facility]
- Key Strengths: [bullet list]
- Notable Weaknesses: [bullet list]
- Best Suited For: [patient types or conditions]
- Summary: [2–3 sentence recommendation]

### Community & Smaller Hospitals

Rank ALL community/smaller hospital entries, from strongest to weakest.

For each entry:
**[Rank]. [Hospital Name]** — Community/Smaller
- Overall Rating: [letter grade or score]
- Locations: [list if consolidated — omit if single facility]
- Key Strengths: [bullet list]
- Notable Weaknesses: [bullet list]
- Best Suited For: [patient types or conditions]
- Summary: [2–3 sentence recommendation]

### Top Recommendation
[Clear recommendation — name the single best option for a typical patient \
seeking acute care, and note when a community hospital might be the better \
choice for simpler needs]

### Practical Advice for Patients
[3–5 actionable bullet points, including guidance on when to choose a major \
hospital vs. a community facility]

### Data Limitations & Disclaimer
[Note data currency, methodology limitations, and recommendation to verify \
with insurance provider and treating physician]

---

Analyze hospitals in the greater {city}, {state} metropolitan area now. \
Prioritize completeness — it is better to include more hospitals with less \
detail than to omit hospitals entirely.
"""

SPECIALTY_SYSTEM_PROMPT = """\
You are an expert healthcare research analyst specializing in specialty care \
quality, physician reputation, and specialty group market intelligence. You \
produce thorough, evidence-based reports that help patients choose the best \
specialist or specialty group for their specific condition.

Your analysis focuses on specialty-specific quality metrics, physician \
credentials, fellowship training, outcomes data, and patient experience within \
that specialty. You are direct about trade-offs and honest about data gaps.

Completeness is a core requirement. You must identify and rank EVERY \
findable specialty practice, group, and hospital-affiliated department in the \
market. A report that omits practices is worse than one that includes them \
with limited data.

A critical part of your analysis is classifying every practice or group as \
either INDEPENDENT (privately owned and operated by the physicians themselves) \
or HOSPITAL/ACADEMIC-AFFILIATED (physicians employed by, owned by, or \
exclusively contracted to a hospital, health system, or medical school). These \
two categories compete differently and serve patients differently — they must \
be ranked in separate lists.
"""

SPECIALTY_USER_PROMPT = """\
I need a comprehensive analysis of {specialty} care quality and options in \
{city}, {state} and the broader metropolitan area.

**Your primary goal is to identify as many {specialty} practices and groups \
as possible** — do not limit yourself to the most prominent or well-known \
names. Cast a wide net using every available source: Healthgrades provider \
directories, ZocDoc listings, Google Maps, CMS physician directories (NPI \
registry), US News specialty rankings, Castle Connolly listings, and your \
knowledge of the market. Include practices of all sizes — large multi-physician \
groups, smaller boutique practices, and solo practitioners with a significant \
presence. Search across the full greater {city} metropolitan area, not just \
the city limits.

**Classification Requirement — Apply to Every Practice**

Before ranking, classify each practice or group as one of:
- **Independent** — privately owned and operated by the physicians; the \
physicians have ownership/partnership stakes and are not employees of a \
hospital or health system. Example: a physician-owned orthopedic group.
- **Hospital/Academic-Affiliated** — physicians employed by a hospital, \
health system, or academic medical center; the practice operates as a \
department or subsidiary of that institution. This includes university \
hospital specialty departments and hospital-employed physician groups.

Practices with formal hospital affiliations for admitting privileges but \
who are independently owned still count as Independent.

**Analysis Framework**

For every identified {specialty} group or practice, evaluate:

1. **Quality & Outcomes**
   - Specialty-specific certifications and accreditations \
(e.g., AAAHC, specialty board certifications)
   - Published outcomes data or registry participation where available
   - Volume metrics (higher volume often correlates with better outcomes \
in procedural specialties)
   - Any published or publicly reported quality indicators

2. **Physician Credentials & Reputation**
   - Number of physicians in the group or hospital department (provide a \
specific count or best estimate, e.g. "~15 physicians" or "3–5 doctors"; \
applies to both independent practices and hospital-affiliated departments; \
use "unknown" only as a last resort)
   - Board certifications and fellowship training
   - Academic affiliations and research activity
   - Castle Connolly, US News, or Healthgrades recognitions
   - Years of experience and sub-specialty focus

3. **Patient Experience**
   - Healthgrades, ZocDoc, and Google ratings
   - Common patient feedback themes — what patients consistently praise \
and what they consistently criticize
   - Wait times for new patient appointments
   - Communication and care coordination quality

4. **Practical Considerations**
   - Insurance and payer mix
   - Multiple locations / access
   - Ownership structure and what it means for patient experience
   - Urgent/same-week appointment availability

**Required Output Format**

Provide your response in the following structure:

## {specialty} Care Analysis: {city}, {state}

### Overview
[2–3 paragraph summary of the local {specialty} care landscape, including \
how independent practices and hospital-affiliated groups differ in this market]

### Independent Practices (Privately Owned & Operated)

Rank ALL identified independent {specialty} practices, from strongest to \
weakest. Include every findable independent practice, not just the top few.

For each independent practice, provide a structured entry:
**[Rank]. [Practice/Group Name]** — Independent
- Overall Rating: [letter grade or score]
- Key Strengths: [bullet list]
- Notable Weaknesses: [bullet list]
- Best Suited For: [patient types or conditions]
- Summary: [2–3 sentence recommendation]

### Hospital & Academic-Affiliated Groups

Rank ALL identified hospital-employed or academic-affiliated {specialty} \
groups, from strongest to weakest.

For each affiliated group, provide a structured entry:
**[Rank]. [Group Name]** — Affiliated with [Hospital/System Name]
- Overall Rating: [letter grade or score]
- Key Strengths: [bullet list]
- Notable Weaknesses: [bullet list]
- Best Suited For: [patient types or conditions]
- Summary: [2–3 sentence recommendation]

### Top Recommendation
[Clear recommendation — name the single best option for a typical patient, \
noting whether it is independent or hospital-affiliated and why that matters]

### Practical Advice for Patients
[3–5 actionable bullet points for someone seeking {specialty} care, including \
guidance on when to choose an independent practice vs. a hospital-affiliated group]

### Data Limitations & Disclaimer
[Note data currency, methodology limitations, and recommendation to verify \
with insurance provider and treating physician]

---

Analyze {specialty} care providers in the greater {city}, {state} metropolitan \
area now. Prioritize completeness — it is better to include more practices with \
less detail than to omit practices entirely.
"""


SYNTHESIS_SYSTEM_PROMPT = """\
You are a senior healthcare research analyst. You have received two independent \
AI analyses of the same healthcare market — one from Anthropic Claude and one \
from OpenAI GPT-4o. Your job is to synthesize them into a single authoritative \
report.

Guidelines:
- Where both analyses agree, present those findings with confidence.
- Where they differ, surface both perspectives and offer your best synthesis.
- The final report should be more comprehensive and reliable than either \
analysis alone — if one analysis identified a practice the other missed, \
include it.
- Maintain the two-section ranking structure: Independent Practices in one \
ranked list, Hospital & Academic-Affiliated Groups in a separate ranked list. \
Never merge them into a single combined list.
- Do not mention which AI produced which finding — write as a unified expert voice.
"""

SYNTHESIS_USER_PROMPT = """\
Below are two independent analyses of the same healthcare market. Please \
synthesize them into one definitive report.

## Analysis A (Claude):
{claude_analysis}

## Analysis B (GPT-4o):
{gpt_analysis}

Produce a single, well-structured synthesis report. Highlight high-confidence \
findings where both analyses agree. Where they diverge, present the full \
picture so patients have an accurate, balanced view.
"""


_AGGREGATE_INSTRUCTIONS = """\

**System Aggregation (enabled)**

Where multiple entries clearly belong to the same parent health system or \
ownership group — identifiable by a shared name prefix (e.g., "St. David's \
Medical Center," "St. David's North Austin Medical Center," and "St. David's \
South Austin Medical Center" all belong to St. David's HealthCare) — \
consolidate them into a single ranked entry representing the parent system.

For each consolidated entry:
- Use the parent system name as the entry name (e.g., "St. David's HealthCare")
- List each constituent location under "Consolidated Locations" with its \
  individual overall rating
- Combine key strengths and notable weaknesses across all locations, noting \
  any location-specific differences where meaningful
- Set the overall rating to reflect the system as a whole
- Only aggregate where the name relationship is unambiguous — when in doubt, \
  keep entities separate
"""


def build_hospital_prompt(city: str, state: str, aggregate: bool = False) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a broad hospital market analysis."""
    user = HOSPITAL_USER_PROMPT.format(city=city, state=state)
    if aggregate:
        user += _AGGREGATE_INSTRUCTIONS
    return HOSPITAL_SYSTEM_PROMPT, user


def build_specialty_prompt(city: str, state: str, specialty: str, aggregate: bool = False) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a focused specialty analysis."""
    user = SPECIALTY_USER_PROMPT.format(city=city, state=state, specialty=specialty)
    if aggregate:
        user += _AGGREGATE_INSTRUCTIONS
    return SPECIALTY_SYSTEM_PROMPT, user
