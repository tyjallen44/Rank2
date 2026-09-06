# Provider-Directory / Physician-Profile Analysis — Design

Status: **in progress (Phase 2a)** · Owner: content-analysis · Last updated: 2026-09-06

## Why
AI assistants recommend individual providers from healthcare directories —
Healthgrades, Vitals, Zocdoc, Google physician profiles (later Castle Connolly
and insurer directories) — not from a hospital brand page. Today's Content
Analysis only checks the org's main website + Wikidata/Wikipedia + reputation.
For **ambulatory / physician practices** (including those owned by hospital
networks) this misses the single biggest AI-visibility lever. See
`content_analyzer.py` (Phase-1 scope note defers directories) and the Service
Line Analysis feature.

## Locked decisions
1. **Network model = service-line-anchored deep dives.** Analyze a network's key
   service lines as practice-shaped entities (reuse Service Line machinery),
   each with a *sampled* provider set — not per-physician across the whole system.
2. **No customer rosters.** Discover providers ourselves: the network/practice
   "Find a Doctor" page (Playwright; JS-heavy, paginated) as the employed-provider
   list, enriched/validated via **NPPES / NPI Registry** (free public API) for
   NPI + canonical name + taxonomy/specialty + practice address.
3. **Directory priority:** Google physician profiles + Healthgrades + Vitals +
   Zocdoc first; Castle Connolly + insurer directories later.
4. **Runtime can be longer, but progress must be visible.** Per
   service-line/provider/directory counters + elapsed clock + "safe to leave
   open" copy. Watchdog + hard runtime cap that finalizes with a *disclosed*
   partial sample. Never silent-truncate; the job must always emit done/error
   (learned from the Cloudflare-fallback hang).

## Architecture
One new module → the shared `ContentFindings` object → both Report 1 (keys
section) and Report 2 (detailed), on both the Deep Diagnostic and Network paths.
Same engine, three roster sources:

| Entity | Roster source | Unit analyzed |
|---|---|---|
| Ambulatory practice | its own "our providers" page | all/most providers |
| Single hospital | its find-a-doctor page | sampled providers |
| Network | find-a-doctor filtered by service line | sampled providers per key service line |

## Per-provider directory check (Phase 2c)
Match by name + location + specialty + NPI (avoid common-name false positives;
use an "unverified/possible match" status when confidence is low). For each
directory: exists? · NAP + specialty consistent? · ratings present? Each check
is timeout-bounded and **fail-soft** — a blocked/timed-out directory yields
"couldn't verify," never a false "missing." Results cached per NPI (~14–30 days).

## Findings / drafting / reporting
New CIK finding types (e.g. "5 of 8 sampled cardiologists have no Healthgrades
profile"; "specialty mismatch between Google and Vitals for Dr. X"). Aggregate
per-service-line "provider findability" + network rollup. Drafting reuses the
existing engine for claim/standardize/NAP-fix steps.

## Progress & reliability (first-class)
- Granular streaming: per service line / provider / directory — log advances
  every few seconds, not just the 25s keepalive ping.
- Live counter + elapsed clock in the header.
- "This deep provider audit can take several minutes — safe to leave open; the
  finished report also appears in History."
- Watchdog + total-runtime cap → finalize with disclosed partial sample.
- SSE-drop resilient: job continues in background; results surface in History.

## Feasibility findings (Phase 2a probing, 2026-09-06)
Empirical probes against the live directories changed the phasing:
- **Reachability:** Healthgrades ✓ (200), Vitals ✓ (200), **Zocdoc ✗ (403** — blocks
  headless Chromium too).
- **Org-level presence is NOT reliably detectable.** All three are physician-indexed
  with fuzzy fallback: searching a practice/org name (e.g. "Charlotte Radiology",
  "Novant Health") returns loosely-related *individual doctors*, not the org as a
  matchable entity — so real, well-known orgs read as "not found". Vitals returns
  nothing for a practice-name search.
- **Conclusion:** org-level directory findings would generate false negatives on real
  orgs → they violate the verified-not-inferred discipline and are dropped. Reliable
  detection is **per-provider**, so the directory *findings* move to Phase 2c.
- **What 2a actually shipped:** reusable foundations only — `fetch_rendered_html`
  (hardened headless fetch for JS result pages), the `directory_analyzer` module +
  conservative card-name matching (the 2c matcher), `on_event` progress plumbing
  through `analyze_content`, and the `platform="directory"` report labels. The module
  is deliberately NOT wired into `analyze_content` and never asserts org-level absence.

## Phases
- **2a — Foundations + feasibility (done).** Hardened rendered-fetch, directory module
  skeleton + matcher, progress plumbing, report labels. Org-level findings dropped as
  unreliable (see above).
- **2b — Roster discovery.** Find-a-doctor crawler + NPPES enrichment + sampling.
- **2c — Per-provider directory checks + findings + drafting + per-NPI cache.**
- **2d — Network service-line integration + History surfacing.**
- **2e — Later directories** (Castle Connolly, insurer directories).

## Open risks
- Find-a-doctor variability (top risk; may need per-system tuning).
- Directory bot protection (Playwright + rate-limit; "couldn't verify" not "missing").
- Scraping ToS — NPPES is a clean public API; the three consumer directories are
  not. Prefer official data where it exists.
- Sampling policy (count + selection) and the standard service-line set — TBD.
