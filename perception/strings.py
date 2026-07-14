"""User-visible display strings — single source of truth for the Pulse brand.

Import from here in presentation-layer modules (pdf.py, analyzer.py, email_utils.py).
Do NOT import in scoring, evidence-collection, or analysis logic.
"""

PRODUCT_NAME     = "Pulse"
PRODUCT_SUBTITLE = "AI Visibility Intelligence"

# ── Report-type display names (sidebar nav, page titles, completion back-buttons) ──
REPORT_MARKET     = "Market Pulse"
REPORT_PATIENT    = "Patient Pulse"
REPORT_INDIVIDUAL = "Pulse Diagnostic"
REPORT_COMPARISON = "Pulse Comparison"

# ── PDF cover eyebrow & sub-label ───────────────────────────────────────────────
COVER_MARKET              = "Market Pulse"
COVER_PATIENT             = "Patient Pulse"
COVER_PATIENT_TEASER      = "Patient Pulse Summary — Request Full Report"
COVER_INDIVIDUAL          = "Pulse Diagnostic"
COVER_INDIVIDUAL_TEASER   = "Pulse Diagnostic Summary — Request Full Report"
COVER_COMPARISON          = "Pulse Comparison"
COVER_REPORT_SUB          = "AI Visibility Report"   # small line under Pulse Diagnostic / Pulse Comparison

# ── Section headers (presentation layer only — not used in prompts or extraction) ──
SECTION_VERDICT                  = "Pulse Verdict"
SECTION_ASSESSMENT               = "Diagnostic Assessment &amp; Roadmap"
SECTION_COMPARISON_OVERVIEWS     = "Organization Overviews &amp; Pulse Verdicts"
SECTION_COMPARISON_SCORE_LABEL   = "Pulse Score (AI Visibility)"
SECTION_COMPARISON_VERDICT_LABEL = "Pulse Verdict"

# ── Score badge labels ───────────────────────────────────────────────────────────
SCORE_LABEL      = "Pulse Score"    # displayed uppercase via CSS in PDF
SCORE_DESCRIPTOR = "AI Visibility"  # sub-label line, also uppercase via CSS

# ── Teaser / roadmap ────────────────────────────────────────────────────────────
ROADMAP_TITLE = "Pulse Improvement Roadmap"

BLUR_CTA_INDIVIDUAL = (
    "Access the complete Pulse diagnostic, detailed signal breakdown, "
    "and your personalized Pulse Improvement Roadmap."
)
BLUR_CTA_COMPARISON = (
    "Access the complete Pulse Comparison, detailed signal breakdown, "
    "and your personalized Pulse Improvement Roadmap."
)

# ── Rankings subtitles ───────────────────────────────────────────────────────────
RANKED_TEASER_SUBTITLE = (
    "Ranked by Pulse Score — contact us for the full report"
)
RANKED_PATIENT_SUBTITLE = (
    "Ranked by Pulse Score — the order a patient is likely to encounter "
    "these providers when asking an AI assistant for guidance"
)

# ── Market / patient advice CTA ─────────────────────────────────────────────────
MARKET_ADVICE_CTA = (
    "The recommendations in this section are most valuable when focused on a "
    "single organization. This report covers multiple providers across a market — "
    "to receive a personalized Pulse Diagnostic for your organization, "
    "contact us for a full Pulse Diagnostic. An individual report delivers a "
    "prioritized, action-ready roadmap specific to your digital footprint, naming "
    "exactly what to fix, where to fix it, and which AI visibility channel each "
    "action improves. Call us at 801.998.2830 or "
    "<a href='https://www.rldatix.com/en-nam/book-a-demo/' style='color:#2aa198'>"
    "Get Your Report</a>."
)

# ── Deep-dive section header inside Pulse Comparison PDF ────────────────────────
DEEP_DIVE_HEADER_TPL = "Pulse Diagnostic — {name}"

# ── AI Visibility disclaimer ─────────────────────────────────────────────────────
# AIVS_DISCLAIMER: the closing Pulse Score definition sentence (one sentence, unchanged).
# DATA_LIMITATIONS_BLOCK: the full context block that precedes it — hardcoded so it
#   is never dependent on LLM generation and never silently discarded by the guard.
# FULL_DISCLAIMER: the complete client-facing disclaimer used in every report.
AIVS_DISCLAIMER = (
    "The Pulse Score (0–100) is an AI-visibility measure reflecting how "
    "favorably this provider surfaces to today’s leading AI assistants — "
    "scored on the public sources those assistants state they weight when "
    "recommending providers, blended by each assistant’s usage. It is a "
    "market-perception measure, not a clinical-quality verdict."
)
AIVS_DISCLAIMER_CHECK = "Pulse Score"   # retained for backward-compat; no longer used as guard

DATA_LIMITATIONS_BLOCK = (
    "Data Limitations & Disclaimer\n\n"
    "Scores and rankings are derived from publicly available signals collected at "
    "the time of this report. Ratings, review counts, accreditation statuses, and "
    "quality designations change over time; verify current standings directly with "
    "the primary sources: Leapfrog Group (leapfroggroup.org), CMS Care Compare "
    "(medicare.gov/care-compare), U.S. News & World Report Health, and each "
    "provider’s own website and credentialing body.\n\n"
    "No quotes, patient statements, or clinical outcomes in this report have been "
    "fabricated. All quoted or paraphrased language is attributed to publicly "
    "available sources. Any unverifiable signal is rendered as "
    "✗ Not established rather than estimated.\n\n"
    "This report is not a substitute for the judgment of an insurer, benefits "
    "administrator, or treating physician. Before making coverage, referral, or "
    "treatment decisions, confirm provider credentials, network participation, and "
    "current quality ratings with the relevant insurer and treating clinician."
)

FULL_DISCLAIMER = DATA_LIMITATIONS_BLOCK + "\n\n" + AIVS_DISCLAIMER

# ── Weighting-profile display names (for client-facing prose — no snake_case) ────
PROFILE_DISPLAY_HOSPITAL: dict[str, str] = {
    "procedural":   "Procedural",
    "relationship": "Relationship",
}
# Practice-edition display names imported from practice_models.PROFILE_DISPLAY;
# re-exported here so callers have one import location.
def _practice_profile_display() -> dict[str, str]:
    from .practice_models import PROFILE_DISPLAY
    return PROFILE_DISPLAY

# ── Email brand name ─────────────────────────────────────────────────────────────
EMAIL_BRAND = "Pulse"

# ── PDF filename tokens ──────────────────────────────────────────────────────────
FILE_INDIVIDUAL     = "Pulse-Diagnostic"
FILE_INDIVIDUAL_SUM = "Pulse-Diagnostic-Summary"
FILE_PATIENT        = "Patient-Pulse"
FILE_COMPARISON_PFX = "pulse-comparison"
