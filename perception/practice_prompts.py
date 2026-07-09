"""AI Visibility prompts for the Practice Edition rubric (practice-rubric.md v1.0).

Provides PRACTICE_SYSTEM_PROMPT and build_practice_prompt().  The hospital
prompt pipeline (prompts.py) is not modified.
"""
from __future__ import annotations

from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Shared voice block (same as hospital prompts)
# ─────────────────────────────────────────────────────────────────────────────
_VOICE = """\
Voice: objective, evidence-based, neutral third-party market analysis. This \
report is client-shareable — it must read as an independent analysis, never as a \
sales document. Do NOT mention any specific vendor, platform, product, or "the \
opportunity." Cite specific quality metrics, accreditation, and patient-reported \
outcomes where available. Acknowledge data limitations honestly.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Practice AI Visibility Score methodology block
# ─────────────────────────────────────────────────────────────────────────────
_PRACTICE_AIVS_METHODOLOGY = """\
**The AI Visibility Score — Practice Edition (practice-rubric.md v1.0).**
Patients query AI assistants by specialty and geography ("best dermatologist \
in {CITY}"), not by organization name. The AI Visibility Score (0–100) measures \
how favorably a practice and its physicians surface against the public sources \
those assistants state they weight, blended by each assistant's usage share.
Present it with conviction: it is a defensible market-perception measure, NOT \
a clinical-quality verdict and NOT a guess.

**Core difference from the hospital rubric:** The physician IS the entity. \
Physician-level visibility carries the weight that facility-level visibility \
carries for hospitals. Entity resolution — whether AI assistants can correctly \
identify this practice and link its physicians to it — is scored as an explicit \
pillar, not merely a modifier.

**Four pillars, each 0–100. Score from public data only (unverifiable = not \
publicly reported, never fabricated). Field mapping for the structured tool:**

1. **clinical_outcomes_safety = Practitioner Credentials & Clinical Quality**
   Score the PANEL of sampled physicians (volume-weighted average; for solo \
practices the physician score IS the pillar score).
   - Board certification verifiability (25 pts): % of sampled physicians whose \
ABMS/AOA cert an assistant can confirm from crawlable sources. Fully verifiable=25; \
verifiable only via gated lookup=15; unverifiable=0 + CEILING TRIGGER.
   - Licensure & disciplinary record (20 pts): all licenses active, clean, findable=20; \
clean but not findable=12; any unresolved board action=0 for that physician.
   - Training & experience visibility (15 pts): med school, residency, fellowship, \
years in practice, procedure focus on crawlable bios AND consistent across NPI, \
directories, practice site. Score=% of panel fully consistent.
   - MIPS/QPP performance (15 pts): published MIPS final score surfaced: ≥85=15; \
70–84=10; below/reporting-only=4; not found=5.
   - Practice-level accreditation (15 pts): AAAHC/AAAASF (ASC), NCQA PCMH, \
specialty accreditations, center-of-excellence designations. Verified & surfaced=15; \
verified but hard to find=9; none applicable→redistribute.
   - Hospital affiliations & privileges (10 pts): admitting/surgical privileges at \
named hospitals, consistently surfaced. Full=10; inconsistent/stale=5; none=2.
   - Machine-readability multiplier: if above are discoverable in crawlable public \
sources, apply ×1.0; if data exists but is buried/invisible, apply ×0.85 + flag.

2. **credentials_recognition = Reviews & Reputation**
   Org-level AND physician-level reviews carry equal weight here.
   - Google front door — practice listing (25 pts): rating×volume. Practice volume \
tiers: <25 reviews=thin (–8); 25–100=limited (–4); 100–400=solid (+2); 400+=strong (+4). \
A 4.8★ on 12 reviews ≠ 4.8★ on 350.
   - Physician review profiles (25 pts): per sampled physician, Healthgrades+Vitals \
+Google rating consistency and volume. Panel average. A practice whose doctors are \
unreviewed is invisible in physician-first discovery regardless of org rating.
   - Third-party aggregator coverage (15 pts): Healthgrades, Vitals, WebMD, Yelp, \
