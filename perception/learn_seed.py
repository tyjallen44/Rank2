"""Starter content for the Learn page.

Seeded on demand by an admin via POST /api/admin/learn/seed. Seeding is
idempotent: an article is inserted only if no existing article has the same
title (case-insensitive), so it is safe to run more than once and never
overwrites edits an admin has already made.
"""
from __future__ import annotations

STARTER_ARTICLES: list[dict] = [
    # ── Overview ──────────────────────────────────────────────────────────────
    {
        "category": "Overview",
        "title": "What is Pulse?",
        "body": (
            "Pulse is an **AI Visibility Intelligence** platform for healthcare. "
            "It measures how your organization shows up when patients and consumers "
            "ask AI assistants — like ChatGPT, Gemini, and Copilot — for help choosing "
            "where to get care.\n\n"
            "Every report produces a **Pulse Score** (0–100) and a **national quartile** "
            "(Q1–Q4), built from four pillars:\n\n"
            "- **Outcomes & Safety** — quality, safety, and clinical reputation\n"
            "- **Credentials & Recognition** — accreditations, awards, and affiliations\n"
            "- **Experience & Reviews** — patient sentiment and ratings\n"
            "- **Access & Fit** — location, availability, and how well you match what patients ask for\n\n"
            "Together they tell you not just *whether* AI assistants mention you, but "
            "*how favorably* — and where you stand against everyone else in your market."
        ),
    },
    {
        "category": "Overview",
        "title": "Why AI visibility matters",
        "body": (
            "Patients no longer start with a search box. They ask an AI assistant: "
            "*\"Who's the best orthopedic surgeon near me?\"* or *\"Which hospital should "
            "I go to for heart surgery?\"*\n\n"
            "The assistant answers with a short, confident list — and most patients never "
            "look further. If your organization isn't in that list, or is described "
            "inaccurately, you're effectively invisible at the moment the decision is made.\n\n"
            "Pulse shows you exactly what these assistants say today, how you compare to "
            "competitors, and what to change to move up."
        ),
    },
    # ── The Reports ───────────────────────────────────────────────────────────
    {
        "category": "The Reports",
        "title": "Deep Diagnostic",
        "body": (
            "A focused, in-depth report for a **single hospital or practice**.\n\n"
            "**What it shows**\n\n"
            "- Your Pulse Score and national quartile\n"
            "- A full breakdown across the four pillars\n"
            "- What AI assistants currently say about you — in their own words\n"
            "- Specific, prioritized recommendations to improve\n\n"
            "**Best for:** understanding one organization in detail, preparing for a "
            "meeting, or establishing a baseline before making changes."
        ),
    },
    {
        "category": "The Reports",
        "title": "Hospital Network",
        "body": (
            "An AI Visibility report for a **multi-facility or multi-state hospital "
            "network**.\n\n"
            "**What it shows**\n\n"
            "- A network-level Pulse Score and quartile\n"
            "- A **facility scorecard** ranking every hospital in the network\n"
            "- Score-breakdown detail and network-wide patterns\n\n"
            "**Best for:** health systems that need to see visibility across all their "
            "facilities at once and spot which locations need attention."
        ),
    },
    {
        "category": "The Reports",
        "title": "Hospital Network — Bulk List Scoring",
        "body": (
            "Score an **entire list of hospital systems at once**, instead of one "
            "report at a time. Upload a spreadsheet of health systems and get the same "
            "file back with an AI Visibility **Pulse Score**, a **national quartile**, "
            "and all four pillar scores filled in for every entity.\n\n"
            "**What it shows**\n\n"
            "For each system in your list:\n\n"
            "- A **Pulse Score** (0–100) and **national quartile** (Q1–Q4)\n"
            "- All four pillar scores, on the same 0–100 scale:\n"
            "  - **Outcomes & Safety** — quality, safety, and clinical reputation\n"
            "  - **Credentials & Recognition** — accreditations, awards, and affiliations\n"
            "  - **Experience & Reviews** — patient sentiment and ratings\n"
            "  - **Access & Fit** — findability and match to what patients ask for\n\n"
            "**How it works**\n\n"
            "Switch the Hospital Network page to **Bulk List (CSV)** and upload a file "
            "with a system-name column (city and state are optional). Pulse scores every "
            "entity in the background — no individual reports to open — and you download "
            "the enriched CSV when it's done. Each run is also saved under **National "
            "Entity Runs** on the History page, so you can re-download it any time.\n\n"
            "**Why the pillar detail matters**\n\n"
            "The four pillar columns let you see *where* each system is strong or weak at "
            "a glance. Sort or filter any column to find the systems with the biggest gaps "
            "— whether in reputation, quality recognition, or findability.\n\n"
            "**Good to know**\n\n"
            "- Uses the same four-pillar engine as the Hospital Network and Hospital "
            "Market reports, so a system reads the same score across every report.\n"
            "- Fast and repeatable — recent scores are reused, and long lists can be "
            "resumed if a run is interrupted.\n\n"
            "**Best for:** sizing up a whole market, region, or target list quickly — and "
            "spotting which systems most need attention."
        ),
    },
    {
        "category": "The Reports",
        "title": "Competitors Rankings",
        "body": (
            "Ranks the providers in a market from **most to least AI-visible**, exactly "
            "as they surface when patients ask.\n\n"
            "Two ways to run it:\n\n"
            "- **Enticement (prospect-facing)** — your prospect is shown clearly at their "
            "true rank while every other competitor is obscured. Perfect for showing a "
            "prospect where they really stand without revealing competitor detail.\n"
            "- **Full report (customer-facing)** — the complete, un-obscured market "
            "ranking for an existing customer.\n\n"
            "**Best for:** business development and competitive positioning conversations."
        ),
    },
    {
        "category": "The Reports",
        "title": "Compare Two",
        "body": (
            "A head-to-head comparison of **two organizations** on Pulse Score and the "
            "four pillars.\n\n"
            "**What it shows**\n\n"
            "- Side-by-side Pulse Scores and quartiles\n"
            "- Pillar-by-pillar strengths and gaps\n"
            "- Where each organization wins\n\n"
            "**Best for:** direct \"us vs. them\" conversations and quick competitive checks."
        ),
    },
    {
        "category": "The Reports",
        "title": "Event Preparation",
        "body": (
            "Generates AI-visibility diagnostics for a **list of event attendees** in one "
            "batch.\n\n"
            "**What it shows**\n\n"
            "- A diagnostic for each organization on your list\n"
            "- An exportable summary (including letter grades) for quick review\n\n"
            "**Best for:** conferences and events — walk in already knowing every "
            "attendee's AI visibility."
        ),
    },
    # ── Who It's For ──────────────────────────────────────────────────────────
    {
        "category": "Who It's For",
        "title": "Who benefits from Pulse",
        "body": (
            "- **Health system & hospital leadership** — see how your brand and facilities "
            "show up in AI-driven patient decisions.\n"
            "- **Marketing teams** — measure and improve the story AI assistants tell about you.\n"
            "- **Sales & business development** — open conversations with prospects using "
            "their real market ranking.\n"
            "- **Practices & provider groups** — understand where you stand against local "
            "competitors."
        ),
    },
    # ── Getting Started ───────────────────────────────────────────────────────
    {
        "category": "Getting Started",
        "title": "How to run a report",
        "body": (
            "1. Sign in and choose a report type from **Report Creation** in the sidebar.\n"
            "2. Enter the organization or market details the report asks for.\n"
            "3. Start the run — Pulse gathers and analyzes the data live and shows progress "
            "as it works.\n"
            "4. When it finishes, review the report on screen and **download the PDF**.\n\n"
            "Past reports are always available under **History**."
        ),
    },
    {
        "category": "Getting Started",
        "title": "How to get access",
        "body": (
            "Pulse is available to approved users.\n\n"
            "- Already invited? Sign in with Google or your email and password.\n"
            "- Need access? Click **Request Access** on the login screen and an "
            "administrator will review your request.\n\n"
            "Questions about access can go to your Pulse administrator."
        ),
    },
]


