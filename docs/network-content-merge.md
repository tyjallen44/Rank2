# Merge: content summary into the standard Hospital Network report — Design

Status: **planned** · Owner: content-analysis · Last updated: 2026-09-08

## Goal
Every **standard Hospital Network report** — the Hospital Network page "Run" *and*
the HubSpot/public webhook — includes a lightweight **Content & Listing Health**
summary section plus a **CTA to request the full remediation plan**. The heavy
service-line audit stays opt-in/internal. The standalone Content Analysis flow
stays as the internal **fulfillment tool** for producing the remediation plan.

## Locked decisions (2026-09-08)
1. Public/webhook reports get the **lightweight content summary + CTA only** — NOT
   the service-line scorecard (too costly per lead).
2. Keep the standalone Content Analysis flow as an **internal** tool (not retired).

## Why this is clean
- The webhook already calls the standard network generator, so the section reaches
  every public lead with **no integration change**.
- `_content_keys_section(findings)` already renders the findings **summary table +
  the "Request your Content Improvement Plan" CTA** — the exact section we want.
- `render_content_network` already = `render_network_pdf` HTML + that section, so the
  pattern exists; we just make it the default for the standard report.

## What "lightweight content check" runs
`analyze_content(network_name, [source_url], city, state, entity_kind="hospital",
reputation=<per-facility from result.facilities>)` — the existing website crawl
(schema.org / llms.txt / robots AI-access) + Wikidata + Wikipedia + grouped
reputation. **No service-line audit, no drafting.** ~1 min, bounded, fail-soft:
if it errors, the network report still renders without the section.

## Implementation

### 1. Renderer
`render_network_pdf(result, path, brand="original", teaser=False, findings=None)`:
when `findings` is present and not a teaser, append `_content_keys_section(findings)`
(table + CTA) before `</body>` — same injection `render_content_network` uses.

### 2. Generation (gated)
Add `content_summary: bool = False` to `analyze_network`. When True, after the
`NetworkResult` is built and before rendering:
1. Build the reputation dict from `result.facilities` (per-facility google_rating /
   review_count + fragmented/multi-listing footprint) — the same dict the content
   flow builds today.
2. `findings = analyze_content(name, [result.source_url], city, state,
   entity_kind="hospital", reputation=rep, on_event=emit)` (lightweight).
3. `save_content_findings(result.run_id, …, findings)` so the CTA-fulfillment /
   internal detailed flow can reuse them (and later draft from them).
4. Render via `render_network_pdf(result, path, findings=findings)`.
Wrapped so any failure falls back to the plain network report.

### 3. Who passes `content_summary=True`
- **Base Hospital Network run** (`_job_network_analyze`) → True.
- **HubSpot/public webhook** (`_run_public_report_job`) → True.
- **Bulk national-entity runs** (`_run_network_bulk_job`) → **False** (per-entity cost).
- **Content Analysis flow** (`_job_content_analysis_network`) → **False** — it already
  runs its own richer content pass + Report 1/2; avoids double work. (It reuses cached
  network runs anyway.)

### 4. Service-line audit (unchanged)
Stays the opt-in toggle on the internal Content Analysis flow. Never runs on the
public/webhook report. (Optional later: expose the toggle on the internal "Run
Hospital Network" so an internal standard report can also carry the scorecard.)

### 5. Webhook
No change to the HubSpot integration or endpoints — the job just passes
`content_summary=True`.

## Caching / cost
- The content check runs only on **fresh** `analyze_network` generation; a cached
  network run returns its already-rendered PDF (which includes the section if it was
  generated post-merge). Pre-merge cached runs render without it until refreshed.
- Content findings persist per `run_id` (`content_findings`) → reused for the CTA
  fulfillment and internal drafting.
- Base + webhook add ~1 min each. Bulk unaffected. Service-line audit unchanged.

## Retire / keep
- Standard network report becomes the primary artifact; the content-analysis
  **Report 1** (network + keys) is now redundant (kept, but not the lead surface).
- **Report 2** (detailed drafted remediation) = the CTA deliverable, produced
  internally via the standalone flow when a lead engages.
- Standalone Content Analysis flow: **kept** as the internal fulfillment tool.

## Open questions
1. Expose the service-line toggle on the internal "Run Hospital Network" so an
   internal standard report can also include the scorecard? (lean: yes, internal only)
2. CTA contact target — book-a-demo link / SocialClimb / a specific inbox?
3. On a **cached** network-run reuse, re-run the cheap content check to attach a fresh
   summary, or accept the cached PDF as-is? (lean: accept cached; refresh via ignore_cache)
