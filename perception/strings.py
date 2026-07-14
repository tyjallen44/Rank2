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
# The one sentence the brand spec explicitly renamed; the rest of the sentence is
# identical to the old wording.
AIVS_DISCLAIMER = (
    "The Pulse Score (0–100) is an AI-visibility measure reflecting how "
    "favorably this provider surfaces to today’s leading AI assistants — "
    "scored on the public sources those assistants state they weight when "
    "recommending providers, blended by each assistant’s usage. It is a "
    "market-perception measure, not a clinical-quality verdict."
)
AIVS_DISCLAIMER_CHECK = "Pulse Score"   # guard: append disclaimer if this token absent

# ── Email brand name ─────────────────────────────────────────────────────────────
EMAIL_BRAND = "Pulse"

# ── PDF filename tokens ──────────────────────────────────────────────────────────
FILE_INDIVIDUAL     = "Pulse-Diagnostic"
FILE_INDIVIDUAL_SUM = "Pulse-Diagnostic-Summary"
FILE_PATIENT        = "Patient-Pulse"
FILE_COMPARISON_PFX = "pulse-comparison"
