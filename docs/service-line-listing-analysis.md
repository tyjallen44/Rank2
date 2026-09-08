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

### Implementation notes (as built)
- `perception/service_line_discovery.py` + `ServiceLine`/`ServiceLineSet` models.
- **All hub fetches are browser-rendered** (`_BrowserFetcher.fetch_rendered_html`),
  not raw HTTP — most system sites are SPAs whose services list is client-rendered,
  so the httpx shell was empty. `fetch_rendered_html` now also returns a page that
  genuinely loaded even if the initial response was a Cloudflare 403 challenge that
  JS resolved (with one longer retry), and rejects interstitials.
- **Word-boundary alias matching** (compiled regex) — fixes short-alias false
  positives (`ent` in "urgent", `eye` in "eyewear").
- **Hub ranking** prefers the shallow services *index* over deep condition pages
  (path-depth bonus + exact-basename bonus), so extraction sees the full list.
- Runtime ~70–90s/system (≤5 browser renders + 1–2 LLM calls); acceptable per the
  longer-runtime decision, and streams progress.

### Acceptance results (2026-09-07)
- **Atrium Health:** 21 service lines, high precision (no junk), all majors present,
  URLs on ~19/21. ✅
- **Novant Health (SPA):** 23 lines, clean, all majors, URLs on all. ✅
- **USA Health:** `coverage=none` — its Cloudflare challenge intermittently serves an
  interstitial the headless browser doesn't clear (probabilistic; the same site
  cleared earlier in testing). Handled honestly (disclosed, never fabricated). Known
  limitation for hard-Cloudflare sites; could be hardened later (retry/backoff) if it
  proves common.

### Open decisions for Phase A
- Taxonomy is a fixed code constant for v1 (not admin-editable) — OK?
- `max_hubs` bound (default 4) and per-hub link cap — acceptable?
- Do we want a `min_confidence` filter, or surface `other`/low-confidence lines too
  (flagged) so nothing is silently dropped?

## Phase B — Implementation spec (listing & location resolution)

**Goal:** for each service line from Phase A, resolve the **flagship** listing + a
**bounded sample of locations** offering that line (as Google Business Profile /
Places entities with rating + reviews + place_id), and **surface service lines
that have no findable listing** ("invisible lines"). Feeds Phase C's scoring.

### Module & API
New `perception/service_line_locations.py`:
```
resolve_service_line_listings(system_name, hq_location, service_line_set,
                              on_event=None, per_line_cap=6, cache=True)
    -> ServiceLineListingSet
```
Never raises; always returns; streams progress per line.

### Data model (`perception/models.py`)
```
class Listing(BaseModel):
    place_id: Optional[str]
    name: str
    formatted_address: str = ""
    city: str = ""; state: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    maps_url: Optional[str] = None
    types: list[str] = []
    role: str = "location"          # flagship | location
    affiliation: str = "medium"     # high|medium|low  (name match to the system)
    source: str = "places_search"   # places_search | landing_page

class ServiceLineListings(BaseModel):
    canonical_key: str; canonical_label: str
    landing_url: Optional[str] = None
    flagship: Optional[Listing] = None
    locations: list[Listing] = []   # bounded sample, excludes flagship
    sampled: int = 0
    estimated_total: Optional[int] = None
    coverage: str = "full"          # full | partial | none  (none = invisible line)

class ServiceLineListingSet(BaseModel):
    system_name: str
    lines: list[ServiceLineListings] = []
```

### Places helper (`perception/data/places.py`, new)
```
def text_search(query, *, max_results=10, api_key=None, timeout=20.0) -> list[dict]
# [{place_id, name, formatted_address, rating, review_count, types, maps_url}]
```
Centralizes the Text Search call + field mask (reuse `_SEARCH_TEXT`, the mask,
`_is_healthcare`, `_name_match` already in the module). `fetch_provider` only
returns the top match; Phase B needs the full candidate list.

