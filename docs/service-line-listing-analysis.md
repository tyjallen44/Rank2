# Service-Line Listing & Reputation Analysis — Design

Status: **planned (Step 2 of Content Analysis)** · Owner: content-analysis · Last updated: 2026-09-07

## Goal
For a hospital group, evaluate its **AI-visibility and reputation at the
service-line level** (cardiology, orthopedics, oncology, women's health, …):
1. **Enumerate** the service lines the system offers.
2. **Determine whether it owns/actively manages a set of listings** for each
   service line (dedicated pages + claimed Google Business Profiles + presence
   where patients/AI look), or whether that presence is fragmented/absent.
3. **Evaluate the listings they manage for their capability to attract patients.**

This deliberately does **not** analyze individual physicians. Big systems have
5,000–10,000 providers; per-physician analysis is intractable and isn't how
systems market or how AI assistants answer ("best heart hospital near me").

## Evolution (why this shape)
Step 2 originally aimed at per-physician directory presence (Healthgrades/Vitals/
Zocdoc). Phase-2a probing (2026-09-06) found those directories are
**physician-indexed with fuzzy fallback**, so org-level presence isn't reliably
detectable (real orgs like "Novant Health" read as "not found"), and per-physician
doesn't scale to large systems. We pivoted the unit of analysis up to the
**service line** — tractable (~15–40 lines vs. thousands of providers), reliable
(leans on the system's own site + Google/GBP + AI-answer probing, not fuzzy
directories), and aligned with how systems organize consumer marketing.

## Locked decisions
1. **Service lines come from crawling the system's own site** for the latest
   details (their real service lines / centers / institutes), then normalized to
   a canonical taxonomy for cross-system benchmarking.
2. **Listing scope = flagship + a bounded sample of locations** offering each line
   (not just the flagship, not all locations).
3. **Patient-attraction rubric** (agreed): findability · listing completeness ·
   reputation · content quality (detailed below).
4. **Output** = a per-service-line **scorecard** section inside the network
   Content report, plus a system-level **"service-line listing management"**
   summary.

## Unit of analysis
The **service line** (optionally per sampled location within a line). Never the
individual physician.

## Pipeline
1. **Discover service lines** — crawl the system's Services / Institutes / Centers
   / Conditions navigation → extract their service lines + landing-page URLs →
   normalize to a canonical taxonomy (~20–30 lines).
2. **Resolve listings & locations** — for each service line, identify the flagship
   center + a bounded sample of locations offering it, via the site + Google Places.
3. **Detect owned/managed presence** — dedicated landing page? claimed/managed GBP
   (via proxies)? surfaces when an AI is asked "best [line] in [metro]"?
4. **Score patient-attraction capability** — the 4-dimension rubric per service line
   (and per sampled location), with evidence.
5. **Aggregate & report** — per-service-line scorecard + system-level summary into
   the shared Report 1 (keys section) and Report 2 (detailed).

## Patient-attraction rubric (verifiable signals)
- **Findability** — dedicated service-line landing page exists (crawl); a Google
  Business Profile / listing exists for the center/clinic (Places); the line
  surfaces when an AI assistant is asked "best [line] in [metro]" (prompt battery).
- **Listing completeness** — core GBP fields present: category, hours, phone,
  website, description, photos, services/attributes, appointment/booking link.
- **Reputation** — rating, review volume, review recency, and **review-response
  rate** (responses are a proxy for *active management*).
- **Content quality** — condition/procedure content depth, team presence, schema.org
  markup (MedicalOrganization/MedicalClinic/MedicalProcedure), and clear CTAs /
  online scheduling.

Each signal is verified with evidence; anything unreachable is "couldn't verify,"
never guessed.

## Verified vs. inferred (discipline)
- **"Managing their own listings" is not directly observable.** Google's API does
  not expose "claimed" status. We report **signals of active management** (review
  responses, completeness, recency, consistent branding) *with evidence* — never a
  bare assertion that they do/don't manage a listing.
- Service-line **naming varies** ("Sanger Heart & Vascular" vs "Cardiology" vs
  "Heart Institute"); we crawl their names, then normalize — the normalization
  layer is where the fuzziness lives.

## Reuses existing machinery
- **Service Line Analysis** engine (analyzes a department as a practice-shaped entity).
- **Google Places** (listing + reputation data — already used by content analysis).
- **Content crawler** (schema/llms.txt/pages — `content_analyzer`, incl. the hardened
  headless-browser fallback).
- **AI-visibility prompt battery** (MQCR-style surfacing checks).

## Phasing
- **Phase A — Service-line discovery + normalization.** Crawl the system site →
  service lines + landing URLs → canonical taxonomy.
- **Phase B — Listing & location resolution.** Flagship + bounded location sample
  per line, via site + Places.
- **Phase C — Managed-presence detection + rubric scoring.** Run the 4-dimension
  checks per line/location (crawl + Places + battery); managed-vs-fragmented
  determination with evidence.
- **Phase D — Aggregation + reporting.** Per-service-line scorecard section +
  system-level "service-line listing management" summary; new CIK finding types +
  drafted remediation; renders into shared Report 1/Report 2.

## Cross-cutting (progress, reliability, cost)
- **Live progress (hard requirement):** stream per service line / per location /
  per check so the log advances continuously; header counter + elapsed clock;
  "safe to leave open — the finished report also appears in History" copy.
- **Watchdog + total-runtime cap** → finalize with a **disclosed partial sample**;
  never silent-truncate; the job must always emit done/error (per the earlier
  headless-fetch hang).
- **Caching** per service-line/location (~14–30 days) for cheap re-runs and
  cross-report reuse.
- **Sampling caps** on lines and locations, always disclosed in the report.
- **Google Places quota/cost** — bounded by the sampling caps.

## Open risks
- Service-line naming → canonical normalization (top fuzziness).
- "Managed/claimed" is proxy-inferred, not directly observable.
- Location-sample selection + caps (representativeness vs. cost).
- Runtime on large systems — bounded via caps + progress UX.

## Retired
- Per-physician roster discovery and per-physician directory lookups.
- Org-level consumer-directory *findings* (dropped in 2a as unreliable). Kept
  foundations: `_BrowserFetcher.fetch_rendered_html`, the `on_event` progress hook
  in `analyze_content`, `platform="directory"` report labels, and the conservative
  result-card matcher in `directory_analyzer` (reusable for landing-page/GBP name
  matching here).
