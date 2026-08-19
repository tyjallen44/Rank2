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