### Algorithm (per service line)
1. **Queries** from system + service line + HQ metro:
   - flagship: `"{raw_name} {metro}"` (named institute, e.g. "Sanger Heart & Vascular Institute Charlotte NC")
   - locations: `"{system_name} {canonical_label} {metro}"`
2. **Flagship** — `text_search(flagship_query)`; pick best candidate that is
   healthcare (`_is_healthcare`) with a high `_name_match` to raw_name/system.
   No confident match → `flagship=None` (many lines are clinics with no named center).
3. **Location sample** — `text_search(location_query, max_results=10)` →
   **filter** to healthcare + **affiliated** (see below) → **dedupe** by place_id,
   drop the flagship → **cap** to `per_line_cap`, record `estimated_total` from the
   affiliated candidate count (disclosed sample). v1 selection: nearest-to-HQ first.
4. **Coverage** — `none` if flagship None AND locations empty (**invisible line**,
   surfaced not dropped); `partial` if capped/`estimated_total`>`sampled` or no API
   key; else `full`.

### Affiliation matching (avoid competitors — top risk)
Reuse `_name_match`. A candidate is affiliated if its name shares tokens with **any
of**: system name, the resolved flagship name, or the service line `raw_name`. This
catches sub-brands (Atrium's "Sanger", "Levine") that don't contain "Atrium".
Known fuzziness: sub-brand tokens can over-match; low-confidence affiliations are
kept but flagged `affiliation=low`, never silently dropped.

### Cost / caching / reliability
- ~2 Places Text Search calls per line (flagship + locations) → ~40 for a 20-line
  system. Bounded by caps; **cache per (system, canonical_key) ~14–30 days**.
- No silent failure: invisible lines → coverage=none; capped samples disclose
  `sampled` vs `estimated_total`; missing API key → coverage=partial + reason;
  never raises; per-line progress events.

### Acceptance test
Phase A→B on Atrium + Novant. Verify: (a) flagships resolve for named institutes
(Sanger Heart, Levine Cancer); (b) location samples are the **system's own**
locations (no competitors) — the key manual eyeball; (c) any invisible line surfaces
as coverage=none; (d) ratings/review counts populate.

### Acceptance results (2026-09-07, Atrium Health)
Phase A→B end-to-end (18 lines, ~104s A + ~25s B):
- **Location affiliation is the win:** every sampled location came back genuinely
  "Atrium Health …" branded (high) — zero competitors — because affiliation matches
  BRAND tokens, not clinical words. Ratings/review counts populated; nearest-to-HQ
  ordering working; `estimated_total` disclosed on capped lines.
- **Bug found & fixed:** flagship resolution originally matched only the specialty
  word, so it wrongly picked independents ("Charlotte Dermatology", "Charlotte
  Surgery Center", "Carolina Endocrinology", "Dr. Alamarie – Pain"). Flagship now
  ALSO requires brand affiliation; verified those 4 are rejected while genuine
  flagships (Sanger Heart, Levine Cancer, Transplant Center) are accepted.
- Not yet built here: per-(system, service line) caching (spec'd; deferred to when
  wired into the report flow).

### Open decisions for Phase B
- `per_line_cap` default = 6 locations (+ flagship) — OK?
- Accept flagship/sub-brand tokens for affiliation (needed for recall on Sanger/
  Levine-style brands, slight false-affiliation risk) — OK?
- Site-derived locations (parse the landing page's "find a location" links) —
  include in v1, or defer and lead with Places only (my lean: defer)?
- Location selection when population > cap: nearest-to-HQ (my lean) vs highest-review.

## Phase C — Implementation spec (managed-presence detection + rubric scoring)

**Goal:** for each service line (with its Phase-B flagship + location sample), score
**patient-attraction capability** on the agreed 4-dimension rubric and determine a
**management status** (managed / partial / unmanaged / invisible), with evidence.

**Input:** the `ServiceLineListingSet` (Phase B) + each line's `landing_url` (Phase A).
Biggest phase → **sub-phased**: **C1** = per-listing enrichment + completeness /
reputation / content scoring; **C2** = AI-surfacing probe + findability + management
status + overall score.

### Module & API
New `perception/service_line_scoring.py`:
```
score_service_lines(listing_set, hq_location, on_event=None,
                    details_per_line=2, ai_probe=True, cache=True)
    -> ServiceLineScorecardSet
```
Never raises; per-line progress; caches per (system, canonical_key) ~14–30 days.

### New Places helper (`perception/data/places.py`)
```
def place_details(place_id, *, api_key=None, timeout=20.0) -> dict
```
Places v1 GET `/v1/places/{id}` with a field mask for: `primaryType,types,
nationalPhoneNumber,websiteUri,regularOpeningHours,editorialSummary,photos,
rating,userRatingCount,reviews` (reviews give `publishTime` for recency). Never
raises → `{}` on error. Called only for the flagship + top `details_per_line`
locations (cost bound).

### Data model (`perception/models.py`)
```
class DimensionScore(BaseModel):
    key: str                 # findability | completeness | reputation | content
    score: Optional[int]     # 0-100, or None if not_assessed
    status: str              # verified | partial | not_assessed
    signals: list[str]       # evidence bullets

class ServiceLineScorecard(BaseModel):
    canonical_key: str; canonical_label: str
    management_status: str   # managed | partial | unmanaged | invisible
    overall_score: Optional[int]
    dimensions: list[DimensionScore]
    listings_scored: int
    notes: list[str]

class ServiceLineScorecardSet(BaseModel):
    system_name: str
    scorecards: list[ServiceLineScorecard]
```

### Rubric (concrete, verifiable signals)
**Findability (C2)** — has a dedicated landing page (crawl `landing_url`, resolves &
service-line-specific) · has a GBP (Phase B flagship or ≥1 location) · surfaces when
an AI is asked "best {line} in {metro}" (bounded probe, see C2). Score = weighted mix
of the three; components that can't be checked are excluded and the rest renormalized.

**Listing completeness (C1)** — on the flagship (or representative location) via
`place_details`: primaryType/category · hours · phone · website · description
(editorialSummary) · ≥3 photos · appointment/booking link. Score = % of fields present.

**Reputation (C1)** — blend across sampled locations: rating (40%) · review volume,
log-scaled (30%), renormalized to 100. AS BUILT: recency and response-rate are NOT
scored. Response-rate isn't exposed by the API; and recency proved unreliable — the
Places API returns the "most relevant" reviews, not the newest, so a recency number
is misleading (a busy system showed "~1085d"). Both are shown as caveated
informational signals only. Rating + volume are fully verified.

**Content quality (C1)** — crawl `landing_url` (reuse `content_analyzer` crawler +
schema extraction): schema.org Medical* present (30) · online-scheduling/booking CTA
(30) · condition/procedure content depth (25) · provider/team presence (15).

### Management status (derived, with evidence)
- **invisible** — Phase B coverage=none (no listings).
- **unmanaged** — listings exist but completeness low AND no landing page AND stale reviews.
- **managed** — complete listings + landing page + recent reviews (+ responses if measurable).
- **partial** — anything in between.
Framed as *signals of active management* with evidence, never a bare claim (Google
doesn't expose "claimed" status).

### Overall score
Weighted blend of the four dimensions (proposed default weights: findability 30,
completeness 25, reputation 25, content 20 — see open decisions). `not_assessed`
dimensions are excluded and weights renormalized; disclosed in the scorecard.

### AI-surfacing probe (C2)
Reuse the existing battery/prompt pattern, but bounded: 1–2 queries per line
("best {label} in {metro}", "top {specialty} near {metro}") → check if the system
(brand tokens) is named. Fail-soft; counts toward findability only.

### Cost / runtime / reliability
Per line: 1 landing-page crawl + ~2 `place_details` + 1–2 AI probes. For a 20-line
system this is minutes → **live progress is mandatory** (per line + per step), plus
the watchdog + runtime cap + disclosed partial sampling already required. Cache per
(system, canonical_key). No silent failure: `not_assessed` dimensions are shown, never
silently zeroed; invisible lines carry through from Phase B.

### Acceptance test
Phase A→B→C on Atrium + one smaller system. Verify: (a) completeness/reputation/content
scores are sane and backed by real signals (spot-check a flagship's GBP + landing page);
(b) an under-managed line scores lower than a flagship institute; (c) any invisible line
is flagged; (d) management-status labels match a human eyeball on 3–4 lines.

### Acceptance results (2026-09-07, Atrium slice: oncology/cardiology/imaging/pain)
Phase A→B→C end-to-end, ~102s for 4 lines:
- **Discriminating & sensible:** overall oncology 83, cardiology 83, imaging 77,
  pain 69 — reputation correctly separates pain (2.6★ → 30-ish) from cardiology
  (4.7★ → high). Findability 100 on all (dedicated page + GBP + **AI probe surfaced
  the system** for "best {line} in Charlotte"). Completeness 83 (5/6; Atrium GBPs
  lack an editorialSummary). Content 70 (real finding: Atrium service pages have no
  schema.org medical markup; booking CTA + provider links present).
- **Fix from testing:** dropped recency from the reputation score (unreliable, see
  above) — cardiology went 78→97, which matches its genuinely excellent reputation.
- management_status is presence/completeness-based (managed = complete listings +
  dedicated page), so a well-managed-but-underperforming line reads as
  `managed` with a low overall (pain: managed, 69) — the intended dual signal.

### Open decisions for Phase C
- Overall weights (default 30/25/25/20) — adjust?
- Review-response-rate: add the optional Maps place-page scrape (Playwright, best-effort)
  to get it, or leave it `not_assessed` in v1 (my lean: leave it v1, add later)?
- AI-surfacing probe count per line (1 vs 2), and is C2 in-scope now or after C1 ships?
- `details_per_line` = 2 (flagship + top 2 locations) — OK for the cost bound?

## Phase D — Implementation spec (aggregation + reporting + wiring)

**Goal:** turn the Phase-C scorecards into (1) a **per-service-line scorecard section**
+ a **system-level "Service-Line Listing Management" summary** in the network Content
report, (2) a bounded set of **actionable CIK findings** (with drafted remediation), and
(3) wire the whole A→D pass into the network content job with live progress + caching.
Sub-phased: **D1** aggregate + finding derivation + persistence · **D2** report rendering
· **D3** job wiring + caching + progress.

### D1 — Aggregate + derived findings
Aggregate model (`perception/models.py`):
```
class ServiceLineSummary(BaseModel):
    total_lines: int
    managed: int; partial: int; unmanaged: int; invisible: int
    avg_overall: Optional[int]
    best: Optional[str]; worst: Optional[str]
    cross_cutting: list[str]      # e.g. "8 of 18 service pages lack schema.org markup"
    sampling_note: str            # "flagship + up to 6 locations/line; sampled X of ~Y"
```
Computed from the `ServiceLineScorecardSet` in a new
`perception/service_line_report.py` (`build_summary`, `derive_findings`).

**Finding derivation** (scorecard → `ContentFinding`s; platform `service_line`;
aggregate similar gaps so the list stays readable — one cross-cutting finding, not 18):
- **Invisible line** (coverage=none) → HIGH, per line, remediation `listing_management`:
  "No findable listing for {line} — patients & AI can't discover this service."
- **Reputation drag** (reputation dim < 40) → per line, `reputation_program`.
- **Schema gap** (content: no medical schema) → ONE aggregated finding listing the
  lines, `schema_markup`.
- **Listing completeness gap** (common missing field, e.g. description) → ONE
  aggregated finding, `listing_management`.
- **AI-invisibility** (findability AI probe = no) → per line (if metro assessed).
These feed the existing findings list, so the existing drafting engine writes
remediation for them for free.

### D2 — Rendering (reuse the shared surfaces)
- **Report 2** (`content_report_pdf._build_html`): add a new **"Service-Line Listing
  Management"** section (before the findings `wrap`) = summary box (managed/partial/
  unmanaged/invisible counts, avg score, sampling note) + a **scorecard table**:
  `Service line | Status | Overall | Find | Complete | Reput | Content`, score cells
  color-graded. Pass the `ServiceLineScorecardSet` + `ServiceLineSummary` in as a new
  optional arg (default None → section omitted, so single-entity reports are unaffected).
- **Report 1** (`network_pdf.render_content_network`): add a compact one-box summary to
  the keys area ("X of N service lines actively managed · W invisible · avg
  listing-attraction score N").
- The derived findings render in the existing CIK list/section automatically.

### D3 — Wiring into the network content job
In `_job_content_analysis_network` (server.py), after the existing content analysis:
```
emit phase "Service-line listing analysis"
sl   = discover_service_lines(canonical_name, urls, hq, on_event=emit)      # A
lst  = resolve_service_line_listings(canonical_name, hq, sl, on_event=emit) # B
cards= score_service_lines(lst, hq, on_event=emit)                          # C
summ = build_summary(cards); findings += derive_findings(cards)             # D1
# pass cards+summ to the renderers (D2); persist for History/re-download
```
- **Caching (D3):** check a cached service-line result (normalized system name,
  ~14–30 days) before running A→C; store the `ServiceLineScorecardSet` JSON (new
  `service_line_analysis` table or a JSON column on `content_analysis_runs`).
- **Progress/watchdog:** the modules already stream `on_event`; add the header
  counter + elapsed clock + "safe to leave open" copy, a total-runtime cap that
  finalizes with a disclosed partial set, and the guarantee the job always emits
  done/error (per the earlier hang).
- **Gating (open decision):** always-on for network content runs (adds minutes) vs. an
  opt-in "deep service-line audit" toggle.

### No silent failure (carried through)
Invisible lines shown; partial coverage + sampled/estimated_total disclosed in the
summary; `not_assessed` dimensions rendered, not zeroed; a capped/timed-out run
finalizes with a disclosed partial set.

### Acceptance test
Full A→D on Atrium: generate the report section + findings, eyeball that the scorecard
table matches the Phase-C numbers, the summary counts are right, invisible/weak lines
produce the expected findings, and the whole pass streams progress and finishes.

### As built (Phases A–D complete, 2026-09-07)
- D1 `service_line_report.py` (build_summary + derive_findings), D2 scorecard section
  in `content_report_pdf` (Report 2 only), D3 wired into `_job_content_analysis_network`
  behind the opt-in `service_line_audit` toggle (frontend checkbox), with the
  `service_line_analysis` cache table (21-day), findings merged severity-sorted into the
  CIK list, and the section preserved on draft-regeneration.
- Validated in isolation: D1 finding derivation, D2/D3 render (scorecard + merged
  findings), the DB cache round-trip. A full in-app network run (analyze_network + the
  A→D pass, ~10–15 min) is best validated live via the toggle.

### Resolved decisions for Phase D (2026-09-07)
- **Gating:** opt-in toggle ("Include deep service-line listing audit"), not always-on.
- **Persistence:** new `service_line_analysis` table, cached by system name (~14–30d).
- **Finding granularity:** invisible + reputation-drag per line; schema + completeness
  (+ AI-invisibility) aggregated into single findings.
- **Placement:** scorecard section in **Report 2 only** (no Report 1 summary box).

## Retired
- Per-physician roster discovery and per-physician directory lookups.
- Org-level consumer-directory *findings* (dropped in 2a as unreliable). Kept
  foundations: `_BrowserFetcher.fetch_rendered_html`, the `on_event` progress hook
  in `analyze_content`, `platform="directory"` report labels, and the conservative
  result-card matcher in `directory_analyzer` (reusable for landing-page/GBP name
  matching here).
