# Learn & Methodology content — review draft (2026-09-15)

Rewritten posts reflecting the AI Reputation naming and the current workflow. Unchanged posts are omitted.
Source of truth: `perception/learn_seed.py`. Apply to the live rows with `scripts/apply_learn_content.py --apply`.


## Learn page


### What is Pulse?  
*Category: Overview*

Pulse is the **AI Reputation Analysis Platform** for healthcare. It measures how your organization shows up when patients and consumers ask AI assistants — like ChatGPT, Gemini, Claude, and Copilot — for help choosing where to get care, and it tells you exactly what to change.

Every report produces a **Pulse Score** (0–100) and a **national quartile** (1st through 4th), built from four pillars. For hospitals and health systems they are:

- **Outcomes & Safety** — quality, safety, and clinical reputation
- **Credentials & Recognition** — accreditations, awards, and affiliations
- **Experience & Reviews** — patient sentiment and ratings
- **Access & Fit** — location, availability, and how well you match what patients ask for

Specialty practices and hospital service lines are scored on a practice rubric with its own four pillars (Practitioner Credentials & Clinical Quality, Reviews & Reputation, Identity & Machine-Readability, Access & Fit), and community health centers use a five-pillar rubric — see the Methodology page.

Together they tell you not just *whether* AI assistants mention you, but *how favorably* — and every Deep Diagnostic and Hospital Network report now includes a **content analysis with a drafted prescription**: the specific pages, listings, and structured data to fix, written out and ready to publish.


### Deep Diagnostic  
*Category: The Reports*

A focused, in-depth report for **one organization**. Choose the analysis type first:

- **Hospital** — a single hospital.
- **Hospital Service Line** — one department of a health system (e.g. *Houston Methodist Orthopedics*): every clinic of that service line rolled up and scored on the practice rubric.
- **Specialty Practice** — an independent practice or group, all locations included.
- **Community Health (FQHC)** — the Community Health Edition, on its five-pillar rubric.

**What it shows**

- Your Pulse Score and national quartile, with a full breakdown across the four pillars
- What AI assistants currently say about you — in their own words
- A **content analysis and drafted prescription** — the exact website, listing, Wikidata, and structured-data fixes, written out
- For practices and service lines, a **per-location reputation table**: every location's Google and third-party ratings and review volume

Every run also produces a **Teaser** version (details blurred) for sending to a prospect.

**Best for:** understanding one organization in detail, preparing for a meeting, or establishing a baseline before making changes.


### Hospital Network  
*Category: The Reports*

An AI Reputation report for a **multi-facility or multi-state hospital system**, designed for C-suite audiences and industry benchmarking.

**What it shows**

- A system-level Pulse Score and national quartile
- A facility scorecard ranking every hospital in the system
- The **content analysis and drafted prescription** for the system's digital footprint
- Optionally, a **service-line scorecard** grading each department's listings

Every run produces three files: the **standard report**, a **Teaser** for prospects, and a **Full Detail** report with the complete content analysis and a publication-ready remediation draft for every finding.

**Best for:** health systems that need to see their AI reputation across every facility at once and spot which locations need attention.


### Hospital Network — Bulk List Scoring  
*Category: The Reports*

*Admin-only feature.* Score an entire list of hospital systems at once, instead of one report at a time. Upload a spreadsheet of health systems and get the same file back with each system's **Pulse Score (AI Reputation)**, national quartile, and all four pillar scores filled in.

**What it shows**

For each system in your list:

- A Pulse Score (0–100) and national quartile (1st through 4th)
- All four pillar scores on the same 0–100 scale: Outcomes & Safety, Credentials & Recognition, Experience & Reviews, Access & Fit

**How it works**

Admins switch the Hospital Network page to **Bulk List (CSV)** and upload a file with a system-name column (city and state are optional). Pulse scores every entity in the background — no individual reports to open — and the enriched CSV is ready to download when it's done. Each run is also saved under *National Entity Runs* on the History page.

**Good to know**

- Uses the same four-pillar engine as the Hospital Network and Competitors Rankings reports, so a system reads the same score across every report.
- Fast and repeatable — recent scores are reused, and long lists can be resumed if a run is interrupted.

**Best for:** sizing up a whole market, region, or target list quickly — and spotting which systems most need attention.


### Competitors Rankings  
*Category: The Reports*

Ranks the providers in a market from strongest to weakest AI reputation, exactly as they surface when patients ask.

**Three ways to define the field**

- **Enter a market** — a city and state, or a ZIP code and radius.
- **Upload a spreadsheet** — your own list of organizations.
- **Student Health Clinics** — on-campus university clinics by state, radius, or athletic conference, on a rubric tailored to student health.

**Three report formats**

- **Enticement** (prospect-facing) — your prospect is shown in full at their true rank while every competitor is obscured.
- **Market Summary** — compact scorecards for every provider, nothing hidden.
- **Full report** (customer-facing) — the complete market ranking with the detail behind every score.

**Best for:** business development and competitive positioning conversations.