# ── Methodology page (public /methodology, linked from report appendices) ─────
METHODOLOGY_ARTICLES: list[dict] = [
    {
        "category": "Overview",
        "title": "What the Pulse Score measures",
        "body": (
            "The **Pulse Score** (0–100) measures how visible and favorable an "
            "organization is when patients and referring professionals ask AI "
            "assistants — ChatGPT, Claude, and Gemini — where to get care. It is a "
            "**market-perception measure**, not a clinical-quality verdict: it reflects "
            "how the public signals AI assistants rely on add up, not the underlying "
            "quality of care.\n\n"
            "Every report — Deep Diagnostic, Hospital Network, Competitors Rankings, "
            "Compare Two, and Event Preparation — uses this same score, so a given "
            "organization reads consistently across reports."
        ),
    },
    {
        "category": "Scoring — The Four Pillars",
        "title": "The four pillars",
        "body": (
            "For hospitals, practices, and markets, the Pulse Score is a weighted blend "
            "of four pillars, each scored 0–100:\n\n"
            "- **Outcomes & Safety** — clinical quality, safety, and reputation (e.g. the "
            "CMS Overall Hospital Quality Star Rating; for practices, procedure depth and "
            "accreditations).\n"
            "- **Credentials & Recognition** — accreditations, awards, national rankings "
            "(e.g. U.S. News), fellowship training, and academic affiliation.\n"
            "- **Experience & Reviews** — patient sentiment and verified review volume and "
            "ratings.\n"
            "- **Access & Fit** — location, availability, online scheduling, insurance "
            "breadth, and how well the organization matches what patients ask for.\n\n"
            "The weighting of the four pillars is set by a **profile** matched to the "
            "organization type (e.g. procedural vs. relationship-based specialties), so the "
            "blend reflects what actually drives patient choice in that setting. An "
            "unscored pillar (a signal that could not be established) is shown in red rather "
            "than guessed."
        ),
    },
    {
        "category": "Scoring — The Four Pillars",
        "title": "National quartiles",
        "body": (
            "The 0–100 Pulse Score maps to a **national quartile**, calibrated against the "
            "distribution of scored organizations in the Pulse database:\n\n"
            "- **Q1 · Top Quartile** — 75 and above\n"
            "- **Q2 · Upper Middle** — 68–74\n"
            "- **Q3 · Lower Middle** — 58–67\n"
            "- **Q4 · Bottom Quartile** — below 58\n\n"
            "Quartiles are shown as *1st / 2nd / 3rd / 4th Quartile* on report covers and "
            "scorecards so the standing is unambiguous."
        ),
    },
    {
        "category": "Data Sources",
        "title": "Where the signals come from",
        "body": (
            "Scores and rankings are derived from publicly available signals collected at "
            "the time of the report. No quotes, patient statements, or clinical outcomes "
            "are fabricated. Primary sources include:\n\n"
            "- **CMS Care Compare** — Overall Hospital Quality Star Rating.\n"
            "- **The Leapfrog Group** — Hospital Safety Grade (A–F).\n"
            "- **Google (Places)** — verified ratings and review volume, sampled across a "
            "system's locations.\n"
            "- **U.S. News & World Report** — national and specialty rankings.\n"
            "- **NPPES** — provider/organization identity and physician rosters.\n\n"
            "Ratings, review counts, accreditation statuses, and quality designations change "
            "over time; verify current standings directly with the primary source before "
            "making coverage, referral, or treatment decisions."
        ),
    },
    {
        "category": "The Prompt Battery",
        "title": "How AI assistants are queried",
        "body": (
            "Pulse evaluates visibility by running a **battery of realistic patient and "
            "referrer queries** against today's leading AI assistants — the same kinds of "
            "questions people actually ask (*\"best orthopedic surgeon near me,\" \"which "
            "hospital for heart surgery in [city],\"* and so on) — spanning brand, local, "
            "specialty, and referral framings.\n\n"
            "The final score is a **usage-weighted blend across assistants**, so an "
            "organization that surfaces well on the assistants patients actually use counts "
            "for more. Divergence between assistants is itself diagnostic — strong on one "
            "assistant but weak on another typically points to an uneven digital footprint "
            "(for example, Google-listings-heavy but thin in the training-data record)."
        ),
    },
    {
        "category": "Community Health Edition",
        "title": "FQHC five-pillar rubric",
        "body": (
            "Community Health (FQHC) reports use a five-pillar rubric calibrated to the "
            "safety-net sector rather than the four-pillar model:\n\n"
            "- **Access & Findability** — including the Mission Query Capture Rate (MQCR).\n"
            "- **Eligibility & Cost Accuracy** — sliding-fee scale, uninsured acceptance, and "
            "enrollment assistance, audited against the center's attested facts.\n"
            "- **Site & Service Completeness** — locations, service lines, and languages.\n"
            "- **Experience & Reputation** — patient sentiment and reviews.\n"
            "- **Institutional Signals** — HRSA Section 330 status and other trust markers.\n\n"
            "The analysis integrates live HRSA Find-a-Health-Center data and a client-attested "
            "intake form."
        ),
    },
]