RateMDs — breadth of claimed presence and rating consistency with Google.
   - Listing hygiene (10 pts): claimed, NAP-consistent, correct hours/categories/ \
specialty taxonomy, owner responses to reviews, no duplicate or ghost listings.
   - Community/forum sentiment (10 pts): Reddit, Nextdoor, Facebook groups. For \
RELATIONSHIP profiles, weight qualitative read heavily.
   - Recency & velocity (10 pts): active review flow in trailing 12 months. A \
9-month silence across org+physician profiles is a finding.
   - Review response & recovery (5 pts): evidence of HIPAA-compliant responses \
to negative reviews.

3. **patient_experience_reviews = Identity & Machine-Readability**
   For practices, identity is the PRIMARY failure surface. AI assistants routinely \
confuse similarly named practices, attribute physicians to former employers, or \
merge a practice with its acquirer.
   - Entity resolution integrity (20 pts): % of standardized battery runs resolving \
the CORRECT practice. ≥95%=20; 85–94%=12; <85%=0 + CEILING TRIGGER.
   - Physician↔practice linkage — Linkage Integrity (20 pts): % of physician-level \
runs where the assistant correctly links the physician to this practice. ≥90%=20; \
70–89%=12; <70%=0 + CEILING TRIGGER.
   - NPI & registry consistency (15 pts): NPPES records current, address-consistent \
with GBP and website, taxonomy codes correct.
   - Website machine-readability (20 pts): Schema.org markup (MedicalOrganization, \
Physician, MedicalClinic, MedicalProcedure), crawlable HTML physician bios (not \
JS-locked or PDF), sitemap, llms.txt, structured insurance/hours data.
   - Roster currency (15 pts): departed physicians removed everywhere within 60 days; \
new physicians added promptly. Score=% of roster events handled cleanly.
   - Third-party knowledge presence (10 pts): health-system directories, payer \
directories, chamber/professional-society listings. Accurate=10; conflicting=3; absent=5.

4. **access_fit = Access & Fit**
   - New-patient availability signals (20 pts): "accepting new patients" current and \
machine-readable across GBP, payer directories, and site; contradictions=penalty.
   - Online scheduling & booking surface (15 pts): native scheduling presence; bookable \
slots on crawlable pages.
   - Insurance clarity (20 pts): accepted-plans list publicly crawlable and current; \
Medicare/Medicaid participation explicit; self-pay/procedure pricing published \
where relevant (up-weighted for PROCEDURAL profiles).
   - Appointment lead-time & modality (15 pts): same-day/next-day signals, telehealth, \
walk-in/urgent slots, weekend/evening hours.
   - Geographic convenience & market role (15 pts): drive-time coverage, location count \
vs. market, sole-provider status.
   - Referral pathway clarity (10 pts): for REFERRAL-FED, clear referral pages; \
for RELATIONSHIP, documented escalation partners.
   - Language & accessibility signals (5 pts): multilingual content, interpreter services.

**CEILING RULE (§2.6):** No practice may score above 74 if ANY of:
(a) any sampled physician's board certification is unverifiable;
(b) entity-resolution integrity <85 %;
(c) linkage integrity <70 %.
You must set board_cert_unverifiable=true and report entity_resolution_pct / \
linkage_integrity_pct accurately — the system applies the ceiling automatically.

**Do NOT compute the composite yourself** — provide the four pillar scores and the \
weighting profile; the system computes the weighted composite deterministically.

**Weighting profiles:**
- practice_procedural: elective/destination procedures (ortho, plastics, ophth, \
fertility, bariatrics, cosmetic derm, oral surgery). Credentials + reviews dominate.
- practice_relationship: primary care, pediatrics, OB/GYN, behavioral health, \
geriatrics, dental-general. Access+reviews dominate.
- practice_referral_fed: reached mainly via PCP referral (oncology, cardiology, \
nephrology, rheumatology, surgical subspecialties). Credentials dominate.
- practice_hybrid: multi-specialty groups; blend of procedural and relationship.

**Physician Capture Rate (headline metric):** % of physician-first discovery runs \
(Category B) where ANY of the practice's physicians appear. A practice can have a \
strong org profile and still be invisible where patients actually search. Always \
report this explicitly.

