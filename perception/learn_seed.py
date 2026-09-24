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
            'Pulse is the **AI Reputation Analysis Platform** for healthcare. It measures how your organization shows up when patients and consumers ask AI assistants — like ChatGPT, Gemini, Claude, and Copilot — for help choosing where to get care, and it tells you exactly what to change.\n'
            '\n'
            'Every report produces a **Pulse Score** (0–100) and a **national quartile** (1st through 4th), built from four pillars. For hospitals and health systems they are:\n'
            '\n'
            '- **Outcomes & Safety** — quality, safety, and clinical reputation\n'
            '- **Credentials & Recognition** — accreditations, awards, and affiliations\n'
            '- **Experience & Reviews** — patient sentiment and ratings\n'
            '- **Access & Fit** — location, availability, and how well you match what patients ask for\n'
            '\n'
            'Specialty practices and hospital service lines are scored on a practice rubric with its own four pillars (Practitioner Credentials & Clinical Quality, Reviews & Reputation, Identity & Machine-Readability, Access & Fit), and community health centers use a five-pillar rubric — see the Methodology page.\n'
            '\n'
            'Together they tell you not just *whether* AI assistants mention you, but *how favorably* — and every Deep Diagnostic and Hospital Network report now includes a **content analysis with a drafted prescription**: the specific pages, listings, and structured data to fix, written out and ready to publish.'
        ),
    },
    {
        "category": "Overview",
        "title": "Why AI reputation matters",
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
            'A focused, in-depth report for **one organization**. Choose the analysis type first:\n'
            '\n'
            '- **Hospital** — a single hospital.\n'
            '- **Hospital Service Line** — one department of a health system (e.g. *Houston Methodist Orthopedics*): every clinic of that service line rolled up and scored on the practice rubric.\n'
            '- **Specialty Practice** — an independent practice or group, all locations included.\n'
            '- **Community Health (FQHC)** — the Community Health Edition, on its five-pillar rubric.\n'
            '\n'
            '**What it shows**\n'
            '\n'
            '- Your Pulse Score and national quartile, with a full breakdown across the four pillars\n'
            '- What AI assistants currently say about you — in their own words\n'
            '- A **content analysis and drafted prescription** — the exact website, listing, Wikidata, and structured-data fixes, written out\n'
            "- For practices and service lines, a **per-location reputation table**: every location's Google and third-party ratings and review volume — plus a **Google Business Profiles checked** table read directly from Google for every confirmed location (website link, phone, hours, photos, status, reviews), which is what the reputation finding is built on\n"
            '- **What AI assistants actually said** (optional — tick the box on the form) — an observed check: about thirty patient-language questions written for the organization\'s specialty (conditions, procedures, symptoms, cost, access, Spanish) put to Claude, ChatGPT and Gemini, each asked twice. The report shows the share of answers that named the organization as a range across passes, who was named instead, and — by question type — which pages the answers drew on and whether yours was among them. Roadmap items and content findings that address a page assistants relied on carry a **Why this matters** line with that evidence. It never changes the score.\n'
            '- **Score Evidence** (High / Medium / Low) under the score, with the reason — review volume, whether CMS and Leapfrog were verified from the source, and whether live web search was available\n'
            '\n'
            '**Getting the roster right (practices and service lines)**\n'
            '\n'
            '- Discovery finds locations within about 50 miles of the flagship. For practices spread across metros, use **Add locations from your own list** — paste names or upload the practice\'s Google profile CSV; each row is matched to its Google listing and joins the roster.\n'
            '- **Known facts** (Advanced options) lets you pass what the practice has told you — profiles claimed and managed, when review invitations started, how many locations it operates. They enter the analysis as owner-attested evidence, and the recommendations shift from "claim your profiles" to "make ownership visible".\n'
            '- For hospitals, the CMS star rating and Leapfrog grade are fetched directly from those sources during the analysis run. The website itself is crawled too: schema markup, physician pages, sitemap, llms.txt and the quality claims it makes are recorded as verified facts.\n'
            '- **If the website cannot be read by AI assistants** — a bot wall, or a robots.txt rule that tells AI crawlers to stay out — the report says so on its first page in red or amber. Assistants can still name the organization from Google, Healthgrades and competitors\' pages, but nothing it publishes reaches their answers, and they describe it from other people\'s pages. The fix is usually a bot-protection setting, not a rebuild, and it is placed first under What to Do First.\n'
            '\n'
            'Every analysis run also produces a **Teaser** version (details blurred) for sending to a prospect.\n'
            '\n'
            '**Best for:** understanding one organization in detail, preparing for a meeting, or establishing a baseline before making changes.'
        ),
    },
    {
        "category": "The Reports",
        "title": "Hospital Network",
        "body": (
            'An AI Reputation report for a **multi-facility or multi-state hospital system**, designed for C-suite audiences and industry benchmarking.\n'
            '\n'
            '**What it shows**\n'
            '\n'
            '- A system-level Pulse Score and national quartile\n'
            '- A facility scorecard ranking every hospital in the system\n'
            "- The **content analysis and drafted prescription** for the system's digital footprint\n"
            "- Optionally, a **service-line scorecard** grading each department's listings\n"
            '\n'
            'Every analysis run produces three files: the **standard report**, a **Teaser** for prospects, and a **Full Detail** report with the complete content analysis and a publication-ready remediation draft for every finding.\n'
            '\n'
            '**The roster is yours to fix.** Pulse discovers the system\'s facilities and shows them for confirmation. If it misses one, **Add a hospital the AI missed**: the facility is verified against its Google listing and its CMS Care Compare record before it joins the roster, and Pulse remembers it for future discoveries of that network.\n'
            '\n'
            'From the completion screen, **Track in Trends** follows the system score month by month over the same fixed roster (see *Trends*).\n'
            '\n'
            '**Best for:** health systems that need to see their AI reputation across every facility at once and spot which locations need attention.'
        ),
    },
    {
        "category": "The Reports",
        "title": "Hospital Network — Bulk List Scoring",
        "body": (
            "*Admin-only feature.* Score an entire list of hospital systems at once, instead of one report at a time. Upload a spreadsheet of health systems and get the same file back with each system's **Pulse Score (AI Reputation)**, national quartile, and all four pillar scores filled in.\n"
            '\n'
            '**What it shows**\n'
            '\n'
            'For each system in your list:\n'
            '\n'
            '- A Pulse Score (0–100) and national quartile (1st through 4th)\n'
            '- All four pillar scores on the same 0–100 scale: Outcomes & Safety, Credentials & Recognition, Experience & Reviews, Access & Fit\n'
            '\n'
            '**How it works**\n'
            '\n'
            "Admins switch the Hospital Network page to **Bulk List (CSV)** and upload a file with a system-name column (city and state are optional). Pulse scores every entity in the background — no individual reports to open — and the enriched CSV is ready to download when it's done. Each run is also saved under *National Entity Runs* on the History page.\n"
            '\n'
            '**Good to know**\n'
            '\n'
            '- Uses the same four-pillar engine as the Hospital Network and Competitors Rankings reports, so a system reads the same score across every report.\n'
            '- Fast and repeatable — recent scores are reused, and long lists can be resumed if an analysis run is interrupted.\n'
            '\n'
            '**Best for:** sizing up a whole market, region, or target list quickly — and spotting which systems most need attention.'
        ),
    },
    {
        "category": "The Reports",
        "title": "Competitors Rankings",
        "body": (
            'Ranks the providers in a market from strongest to weakest AI reputation, exactly as they surface when patients ask.\n'
            '\n'
            '**Pick the analysis type, then the market**\n'
            '\n'
            '- **Hospital Market** — every hospital in a city and state (or, under Advanced options, a ZIP code and radius).\n'
            '- **Specialty Practice** — the practices in a market for one specialty.\n'
            '- **Student Health Clinics** — on-campus university clinics by state, radius, or athletic conference, on a rubric tailored to student health.\n'
            '- Ranking several markets at once? The **Upload a spreadsheet** link under the form takes your own list (CSV or Excel, one market per row).\n'
            '\n'
            '**Report formats**\n'
            '\n'
            '- **Market Summary** (the default) — compact scorecards for every provider, nothing hidden.\n'
            '- Under Advanced options: **Enticement** (prospect-facing — your prospect shown in full at their true rank while every competitor is obscured) and the **Full report** (customer-facing — the complete ranking with the detail behind every score).\n'
            '\n'
            '**Best for:** business development and competitive positioning conversations.'
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
            "**Setting it up:** choose the type and confirm Entity A; Entity B then starts with the same type, specialty and market — change anything that differs and enter the name. "
            "Any organization your team has analyzed before is suggested as you type.\n\n"
            "**Best for:** direct \"us vs. them\" conversations and quick competitive checks."
        ),
    },
    {
        "category": "The Reports",
        "title": "Event Preparation",
        "body": (
            'Generates an AI Reputation diagnostic for **every organization on an attendee list** in one batch.\n'
            '\n'
            '**What you get**\n'
            '\n'
            '- A report PDF for each organization, delivered together in a ZIP\n'
            "- Your list back as an **enriched CSV** with each attendee's Pulse Score, quartile, and letter grade for quick review\n"
            '- For specialty-practice attendees, the full combined report with the content analysis and drafted prescription\n'
            '- Optionally (Advanced options), a Teaser version of each report\n'
            '\n'
            '**Setting it up:** upload the CSV (name, city, state; an optional *specialty* column). The entity type is detected from the file on the Confirm step — rows with a specialty are analyzed as practices — and can be changed there. Event name and date are optional.\n'
            '\n'
            "**Best for:** conferences and events — walk in already knowing every attendee's AI reputation and what you'd tell them to fix."
        ),
    },
    # ── Who It's For ──────────────────────────────────────────────────────────
    {
        "category": "The Reports",
        "title": "Trends",
        "body": (
            'Trends follows an organization\'s Pulse Score over time: a snapshot every month (or week, or on demand), the four pillars charted, and a **Trend Report** PDF that reads like a briefing.\n'
            '\n'
            '**What can be tracked**\n'
            '\n'
            '- **Hospital**, **Hospital Service Line**, **Specialty Practice**, **Community Health** (on the Community Health Edition rubric) and **Hospital Network** (the system score over a fixed facility roster).\n'
            '- Start from the completion screen of a finished report (**Track in Trends**), from a History row, from **Find an organization** on Home, or from the Trends page itself.\n'
            '\n'
            '**How it stays comparable**\n'
            '\n'
            '- Practice types, community health centers and networks measure the **same confirmed roster** every snapshot. Fix the roster once; for a network, **Add a hospital the AI missed** appends a verified facility and records a dated *Roster changed* note.\n'
            '- The entity\'s identity — name, market, specialty, scope, type — is locked. Everything else is editable in Details: the **Display name** (list and report title), cadence, next analysis date, notes, email delivery and change alerts. To change identity, *track a new entity* prefilled from this one.\n'
            '- **Notes** date what changed (a new website, a review campaign) and appear as markers on the chart and in the Trend Report.\n'
            '\n'
            '**Needs attention**\n'
            '\n'
            'A badge appears when the score fell, an analysis run is overdue, a snapshot was scored on a different rubric than the entity\'s type, the history mixes rubrics (rows marked H / P / C), or the roster is not yet confirmed. Click it for the explanation and the action; **Reviewed** hides the flag for that data.\n'
            '\n'
            '**Also in Details**\n'
            '\n'
            '- **Create full report** launches the Deep Diagnostic or Hospital Network report immediately with the tracked settings; the result also joins the trend.\n'
            '- **Compare** two tracked entities from the list, or open the latest snapshot\'s full report.\n'
            '- The **Owner** column shows who set up tracking; adding an organization that is already tracked warns first, so a system does not get two trend lines by accident.\n'
            '\n'
            '**Best for:** showing a customer that the fixes moved the score, and catching a slide before the next business review.'
        ),
    },
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
            '- Sign in — you land on the **Home** page: a short welcome video, **Find an organization**, and **Which report do I need?**\n'
            '- **Not sure which report?** Click **Start a report** (in the hero, in the sidebar, or the link beside any report title) or answer the two questions on Home, and Pulse opens the right report with the type preselected.\n'
            '- **Find an organization** — start typing a hospital, practice or health system your team has analyzed before; pick it to open it in Deep Diagnostic, Compare Two or Trends. The same suggestions appear in every report form.\n'
            '- Otherwise pick a report from the Home cards or **Report Creation** in the sidebar, enter the organization or market, confirm the listing and locations Pulse finds, and run. Rarely-changed settings sit under **Advanced options** on each page.\n'
            '- If the same organization ran in the last 14 days, Pulse offers the existing report before starting a new analysis run.\n'
            '- Long analysis runs keep going if you close the tab: the result lands in **History** and, unless you turn it off on Home, in your inbox with the PDF attached. **Runs in progress** on Home shows what is still running.\n'
            '- When an analysis run finishes, the completion screen offers the next step: download, **Track in Trends**, **Compare against…**, **Email report…**, or start another report.\n'
            '- Every Deep Diagnostic states its **Score Evidence** (High / Medium / Low) — how much public data sits behind the score — and lists the pages its live web search consulted.\n'
            '\n'
            'Past reports are always available under **History**, where every signed-in user sees every report — yours, your colleagues\' and the admins\' — with **Run by** showing who started each analysis run.'
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
            'The **Pulse Score** (0–100) measures how favorably an organization is represented when patients and referring professionals ask AI assistants — ChatGPT, Gemini, Claude, and Copilot — where to get care. It is a **market-perception measure**, not a clinical-quality verdict: it reflects how the public signals AI assistants rely on add up, not the underlying quality of care.\n'
            '\n'
            'Every report — Deep Diagnostic, Hospital Network, Competitors Rankings, Compare Two, and Event Preparation — uses this same score, and a scored organization is cached for 30 days so it reads identically across reports run in that window.'
        ),
    },
    {
        "category": "Scoring — The Four Pillars",
        "title": "The four pillars",
        "body": (
            'For hospitals and health systems, the Pulse Score is a weighted blend of four pillars, each scored 0–100:\n'
            '\n'
            '- **Outcomes & Safety** — clinical quality, safety, and reputation (e.g. the CMS Overall Hospital Quality Star Rating and the Leapfrog Hospital Safety Grade).\n'
            '- **Credentials & Recognition** — accreditations, awards, national rankings (e.g. U.S. News), fellowship training, and academic affiliation.\n'
            '- **Experience & Reviews** — patient sentiment and verified review volume and ratings.\n'
            '- **Access & Fit** — location, availability, online scheduling, insurance breadth, and how well the organization matches what patients ask for.\n'
            '\n'
            "Specialty practices and hospital service lines use the **practice rubric**, whose four pillars are **Practitioner Credentials & Clinical Quality**, **Reviews & Reputation**, **Identity & Machine-Readability**, and **Access & Fit**. The Reviews & Reputation pillar is computed from **every confirmed location** — a review-count-weighted average of their Google ratings and their combined review volume — so it always agrees with the per-location reputation table in the report and is never taken from a parent hospital's main listing.\n"
            '\n'
            'The weighting of the pillars is set by a profile matched to the organization type (e.g. procedural vs. relationship-based specialties), so the blend reflects what actually drives patient choice in that setting. An unscored pillar (a signal that could not be established) is shown in red rather than guessed.'
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
            'Scores, rankings, and content findings are derived from publicly available signals collected at the time of the report. No quotes, patient statements, or clinical outcomes are fabricated. Primary sources include:\n'
            '\n'
            '- **CMS Care Compare** — Overall Hospital Quality Star Rating, fetched directly from CMS during the analysis run (Care Compare pages need JavaScript, so web search cannot read them). "Not rated" is reported only when CMS lists the facility without a star.\n'
            '- **The Leapfrog Group** — Hospital Safety Grade (A–F), fetched directly; "not found" means the grade could not be retrieved, not that the hospital is ungraded.\n'
            '- **Google Business Profiles** — verified ratings and review volume, with each location pinned to its own listing; for practices, each profile\'s website link, phone, hours, photos and status are read directly and reported in the *Google Business Profiles checked* table.\n'
            '- **Live web search** — the narrative is written while reading current pages, and the report lists the pages consulted. When search is unavailable the report says so and its Score Evidence drops.\n'
            '- **Healthgrades, Vitals, WebMD, Yelp, and RateMDs** — third-party ratings for the per-location reputation table.\n'
            '- **U.S. News & World Report** — national and specialty rankings.\n'
            '- **NPPES** — provider/organization identity and physician rosters.\n'
            "- **The organization's own website** — crawled for structured data (schema.org), an llms.txt file, and AI-crawler access — plus **Wikidata** and **Wikipedia** for the content analysis.\n"
            '- **HRSA Find-a-Health-Center** — for the Community Health Edition.\n'
            '\n'
            'Ratings, review counts, accreditation statuses, and quality designations change over time; verify current standings directly with the primary source before making coverage, referral, or treatment decisions.'
        ),
    },
    {
        "category": "Scoring Method",
        "title": "How the score is produced",
        "body": (
            'Pulse does not ask a single AI assistant for a verdict and repeat it. Each report is built in three steps:\n'
            '\n'
            "1. **Evidence first.** Pulse gathers the public signals an AI assistant would find — Google listings for every confirmed location, CMS and Leapfrog quality data, U.S. News rankings, NPPES identity records, and the organization's own website, Wikidata, and Wikipedia presence.\n"
            '2. **Rubric-scored analysis.** An AI analyst model assesses each pillar against that evidence using a fixed rubric — the same questions patients and referrers actually ask (*"best orthopedic surgeon near me," "which hospital for heart surgery in [city]"*) across brand, local, specialty, and referral framings — and reports what AI assistants currently say in their own words.\n'
            "3. **Deterministic scoring.** The four pillar scores are combined by the organization's weighting profile into the Pulse Score. Google-verified signals — and the CMS star rating and Leapfrog grade fetched in code — override the model where they disagree, so a rating, review count or star in a report is always the real one.\n"
            '4. **Observed check.** About thirty patient-language questions for the organization\'s specialty are put to real AI assistants, each twice, and the answers recorded: how often the organization was named (as a range), who was named instead, which pages were cited. This is reported alongside the score and never changes it.\n'
            '\n'
            'Two rules keep scores comparable. The **rubric follows the requested type**: a Hospital-type report stays on the hospital rubric even when the analysis reads the organization as a clinic network (the report says so in Score Evidence — re-run it as a practice or community health center if that is what it is). And **Score Evidence** (High / Medium / Low) states how much data sits behind the number without ever altering it.\n'
            '\n'
            'The Community Health Edition adds a true query battery: the **Mission Query Capture Rate (MQCR)** measures how often a health center is actually surfaced for the mission-related questions its patients ask.'
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


# ── Home page (in-app landing) ───────────────────────────────────────────────
# One featured block. Paste a YouTube, Vimeo or Loom link on its own line to
# embed the welcome video; everything else is ordinary Markdown.
HOME_ARTICLES = [
    {
        "category": "Welcome",
        "title": "Welcome to Pulse",
        "body": (
            "Pulse shows how AI assistants describe, rank and recommend healthcare "
            "organizations — and what to change so they recommend yours.\n\n"
            "_A short walkthrough video is coming soon. Admins: edit this block under "
            "Learn → Manage Content → Home page and paste a YouTube, Vimeo or Loom link on "
            "its own line to embed it here._"
        ),
    },
]