### Event Preparation  
*Category: The Reports*

Generates an AI Reputation diagnostic for **every organization on an attendee list** in one batch.

**What you get**

- A report PDF for each organization, delivered together in a ZIP
- Your list back as an **enriched CSV** with each attendee's Pulse Score, quartile, and letter grade for quick review
- For specialty-practice attendees, the full combined report with the content analysis and drafted prescription
- Optionally, a Teaser version of each report

**Best for:** conferences and events — walk in already knowing every attendee's AI reputation and what you'd tell them to fix.


### How to run a report  
*Category: Getting Started*

- Sign in — you land on the **Home** page, with a short welcome video and a card for every report type.
- Pick a report from the Home cards or from **Report Creation** in the sidebar.
- Enter the organization or market details the report asks for, confirm the locations Pulse finds, and start the run — progress streams live as it works.
- When it finishes, review the report on screen and download the PDF (and the Teaser, where one is produced).

Past reports are always available under **History**.


## Methodology page


### What the Pulse Score measures  
*Category: Overview*

The **Pulse Score** (0–100) measures how favorably an organization is represented when patients and referring professionals ask AI assistants — ChatGPT, Gemini, Claude, and Copilot — where to get care. It is a **market-perception measure**, not a clinical-quality verdict: it reflects how the public signals AI assistants rely on add up, not the underlying quality of care.

Every report — Deep Diagnostic, Hospital Network, Competitors Rankings, Compare Two, and Event Preparation — uses this same score, and a scored organization is cached for 30 days so it reads identically across reports run in that window.


### The four pillars  
*Category: Scoring — The Four Pillars*

For hospitals and health systems, the Pulse Score is a weighted blend of four pillars, each scored 0–100:

- **Outcomes & Safety** — clinical quality, safety, and reputation (e.g. the CMS Overall Hospital Quality Star Rating and the Leapfrog Hospital Safety Grade).
- **Credentials & Recognition** — accreditations, awards, national rankings (e.g. U.S. News), fellowship training, and academic affiliation.
- **Experience & Reviews** — patient sentiment and verified review volume and ratings.
- **Access & Fit** — location, availability, online scheduling, insurance breadth, and how well the organization matches what patients ask for.

Specialty practices and hospital service lines use the **practice rubric**, whose four pillars are **Practitioner Credentials & Clinical Quality**, **Reviews & Reputation**, **Identity & Machine-Readability**, and **Access & Fit**. The Reviews & Reputation pillar is computed from **every confirmed location** — a review-count-weighted average of their Google ratings and their combined review volume — so it always agrees with the per-location reputation table in the report and is never taken from a parent hospital's main listing.

The weighting of the pillars is set by a profile matched to the organization type (e.g. procedural vs. relationship-based specialties), so the blend reflects what actually drives patient choice in that setting. An unscored pillar (a signal that could not be established) is shown in red rather than guessed.


### Where the signals come from  
*Category: Data Sources*

Scores, rankings, and content findings are derived from publicly available signals collected at the time of the report. No quotes, patient statements, or clinical outcomes are fabricated. Primary sources include:

- **CMS Care Compare** — Overall Hospital Quality Star Rating.
- **The Leapfrog Group** — Hospital Safety Grade (A–F).
- **Google Business Profiles** — verified ratings and review volume, with each location pinned to its own listing.
- **Healthgrades, Vitals, WebMD, Yelp, and RateMDs** — third-party ratings for the per-location reputation table.
- **U.S. News & World Report** — national and specialty rankings.
- **NPPES** — provider/organization identity and physician rosters.
- **The organization's own website** — crawled for structured data (schema.org), an llms.txt file, and AI-crawler access — plus **Wikidata** and **Wikipedia** for the content analysis.
- **HRSA Find-a-Health-Center** — for the Community Health Edition.

Ratings, review counts, accreditation statuses, and quality designations change over time; verify current standings directly with the primary source before making coverage, referral, or treatment decisions.


### How the score is produced  
*Category: Scoring Method*

Pulse does not ask a single AI assistant for a verdict and repeat it. Each report is built in three steps:

1. **Evidence first.** Pulse gathers the public signals an AI assistant would find — Google listings for every confirmed location, CMS and Leapfrog quality data, U.S. News rankings, NPPES identity records, and the organization's own website, Wikidata, and Wikipedia presence.
2. **Rubric-scored analysis.** An AI analyst model assesses each pillar against that evidence using a fixed rubric — the same questions patients and referrers actually ask (*"best orthopedic surgeon near me," "which hospital for heart surgery in [city]"*) across brand, local, specialty, and referral framings — and reports what AI assistants currently say in their own words.
3. **Deterministic scoring.** The four pillar scores are combined by the organization's weighting profile into the Pulse Score. Google-verified signals override the model where they disagree, so a rating or review count in a report is always the real one.

The Community Health Edition adds a true query battery: the **Mission Query Capture Rate (MQCR)** measures how often a health center is actually surfaced for the mission-related questions its patients ask.