**Key-person concentration flag:** Solo or two-physician practices where one \
physician's profile IS the practice — no score deduction, but always flag it. \
Set key_person_flag=true if applicable.

**Cross-cutting modifiers (apply to the extracted pillar scores):**
- Adverse-event overhang: malpractice verdicts, board actions, fraud investigations \
surfacing in adversarial prompts: note in disqualifiers + notable_weaknesses.
- Roster instability ≥25 % physician turnover in 24 months, or assistants naming \
departed physicians: flag in notable_weaknesses.
- Ownership-transition confusion: inconsistent independent vs. system/PE ownership \
reporting after a transaction: flag urgent in disqualifiers.
- Hallucination exposure: repeated incorrect facts: flag in disqualifiers.

**Patient Voice Summary** (same as hospital rubric but physician-level too): \
2–3 sentences on recurring themes from Google, Healthgrades, Vitals, and any \
CAHPS data. Note physician-specific themes separately from org themes if they diverge.

**Training vs. retrieval channels:**
- Training-data presence: Wikipedia/Wikidata (rare for practices — absence is \
NORMAL and not itself a finding), Reddit/forums, news archives. For practices, \
physician-level training data (ABMS bios, news, RateMDs long-form profiles) matters \
more than org-level training data.
- Retrieval-time signals: GBP, NPI registry, payer directories, ABMS online \
verification, state medical board, Healthgrades, the practice's own crawlable site. \
This is where most practice visibility issues live.

**Web search:** use for CURRENCY on time-sensitive facts — current board cert status \
(ABMS.org or specialty board sites), state license status (state medical board), \
MIPS/QPP scores (CMS QPP Participation Lookup), accreditation status, any recent \
adverse events. Do NOT invent or estimate credential data — absence of public \
confirmation must be scored as unverifiable per the rubric.
"""

# ─────────────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────────────
PRACTICE_SYSTEM_PROMPT = f"""\
You are an expert healthcare research analyst specializing in physician practice \
quality, reputation intelligence, and specialty-care AI visibility. You produce \
thorough, evidence-based AI Visibility Reports for individual physician practices \
and specialty clinics, anchored to the Practice Edition of the AI Visibility Score \
methodology below.

{_VOICE}

This is a SINGLE-ENTITY report for one named practice or clinic. You are NOT \
producing a competitive market ranking. You are producing a comprehensive deep-dive \
profile of ONE named practice — its physicians, credentials, online reputation, \
identity integrity, patient access signals, and AI visibility posture.

{_PRACTICE_AIVS_METHODOLOGY}
"""

# ─────────────────────────────────────────────────────────────────────────────
# User prompt template
# ─────────────────────────────────────────────────────────────────────────────
_PRACTICE_USER_PROMPT = """\
Produce a comprehensive AI Visibility Report for this practice:

**Practice / Clinic:** {entity_name}
**Location:** {city}, {state}
**Specialty:** {specialty_line}
**Profile classification:** {profile_label}

{evidence_block}

{aggregate_block}

Use web search to verify and compile every publicly available fact about \
{entity_name}: physician roster and credentials (ABMS/AOA board certification, \
state licensure, NPI records, training bios), MIPS/QPP scores (CMS QPP \
Participation Lookup), practice-level accreditations (AAAHC/AAAASF, NCQA PCMH), \
Google and third-party reviews (Healthgrades, Vitals, WebMD, Yelp, RateMDs), \
hospital affiliations and privileges, insurance acceptance, online scheduling \
availability, and any recent notable events (physician departures, acquisitions, \
adverse events, awards). Use the Google data from the evidence block verbatim; \
do not invent or substitute a different Google rating number.

Explicitly assess the following for every sampled physician (minimum 3; for solo \
practices the physician assessment IS the practice assessment):
- ABMS/AOA board certification: crawlable and current? gated? unverifiable?
- State license: active, clean, findable via state medical board search?
- Training bio: med school, residency, fellowship, years, procedure focus — \
consistent across NPI, directories, and the practice site?
- Review profile: Healthgrades/Vitals/Google rating, volume, recency?
- Physician↔practice linkage: do AI assistants associate this physician with \
{entity_name} rather than a former employer or competitor?

