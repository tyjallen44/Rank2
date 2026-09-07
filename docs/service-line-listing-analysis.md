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

## Phase A — Implementation spec (service-line discovery + normalization)

**Goal:** from a system name + website, produce a normalized, deduped list of the
service lines it offers, each with a landing URL — reliable enough for Phases B–D
to build on. Standalone and independently verifiable.

### Module & API
New `perception/service_line_discovery.py`:
```
discover_service_lines(system_name, urls, hq_location="", on_event=None,
                       max_hubs=4, cache=True) -> ServiceLineSet
```
- `urls`: confirmed system website URL(s) (same source the content flow already uses).
- `on_event`: progress hook (same signature as `analyze_content`'s).
- Never raises; returns whatever it could enumerate + a `coverage` flag.

### Data model (`perception/models.py`)
```
class ServiceLine(BaseModel):
    raw_name: str                 # exactly as shown on their site
    canonical_key: str            # "cardiology"  (taxonomy key, or "other")
    canonical_label: str          # "Cardiology / Heart & Vascular"
    landing_url: Optional[str]    # their service-line page (validated, same-domain)
    source_url: str               # hub page it was found on
    confidence: str               # high (alias match) | medium (llm) | low

class ServiceLineSet(BaseModel):
    system_name: str
    lines: list[ServiceLine]
    hubs_crawled: list[str]
    coverage: str                 # full | partial | none  (partial => disclose in report)
```

### Algorithm
1. **Fetch homepage** via the existing crawler (`_fetch` + `_BrowserFetcher`
   fallback — reuse `content_analyzer`, don't re-implement).
2. **Locate service hubs (A1).** From homepage links, keep same-domain hrefs/anchor
   text matching service-hub hints (bounded to `max_hubs`):
   `services, specialties, specialty, centers, center, institute(s), conditions,
    treatments, care, areas-of-care, medical-services, health-services,
    centers-of-excellence, find-care, our-services, clinical-services`.
3. **Extract candidates (A2).** For each hub page: Playwright-render → `inner_text` +
   link list (anchor text + same-domain href). Feed to Claude via a
   `submit_service_lines` tool (mirrors `network_analyzer._load_hospital_roster`)
   returning `[{name, url}]`. Claude filters clinical service lines from nav noise
   (About/Careers/Billing/Locations). **Validate** each returned `url` against the
   actually-crawled links so landing URLs are real, not hallucinated.
4. **Normalize (A3).** For each raw name: (a) deterministic alias match against
   `_CANONICAL_SERVICE_LINES` (token/substring, high precision) → confidence=high;
   (b) unmatched → one batched Claude call mapping raw names to the nearest key or
   `other` → confidence=medium. Preserve `raw_name` (we show their name, benchmark
   by key).
5. **Dedupe** by `canonical_key` (merge multiple raw names to one line, keep the
   best landing URL); return `ServiceLineSet`. Cache per normalized system name
   (~14–30 days).

### Canonical taxonomy (`_CANONICAL_SERVICE_LINES`, v1 ~28 keys)
key → label → representative aliases:
- cardiology → Cardiology / Heart & Vascular → heart, cardiac, cardiovascular, vascular, sanger heart
- orthopedics → Orthopedics → ortho, bone & joint, musculoskeletal, spine, joint replacement, sports medicine
- oncology → Cancer / Oncology → cancer, hematology, tumor, radiation oncology
- neuroscience → Neurology & Neurosurgery → neuro, brain & spine, stroke, neurosurgery
- womens_health → Women's Health → ob/gyn, obstetrics, gynecology, maternity, women's, midwifery
- pediatrics → Pediatrics → children's, peds, pediatric
- primary_care → Primary Care → family medicine, internal medicine, general practice
- gastroenterology → Gastroenterology → gi, digestive, digestive health
- urology → Urology → urologic
- pulmonology → Pulmonology → lung, respiratory, pulmonary
- nephrology → Nephrology → kidney, renal
- endocrinology → Endocrinology → diabetes, hormone, thyroid
- ent → ENT / Otolaryngology → ear nose throat, otolaryngology, head & neck
- ophthalmology → Ophthalmology → eye, vision
- dermatology → Dermatology → skin
- rheumatology → Rheumatology → arthritis
- general_surgery → Surgery → surgical services, general surgery
- transplant → Transplant → organ transplant
- behavioral_health → Behavioral Health → psychiatry, mental health, behavioral
- rehabilitation → Rehabilitation → rehab, physical therapy, pm&r, physiatry
- emergency → Emergency & Trauma → er, emergency, trauma
- urgent_care → Urgent Care → walk-in
- imaging → Imaging / Radiology → radiology, diagnostic imaging
- bariatrics → Bariatrics / Weight Loss → weight loss, metabolic surgery
- pain_management → Pain Management → pain
- wound_care → Wound Care → hyperbaric
- infectious_disease → Infectious Disease → id
- sleep_medicine → Sleep Medicine → sleep
(+ `other` catch-all; list is a curated code constant for v1, revisit later.)

### LLM tool schema (extraction)
`submit_service_lines(lines: [{name: str, url: str|null}])`, `tool_choice` forced,
`max_tokens ~4096`. Batch per hub page. Normalization uses a second forced tool
`map_service_lines(mappings: [{raw: str, key: str}])`.

### Reliability / progress (per the cross-cutting rules)
- Each fetch/LLM call timeout-bounded and fail-soft; a blocked hub → skip, set
  `coverage=partial`. If no hub found and homepage yields nothing → `coverage=none`.
- Progress events: "Locating services directory…", "Found N candidate service
  lines", "Normalizing…", and the final count. Never hang; always return.

### Acceptance test (before wiring into later phases)
Run against ≥3 real systems (e.g. Atrium Health, Novant Health, USA Health) and
eyeball: (a) **precision** — no nav junk (Careers/Billing) in the list;
(b) **recall** — the major lines (cardiology/ortho/oncology/neuro/women's/primary)
are present; (c) **URL validity** — landing URLs resolve same-domain. Tune hub
hints + the extraction prompt until all three pass on the sample.

### Open decisions for Phase A
- Taxonomy is a fixed code constant for v1 (not admin-editable) — OK?
- `max_hubs` bound (default 4) and per-hub link cap — acceptable?
- Do we want a `min_confidence` filter, or surface `other`/low-confidence lines too
  (flagged) so nothing is silently dropped?

## Retired
- Per-physician roster discovery and per-physician directory lookups.
- Org-level consumer-directory *findings* (dropped in 2a as unreliable). Kept
  foundations: `_BrowserFetcher.fetch_rendered_html`, the `on_event` progress hook
  in `analyze_content`, `platform="directory"` report labels, and the conservative
  result-card matcher in `directory_analyzer` (reusable for landing-page/GBP name
  matching here).
