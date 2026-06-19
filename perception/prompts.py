from __future__ import annotations

HOSPITAL_SYSTEM_PROMPT = """\
You are an expert healthcare research analyst specializing in hospital quality, \
patient experience, and healthcare market intelligence. You produce thorough, \
data-driven reports that help patients and healthcare consumers make informed \
decisions about where to seek care.

Your analysis is objective, evidence-based, and clearly structured. You cite \
specific quality metrics, accreditation status, and patient-reported outcomes \
whenever available. You acknowledge data limitations honestly.
"""

HOSPITAL_USER_PROMPT = """\
I need a comprehensive analysis of hospital quality and patient experience in \
{city}, {state}. Please research and evaluate the major hospitals and health \
systems serving this area.

**Analysis Framework**

For each major hospital or health system in {city}, {state}, evaluate:

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

Provide your response in the following structure:

## Hospital Market Analysis: {city}, {state}

### Overview
[2–3 paragraph summary of the local hospital landscape]

### Rankings

For each major hospital, provide a structured entry:
**[Rank]. [Hospital Name]**
- Overall Rating: [letter grade or score]
- Key Strengths: [bullet list]
- Notable Weaknesses: [bullet list]
- Best Suited For: [patient types or conditions]
- Summary: [2–3 sentence recommendation]

### Top Recommendation
[Clear recommendation with rationale]

### Practical Advice for Patients
[3–5 actionable bullet points]

### Data Limitations & Disclaimer
[Note data currency, methodology limitations, and recommendation to verify \
with insurance provider and treating physician]

---

Analyze the hospitals in {city}, {state} now.
"""

SPECIALTY_SYSTEM_PROMPT = """\
You are an expert healthcare research analyst specializing in specialty care \
quality, physician reputation, and specialty group market intelligence. You \
produce thorough, evidence-based reports that help patients choose the best \
specialist or specialty group for their specific condition.

Your analysis focuses on specialty-specific quality metrics, physician \
credentials, fellowship training, outcomes data, and patient experience within \
that specialty. You are direct about trade-offs and honest about data gaps.
"""

SPECIALTY_USER_PROMPT = """\
I need a comprehensive analysis of {specialty} care quality and options in \
{city}, {state}. Please research and evaluate the leading {specialty} groups, \
practices, and physicians serving this area.

**Analysis Framework**

For each major {specialty} group or practice in {city}, {state}, evaluate:

1. **Quality & Outcomes**
   - Specialty-specific certifications and accreditations \
(e.g., AAAHC, specialty board certifications)
   - Published outcomes data or registry participation where available
   - Volume metrics (higher volume often correlates with better outcomes \
in procedural specialties)
   - Any published or publicly reported quality indicators

2. **Physician Credentials & Reputation**
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
   - Hospital affiliations (important for procedures)
   - Urgent/same-week appointment availability

**Required Output Format**

Provide your response in the following structure:

## {specialty} Care Analysis: {city}, {state}

### Overview
[2–3 paragraph summary of the local {specialty} care landscape]

### Rankings

For each major group or practice, provide a structured entry:
**[Rank]. [Practice/Group Name]**
- Overall Rating: [letter grade or score]
- Key Strengths: [bullet list]
- Notable Weaknesses: [bullet list]
- Best Suited For: [patient types or conditions]
- Summary: [2–3 sentence recommendation]

### Top Recommendation
[Clear recommendation with rationale]

### Practical Advice for Patients
[3–5 actionable bullet points for someone seeking {specialty} care]

### Data Limitations & Disclaimer
[Note data currency, methodology limitations, and recommendation to verify \
with insurance provider and treating physician]

---

Analyze {specialty} care providers in {city}, {state} now.
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
analysis alone.
- Use the same report structure as the input analyses.
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


def build_hospital_prompt(city: str, state: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a broad hospital market analysis."""
    user = HOSPITAL_USER_PROMPT.format(city=city, state=state)
    return HOSPITAL_SYSTEM_PROMPT, user


def build_specialty_prompt(city: str, state: str, specialty: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a focused specialty analysis."""
    user = SPECIALTY_USER_PROMPT.format(city=city, state=state, specialty=specialty)
    return SPECIALTY_SYSTEM_PROMPT, user