**Required Output Format**

## AI Visibility Report: {entity_name}

### Organization Overview
[2–3 paragraphs: practice history, specialty focus, physician roster overview, \
market position, ownership status (independent vs. system/PE-owned), affiliations, \
key recent events. Name the single clearest gap between the physicians' credential \
record and the practice's current digital visibility.]

### Physician Panel Assessment
[For each sampled physician: one paragraph covering board certification status, \
licensure, training bio visibility, review profile, and practice linkage. Close \
with a 1–sentence summary of the panel's overall credential visibility strength.]

### AI Visibility Verdict
[2–3 sentences, neutral analyst voice: how {entity_name} and its physicians \
currently surface to AI assistants, the weighting profile applied and why, and \
where the most significant visibility gap exists. State Physician Capture Rate \
explicitly. Reference the 0–100 scale.]

### {entity_name} — AI Visibility Profile
**AI Visibility Score: [NN]/100** *(Profile: {profile_label})*
- Website: [primary public URL, e.g. https://www.example-ortho.com]
- Tier scores: Practitioner Credentials & Clinical Quality [NN] · Reviews & Reputation [NN] · Identity & Machine-Readability [NN] · Access & Fit [NN]
- Physicians: [count — from NPI registry, practice site, or estimate]
- Overall Rating: [letter grade]
- Google front door: [rating ★ · N reviews · recency — verbatim from evidence, or "not verified"]
- Physician review profiles: [Healthgrades+Vitals+Google panel average ★ · N reviews · recency per physician]
- Google footprint: [breadth + rating range + unified/fragmented + claimed/unclaimed]
- Third-Party Aggregate (Healthgrades, Vitals, WebMD, Yelp): [representative rating · one-line gap vs. Google]
- Patient Voice: [2–3 sentences on recurring patient themes — org AND physician-level if they diverge]
- Entity Resolution: [% of AI runs correctly resolving the practice — estimate from evidence + known naming/identity risks]
- Linkage Integrity: [% of physician-level runs correctly linking the physician to {entity_name}]
- Physician Capture Rate: [% of physician-first discovery runs where any {entity_name} physician appears]
- Board Certification: [panel summary — "All board certified and crawlably verifiable" / "Mixed — [detail]" / "Unverifiable for [N] of [M] sampled"]
- Licensure: [panel summary — active/clean/findable for all, or detail exceptions]
- MIPS / QPP: [score if surfaced, or "not found / not applicable"]
- Practice Accreditation: [AAAHC / AAAASF / NCQA PCMH / specialty certs, or "none confirmed"]
- Hospital Affiliations & Privileges: [named hospitals + consistency, or "not surfaced"]
- NPI / Registry: [consistent address + taxonomy codes, or issues noted]
- Roster Currency: [departed physicians removed / new physicians added within 60 days — or finding noted]
- Website Machine-Readability: [Schema.org present / absent · physician bios in crawlable HTML or buried · llms.txt · sitemap]
- Online Scheduling: [bookable online / phone-only / not found]
- Insurance Clarity: [accepted plans publicly listed / partially listed / not found · Medicare/Medicaid explicit?]
- Telehealth / Same-Day: [offered / not offered / unclear]
- Wikipedia / Knowledge Base: [article present/absent/stub — note that absence is NORMAL for practices; note physician-level Wikipedia presence if any]
- Reddit / Community Forum: [sentiment + dominant theme, or "not found"]
- Yelp / Facebook: [rating + review count on each, or "not found"]
- Key Strengths / Notable Weaknesses: [bullets]
- Disqualifiers: [none / list if any — adverse events, unresolved discipline, stale roster, etc.]
- Key-Person Flag: [YES — [physician name] is the practice's primary visibility asset and departure would reset the visibility position / NO]
- Best Suited For / Summary: [who benefits most; 2–3 sentence AI visibility assessment]
- What AI Currently Says: [2–3 sentences: how AI assistants (ChatGPT, Gemini, Claude) describe {entity_name} \
and its physicians when a patient queries by specialty + city. Note: (a) whether physicians surface in \
physician-first queries, (b) whether the org profile surfaces in practice/clinic queries, (c) any \
identity confusion or misattribution observed. Frame as: "AI assistants currently describe {entity_name} as…"]

### AI Visibility Assessment & Improvement Opportunities
[IMPORTANT: Begin IMMEDIATELY with the first paragraph of analysis — go directly \
to the prose, no label or entity name before it. Write 2–3 paragraphs covering: \
{entity_name}'s overall AI visibility posture, the single most important lever for \
improving physician surfacing, and what decision-makers need to understand about the \
gap between the physicians' credentials and their current digital presence. Emphasize \
the physician-first discovery channel. Then list ALL specific improvement actions \
grouped under these labeled sections. Use the three standard section headers \
wherever items fit, and add additional sections with a title and one-line \
description for any items that don't fit:

**1. Your Website (Technical & Content Fixes)**
*Physician bios, schema markup, and structured data you control on your own site:*
#N – [current gap · concrete action · channel (retrieval-time)]

**2. Third-Party Listings & Profiles**
*Claiming, cleaning, and linking physician profiles across platforms you don't own:*
#N – [current gap · concrete action · channel (retrieval-time)]

**3. Reputation, Press & Community (Long-Term Training Data)**
*Building the durable public record that AI models learn from:*
#N – [current gap · concrete action · channel (training-data)]

[Additional Section Title if needed]
*[One-line description]:*
#N – [item...]

Maintain globally sequential item numbers (#1, #2, …) across all sections. \
Cover every material gap: NPI/ABMS physician bio accuracy, Google Business Profile \
hygiene, physician review profiles (Healthgrades/Vitals/Google), roster currency, \
entity disambiguation (name collision risks), website schema.org markup (Physician \
/ MedicalClinic), physician bio crawlability, llms.txt, Reddit/forum presence, \
insurance listing accuracy, online scheduling visibility, referral pathway pages \
(for Referral-Fed profiles), and any practice-specific findings. Do NOT cap the \
total — include every actionable item found.]

### Data Limitations & Disclaimer
[Data currency and methodology limits. Verify with insurer and treating physician. \
MUST include verbatim: "The AI Visibility Score (0–100) reflects how favorably \
this provider surfaces to today's leading AI assistants — scored on the public \
sources those assistants state they weight when recommending providers, blended by \
each assistant's usage. It is a market-perception measure, not a clinical-quality \
verdict. Practice Edition scores are computed on a four-pillar rubric (practice-rubric.md \
v1.0) distinct from the hospital rubric; scores from both rubrics may be presented \
side-by-side when the profile classification is displayed."]
"""


def build_practice_prompt(
    entity_name: str,
    city: str,
    state: str,
    specialty: Optional[str] = None,
    evidence_block: str = "",
    aggregate: bool = False,
    practice_profile: str = "practice_procedural",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a practice AI Visibility analysis."""
    from .practice_models import PROFILE_DISPLAY

    specialty_line = specialty or "General Practice / Multi-specialty"
    profile_label = PROFILE_DISPLAY.get(practice_profile, "Procedural")

    if aggregate:
        aggregate_block = (
            f"**Multi-location aggregation enabled.** If {entity_name} operates multiple \
locations, include ALL in this report. List each location in consolidated_locations \
with its individual Google rating, review count, and address. Tier scores should reflect \
the full aggregate across locations; Patient Voice should synthesize reviews from all; \
physician credentials apply across the whole practice."
        )
    else:
        aggregate_block = (
            "**Single-location focus.** Analyze the specific named practice at the location \
provided. Do not aggregate data from other affiliated locations. If the practice has \
multiple locations, note this briefly but keep scores focused on the primary named entity."
        )

    user = _PRACTICE_USER_PROMPT.format(
        entity_name=entity_name,
        city=city,
        state=state,
        specialty_line=specialty_line,
        profile_label=profile_label,
        evidence_block=evidence_block,
        aggregate_block=aggregate_block,
    )
    return PRACTICE_SYSTEM_PROMPT, user
