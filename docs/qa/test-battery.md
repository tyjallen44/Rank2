# Pulse — QA Test Battery

This file is the **growing, agent-executable test battery** for validating each release.
Every meaningful change appends an entry below. A testing agent should:

1. Read the **Environment** section once.
2. For each **change entry** since the last validated commit, run its **Test Cases** and
   **Regression Checks**, and record pass/fail against the **Acceptance Checklist**.
3. Keep prior entries in the battery — they become permanent regression coverage.

Entry IDs are stable (`AREA-SHORTNAME-N`). Reference them in bug reports.

---

## Environment

- **App:** Pulse (FastAPI backend `server.py`, single-page frontend `web/index.html`).
- **Run locally:** start the server, open `http://localhost:8000`, sign in (admin session).
  Frontend changes take effect on reload; backend changes need a server restart. Production
  deploy is `bash deploy.sh`.
- **Primary UI under test here:** left nav → **Deep Diagnostic** (`#page-individual`).
- **No browser automation is assumed** — cases are written for a human-or-agent driving the UI.
  Where a check is code-level, it's marked **[code]** and can be asserted by reading the file.
- **Test data (safe, well-populated):**
  - Specialty practice: **OrthoSouth**, Memphis, TN, specialty *Orthopedics* (multi-location).
  - Hospital: **Atrium Health**, Charlotte, NC.
  - FQHC: any Community Health org with a known site.

---

## DD-CONSOLIDATION-STAGE1 — Deep Diagnostic flow: auto-select top match + fold practice profile into the options panel

**Shipped:** 2026-09-11 · **Area:** Deep Diagnostic (create-report flow) · **Type:** UX consolidation (stage 1 of 3)

### What changed
The Deep Diagnostic create flow had extra gated screens. Stage 1 removes **two gates**:
1. **Manual candidate "Select" click** → the **top Google Places match is auto-selected** as soon as
   search results render. The candidate list stays visible; clicking a different card, or
   **"Use This Name"**, re-selects and re-advances.
2. **"Practice Profile Classification" screen (`ir-step2b`)** → **removed**. The practice profile is
   still auto-classified, but the override **dropdown now lives inline in the final options panel**
   (`#ir-step3` → `#ir-profile-inline`), shown for Specialty Practice only. The specialty path now goes
   straight from selection into location discovery.

This is stage 1 of a 3-stage consolidation toward a 2-screen flow. Stages 2–3 (merge the location
confirmation list; fold FQHC intake) are **not** in this change — do not test for them yet.

### Files changed
- `web/index.html` only (frontend). Removed `#ir-step2b` card; added `#ir-profile-inline` inside
  `#ir-step3`; reworked `showIrProfileStep()`, `showIrCandidates()` (auto-select), `selectIrCandidate()`
  hospital branch, `showFqhcIntake()`, `irResetSearch()` and a second reset list; deleted
  `irConfirmProfile()`.

### Behavior: before → after
| Step | Before | After |
|---|---|---|
| After Search | Click **Select** on a candidate | Top match **auto-selected**; list still clickable |
| Specialty profile | Separate **"Confirm Profile → Discover Locations"** screen | Dropdown **inline in options panel**; no separate screen |
| Hospital / FQHC | Went straight to options / intake | Unchanged (still straight through) |

### Test Cases

**T1 — Specialty: auto-select + inline profile (happy path)**
- Given Deep Diagnostic, **Specialty Practice**, `OrthoSouth` / `MEMPHIS` / `TN` / `ORTHOPEDICS`.
- When I click **Search**.
- Then the candidate list renders **and the top card is auto-highlighted** (teal border) with no click.
- And there is **no "Practice Profile Classification" screen**.
- And location discovery starts automatically (the "Discovering locations…" step appears).
- And when the options panel (`Selected Organization`) appears, it contains a **Practice Profile**
  dropdown pre-set to the auto-classified value (e.g. *Procedural*) with its description.
- And changing the dropdown updates the description; the chosen value is used when I **Run Diagnostic**.

**T2 — Specialty: choose a different candidate**
- Given T1's results are showing with the top match auto-selected.
- When I click **Select** on a different OrthoSouth card (or **Use This Name**).
- Then that card becomes the selected one and the flow re-advances (re-classify + re-discover) without error.

**T3 — Hospital: unchanged straight-through**
- Given **Hospital / Health System**, `Atrium Health` / `Charlotte` / `NC`, **Search**.
- Then the top match is auto-selected and the options panel appears directly.
- And **no** Practice Profile dropdown is shown (`#ir-profile-inline` stays hidden).

**T4 — FQHC: unchanged straight-through**
- Given **Community Health (FQHC)**, a known org, **Search**.
- Then the top match is auto-selected and the **Client Attestation / intake** step appears.
- And **no** Practice Profile dropdown is shown.

**T5 — Reset / Search Again**
- From any point, click **← Search Again**.
- Then no console errors; the form resets; `#ir-profile-inline` is hidden; a new search works.

### Regression Checks
- **R1** A full Specialty run completes and produces the combined practice report (score + Diagnostic
  Assessment + content prescription) — the profile value chosen inline is respected.
- **R2** Practice Composite still works: enabling it and running still reaches the composite practice
  list confirmation (this stage did **not** change composite discovery).
- **R3** Service-line detection still fires for a department (e.g. a hospital service line) after selection.
- **R4** No JavaScript console errors on: search, auto-select, switching entity type, Search Again.
- **R5 [code]** No remaining references to `ir-step2b` or `irConfirmProfile` in `web/index.html`
  (except explanatory comments). Grep must return only comment lines.

### Acceptance Checklist
- [ ] T1 auto-select + inline profile (specialty)
- [ ] T2 re-select a different candidate
- [ ] T3 hospital straight-through, no profile shown
- [ ] T4 FQHC straight-through, no profile shown
- [ ] T5 reset with no errors
- [ ] R1 full specialty run respects inline profile
- [ ] R2 practice composite unaffected
- [ ] R3 service-line detection unaffected
- [ ] R4 no console errors
- [ ] R5 [code] no dangling `ir-step2b` / `irConfirmProfile` refs

### Notes for the testing agent
- If the app can't reach Google Places (no candidates), the flow shows the **"Use This Name"** card
  and no auto-select occurs — that's expected; drive it via **Use This Name**.

---

## DD-CONSOLIDATION-STAGE2 — Deep Diagnostic flow: merge the location confirmations into the options panel

**Shipped:** 2026-09-11 · **Area:** Deep Diagnostic (create-report flow) · **Type:** UX consolidation (stage 2 of 3)

### What changed
Two separate location-confirmation **screens** are removed; both now render **inline in the options
panel** (`#ir-step3`), so the whole confirm-and-run experience is one panel with a single **Run**:
1. **"Organization Coverage" screen (`ir-step2c`) removed.** For a Specialty Practice, discovered
   locations now render in a **Locations Analyzed** section inside the panel
   (`#ir-locations-section`). No separate "Confirm Organization →" click. Confirmed locations are
   computed from the checkboxes at Run time.
2. **Post-Run "Practice Composite — Confirm Practice List" screen (`ir-step-practice-discovery`)
   removed.** Turning on **Practice Composite** now runs discovery **immediately** and shows the
   practices table **inline** (`#ir-composite-section` → `#ir-pd-tbody`). **Run submits directly** —
   there is no second confirmation after clicking Run.

This kills the prior **contradiction** where the coverage screen said "single location" while the
composite screen found many practices — they're now one panel, clearly labeled, before Run.

### Files changed
- `web/index.html` only. Removed `#ir-step2c` and `#ir-step-practice-discovery` cards; added
  `#ir-locations-section` and `#ir-composite-section` inside `#ir-step3`. JS: `irStartDiscovery`
  (renders inline), `renderIrSiblings` (now sets the org display/title), deleted `irConfirmOrg`;
  `_submitIndividual` computes `_irConfirmedSiblings` from checkboxes at run; `onPracticeCompositeChange`
  + new `onPhysicianCompositeChange` trigger inline discovery; `openPracticeDiscovery` →
  `runCompositeDiscovery` (inline, no screen switch); `runIndividual` collects the roster inline;
  deleted `confirmPracticeRoster` and `irBackFromPracticeDiscovery`; `onBriefingToggle` triggers inline
  composite discovery when it force-enables composite.

### Test Cases

**T1 — Specialty: locations render inline, no coverage screen**
- Given a Specialty run reaches the panel (`OrthoSouth` / Memphis / TN / Orthopedics).
- Then there is **no separate "Organization Coverage" screen** and **no "Confirm Organization" button**.
- And a **Locations Analyzed** section appears in the panel, showing a "Discovering…" spinner then the
  location checklist (anchor pinned + any siblings).
- And unchecking a sibling and running **excludes** it (verify via R1).

**T2 — Practice Composite: inline table, no post-Run screen**
- Given the specialty panel, When I check **Practice Composite**.
- Then a **Practice Composite — Reputation Table** section appears inline with a spinner, then the
  discovered practices table (anchor row marked "analyzed", others checkable).
- And when I click **Run Diagnostic**, it starts the job **directly** — **no** "Confirm Practice List"
  screen appears afterward.
- And unchecking Practice Composite **hides** the table.

**T3 — Physicians in composite (inline re-discovery)**
- Given Practice Composite is on and its table is shown, When I check **Include Physicians in Composite**.
- Then the composite table **re-discovers** and shows physician sub-rows under the anchor practice.
- And Run includes those checked physicians.

**T4 — Pulse Briefing forces composite inline**
- Given the panel, When I check **Include Pulse Briefing**.
- Then Practice Composite (and Physicians) auto-enable **and the inline composite table populates**
  (no post-Run screen).

**T5 — Empty composite guard**
- Given Practice Composite on, When I uncheck every checkable practice and click **Run**.
- Then an inline error asks me to select at least one practice (or turn composite off); no job starts.

### Regression Checks
- **R1** A specialty run with a sibling unchecked completes and the excluded location is **not** in the
  report scope.
- **R2** A specialty **Practice Composite** run completes and the resulting report contains the
  reputation table for the checked practices (and physicians if selected).
- **R3** Hospital and FQHC runs are unaffected (they never showed these location screens).
- **R4** Service-line detection still fires and, when accepted, discovery/locations render inline.
- **R5** No console errors across: reach panel, toggle composite on/off, toggle physicians, toggle
  briefing, Run, Search Again.
- **R6 [code]** No references remain in `web/index.html` to `ir-step2c`, `ir-step-practice-discovery`,
  `openPracticeDiscovery(`, `confirmPracticeRoster(`, `irBackFromPracticeDiscovery`, or `irConfirmOrg(`
  (comments excepted).

### Acceptance Checklist
- [ ] T1 locations inline, no coverage screen
- [ ] T2 composite inline, no post-Run screen
- [ ] T3 physicians re-discover inline
- [ ] T4 briefing forces inline composite
- [ ] T5 empty-composite guard
- [ ] R1 excluded sibling honored
- [ ] R2 composite report correct
- [ ] R3 hospital/FQHC unaffected
- [ ] R4 service-line detection intact
- [ ] R5 no console errors
- [ ] R6 [code] no dangling refs

---

## VERSION-1.08 — Version bump + release notes

**Shipped:** 2026-09-11 · **Area:** Version / Release Notes · **Type:** metadata + content (no logic)

### What changed
`_APP_VERSION` → `1.08`; sidebar shows "Pulse Version 1.08"; Release Notes page gets a new **Version
1.08** section (New Features + Improvements) above the retained 1.07 history block.

### Test Cases
- **T1** Sidebar footer reads **Pulse Version 1.08**; the build line below shows `v1.08 · <commit> · <time>`.
- **T2** Release Notes page shows a **Version 1.08** card at the top with the New Features / Improvements
  entries; the **Version 1.07** card still appears below it.

### Regression Checks
- **R1 [code]** `GET /api/version` returns `{"version":"1.08", ...}` (server `_APP_VERSION`).
- **R2 [code]** `server.py` compiles; page JS `node --check` passes.

### Acceptance Checklist
- [ ] T1 sidebar 1.08
- [ ] T2 release notes 1.08 card present, 1.07 retained
- [ ] R1 /api/version = 1.08
- [ ] R2 [code] compiles / JS valid

---

## CONTENT-LEAPFROG-VACUUM — Missing-Leapfrog prescription explains the "vacuum gets filled" exposure

**Shipped:** 2026-09-11 · **Area:** Content analysis / drafting (`leapfrog_submission`) · **Type:** prescription content

### What changed
When a hospital has **no** Leapfrog grade submitted (missing / "grade not available"), the drafted
prescription for that finding now opens with a short "why this matters" paragraph: the absence isn't
neutral — AI models fill the vacuum with the next-most-visible safety signals (CMS star ratings,
HCAHPS, infection data, news coverage, reviews), so real exposure depends on what that second-best
signal says (strong CMS stars → less exposure; a lawsuit or bad infection report → more). This framing
is added **only for the missing-grade case**, not for a low D/F grade.

### Files changed
- `perception/content_drafting.py` — `_SYSTEM` prompt, `leapfrog_submission` guidance.

### Test Cases
**T1 — Missing-grade prescription includes the vacuum framing**
- Given a hospital / network with at least one hospital that has **no Leapfrog grade**, run a report
  that drafts the prescription (Hospital Network **Full Detail**, or the detailed Content Report).
- Then the drafted action plan for the "no Leapfrog Hospital Safety Grade" finding **opens with the
  "vacuum gets filled" explanation** — mentions models substituting CMS stars / HCAHPS / infection
  data / news / reviews, and that exposure depends on the second-best signal.
- And it still includes the operational steps (register at leapfroggroup.org, survey sections,
  deadlines `[VERIFY: current cycle dates]`, internal owner).

**T2 — Low-grade (D/F) prescription does NOT include the vacuum framing**
- Given a hospital with a **D or F** Leapfrog grade, draft its prescription.
- Then the plan focuses on improving the grade and does **not** include the missing-grade "vacuum" paragraph.

### Regression Checks
- **R1** Non-safety drafted findings (schema, llms.txt, reputation, etc.) are unchanged.
- **R2 [code]** `perception/content_drafting.py` compiles (`python -m py_compile`).

### Acceptance Checklist
- [ ] T1 missing-grade prescription has the vacuum explanation + operational steps
- [ ] T2 low-grade prescription omits the vacuum explanation
- [ ] R1 other prescriptions unchanged
- [ ] R2 [code] module compiles

### Notes for the testing agent
- This affects **drafted** output only (Full Detail / detailed Content Report), not the lightweight
  Content Improvement Keys summary. Drafting is an LLM step — wording will vary; assert on the
  *presence of the concepts* (vacuum / substitution of CMS/HCAHPS/infection/news/reviews / second-best
  signal), not exact text.

---

## DD-CONSOLIDATION-STAGE3 — FQHC flow: confirmed already 2-screen (no fold required)

**Shipped:** 2026-09-11 · **Area:** Deep Diagnostic (create-report flow) · **Type:** consolidation review (stage 3 of 3)

### What changed / decision
No code change to FQHC in this stage. **Rationale:** after Stage 1 (auto-select), the FQHC path is
already a clean 2-screen flow: **Search → auto-selected org → Client Attestation intake card (which
is its Run panel) → Confirm & Run**. The intake is **required input**, not a redundant confirmation
gate like the practice double-discovery was — so it is intentionally *not* folded into the
practice-oriented options panel (which carries profile/composite/briefing options that don't apply
to FQHC). Folding it would add risk and worsen UX. If the product owner still wants a single
combined panel for FQHC, that's a follow-up (tracked as a future change).

### Test Cases
**T1 — FQHC 2-screen flow works**
- Given **Community Health (FQHC)**, a known org, **Search**.
- Then the top match auto-selects and the **Client Attestation / intake** card appears directly.
- And filling the attestation and clicking **Confirm & Run** starts the report.
- And there is **no** practice profile / composite / coverage UI shown.

### Regression Checks
- **R1** FQHC intake validation still works (required fields enforced by `irConfirmIntake`).
- **R2** No console errors on FQHC search → intake → run and Search Again.

### Acceptance Checklist
- [ ] T1 FQHC 2-screen flow
- [ ] R1 intake validation intact
- [ ] R2 no console errors

---

## CI-DEPLOY-WIF — GitHub Actions deploys to Cloud Run on push to main (keyless auth)

**Shipped:** 2026-09-11 · **Area:** Deployment / infra · **Type:** infra (no UI change)

### What changed
- `.github/workflows/deploy.yml` rewritten. It had been **failing on every push** since June (no
  `GCP_SA_KEY` secret) and carried a stale DuckDB-era config (min 0 / max 2 instances, half the
  secrets missing). It now authenticates via **Workload Identity Federation** (no stored key) and
  runs **`bash deploy.sh`**, so the Cloud Run flags live in exactly one place.
- `deploy.sh`: honors `PROJECT_ID` env, tags images `app:<short-sha>` (rollback-friendly), passes
  `--project`/`--quiet` so it runs non-interactively. Docker is no longer a prerequisite.
- New one-time script `scripts/setup_github_deploy.sh` creates the `github-deploy` SA, roles, WIF
  pool/provider (locked to `tyjallen44/Rank2`), and the impersonation binding. Idempotent.
- Post-deploy **smoke check**: polls `/api/version` up to 3 min until `commit` equals the pushed
  short SHA; fails the run otherwise.

### Files changed
`.github/workflows/deploy.yml`, `deploy.sh`, `scripts/setup_github_deploy.sh`

### Test Cases
**T1 — Push to main deploys**
- Given the one-time setup script has been run once, push any commit to `main`.
- Then the "Deploy to Cloud Run" run goes green and its summary shows `Deployed <sha> → <url>`.
- And `GET https://rank2-883710187036.us-central1.run.app/api/version` returns `"commit": "<sha>"`.

**T2 — Manual deploy**
- `gh workflow run 'Deploy to Cloud Run' && gh run watch` → same result as T1.

**T3 — Local deploy still works**
- `bash deploy.sh` from a laptop with gcloud login → deploys the same way (no Docker needed).

### Regression Checks
- **R1** Live service config unchanged after CI deploy: 2Gi / 1 CPU / min 1 / max 10 /
  session-affinity / no-cpu-throttling / all 6 secrets mounted / `/data` GCS volume.
- **R2** Login (password + Google SSO) and report generation work on the new revision.
- **R3** Two rapid pushes queue (concurrency group) rather than racing; no cancelled deploy.

### Acceptance Checklist
- [ ] T1 push → green run → live commit matches
- [ ] T2 manual dispatch works
- [ ] T3 local deploy.sh works
- [ ] R1 service config unchanged
- [ ] R2 login + report generation OK
- [ ] R3 queued deploys

### Notes for the testing agent
NEEDS BROWSER TESTING only for R2 (sanity on the deployed revision). The rest is verifiable from
the GitHub Actions tab and `/api/version`. The first run after this commit is **expected to fail**
until `scripts/setup_github_deploy.sh` has been executed once by a project owner.

---

## UI-RLDATIX-LOGO — RLDatix wordmark above every page title

**Shipped:** 2026-09-11 · **Area:** App shell (all pages) · **Type:** UI

### What changed
- New asset `web/assets/logo-dark.svg` (RLDatix wordmark, dark teal, 156×30) served at
  `/assets/logo-dark.svg` by a small route in `server.py` placed ahead of the SPA catch-all
  (basename-only, 404 on unknown file, 1-day cache header).
- CSS: `.page-title::before` renders the wordmark as a 156×30 block, left-aligned, 16px above
  the title on every page (Market Pulse, Hospital Network, Deep Diagnostic, Competitors Rankings,
  Compare Two, Event Prep, History, Trends, Feedback, Admin, Learn, Release Notes).
- Partner brands (`extension1` Montecito, `extension2` Ashleigh Jane) hide it.

### Files changed
`server.py`, `web/index.html`, `web/assets/logo-dark.svg`

### Test Cases
**T1 — Logo above title (default brand)**
- Log in as an RLDatix/admin user. On each Report Creation page the RLDatix wordmark sits
  directly above the uppercase page title, left-aligned with it, with no layout shift below.

**T2 — Asset served**
- `GET /assets/logo-dark.svg` → 200, `image/svg+xml`. `GET /assets/missing.svg` → 404.

**T3 — Partner brands unaffected**
- Log in with a Montecito or Ashleigh Jane password: no RLDatix logo on any page.

### Regression Checks
- **R1** Page subtitles and forms are unchanged below the title (spacing only added above).
- **R2** Tablet (≤860px) and phone widths: logo scales/holds 156px and doesn't overflow.
- **R3** SPA routes (e.g. `/learn`, `/methodology`) still resolve to the app; `/assets/..` doesn't
  expose server files (returns the app shell).

### Acceptance Checklist
- [ ] T1 logo on all pages (default brand)
- [ ] T2 asset route 200 / 404
- [ ] T3 partner brands show no logo
- [ ] R1 layout below title unchanged
- [ ] R2 responsive widths
- [ ] R3 routing intact

### Notes for the testing agent
NEEDS BROWSER TESTING. Verified locally via headless Chromium on Hospital Network and Deep
Diagnostic (default brand) and Ashleigh Jane (hidden). The wordmark file is the "dark" variant
intended for light backgrounds; report PDFs are not touched.

---

## CI-MANUAL-DEPLOY — Production deploys are manual (push to main no longer deploys)

**Shipped:** 2026-09-11 · **Area:** Deployment / infra · **Type:** infra (no UI change)

### What changed
- Workflow trigger is now `workflow_dispatch` only. Pushing to `main` does nothing in production.
- `bash deploy.sh` (from a laptop, no args) now **ships**: it refuses if local main has unpushed
  commits, warns on uncommitted changes, dispatches the GitHub Actions deploy of `origin/main`,
  watches it, then prints the status. `bash deploy.sh --local` keeps the old direct Cloud Build path.
  CI still runs the direct path (`CI=true`).
- New `scripts/deploy_status.sh`: compares the live `/api/version` commit with `origin/main` and
  lists the not-yet-deployed commits, plus unpushed/uncommitted warnings.

### Files changed
`.github/workflows/deploy.yml`, `deploy.sh`, `scripts/deploy_status.sh`

### Test Cases
**T1 — Push does not deploy**: push a commit to main → no new "Deploy to Cloud Run" run appears.
**T2 — Status**: `bash scripts/deploy_status.sh` shows live commit, origin/main, and pending list.
**T3 — Ship**: `bash deploy.sh` → dispatches run, watches to green, smoke check passes, status
shows "up to date".
**T4 — Guard**: with an unpushed local commit, `bash deploy.sh` exits 1 with a push hint.

### Regression Checks
- **R1** `bash deploy.sh --local` still deploys directly (needs gcloud login).
- **R2** Manual run from the Actions tab ("Run workflow") still works.

### Acceptance Checklist
- [ ] T1 push is inert
- [ ] T2 status output
- [ ] T3 ship end-to-end
- [ ] T4 unpushed guard
- [ ] R1 --local path
- [ ] R2 Actions-tab run

### Notes for the testing agent
No browser testing needed. T3 deploys to production — run it only when a deploy is wanted.

---

## EVENT-PRACTICE-COMBINED — Event Prep practice attendees get the combined practice report

**Shipped:** 2026-09-13 · **Area:** Event Preparation · **Type:** feature

### What changed
- New per-event option **"Include content analysis & prescription (combined practice report)"**,
  shown only when Entity Type = Specialty Practice, **checked by default**. Persisted as
  `event_runs.practice_content` (auto-migrated) so **Resume** repeats it.
- For each practice attendee, after the four-pillar analysis the event job calls the same
  `_finalize_practice_combined` step Deep Diagnostic uses: content analysis (website from the CSV's
  `url` column if present, else the Google listing's site) → drafted prescription → findings-citing
  Assessment → combined PDF **replacing the base PDF in the event folder** (so the ZIP picks it up).
  Then the usual `EventReport` rename applies.
- With **teaser** also checked, the combined teaser (blurred content) is produced and renamed to
  `<EventReport stem>_Teaser.pdf`; the legacy score-only teaser is skipped for those practices.
- Fail-soft: any content/render error logs `⚠ Content analysis failed … base report kept` in the
  event stream and the attendee still completes with the base report.
- `_finalize_practice_combined` now writes next to the base PDF (event folder for events;
  REPORTS_DIR for Deep Diagnostic — unchanged behavior there).

### Files changed
`server.py`, `perception/db.py`, `web/index.html`

### Test Cases
**T1 — Option visibility**: Event Prep → Hospital: no practice box. Specialty Practice: green box
with the checkbox **checked**. FQHC: box hidden, FQHC composite box shown.
**T2 — Practice event with content**: 2–3 practice rows (one with a `url`), run with the box
checked. Each attendee's stream shows "Content analysis for <name>" then phase lines (content /
drafting / assessment / pdf). The ZIP contains one `…EventReport.pdf` per practice that is the
**combined** report (Diagnostic Assessment cites findings; embedded Content Report + prescription).
**T3 — Unchecked**: same rows with the box unchecked → plain four-pillar EventReport PDFs, faster.
**T4 — Teaser + content**: check both → ZIP has `…EventReport.pdf` and `…EventReport_Teaser.pdf`
where the teaser blurs the content section but keeps score/Assessment.
**T5 — Resume**: kill/skip an entity mid-run, Resume → the resumed attendee also gets the combined
report (setting persisted).

### Regression Checks
- **R1** Hospital and FQHC events unchanged (no content step, no new files).
- **R2** Deep Diagnostic practice report still writes to REPORTS_DIR and downloads from History.
- **R3** Enriched CSV scores/grades unchanged; entity counts done/skipped correct.
- **R4** A practice whose content analysis fails still counts as done with the base PDF.

### Acceptance Checklist
- [ ] T1 visibility / default
- [ ] T2 combined PDFs in ZIP
- [ ] T3 unchecked = base PDFs
- [ ] T4 teaser variant
- [ ] T5 resume persists
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. Runtime: content analysis adds roughly 2–4 minutes per practice (5 run
in parallel). Unit suite: 322 pass / 15 fail — the 15 failures are pre-existing (grade-token and
rebrand assertions) and identical on the previous commit.

## NETWORK-BULK-ADMIN-ONLY — Hospital Network "Single / Bulk List (CSV)" toggle is admin only

**Shipped:** 2026-09-14 · **Area:** Hospital Network / History · **Type:** UX + access control

### What changed
- The mode toggle at the top of the Hospital Network page (**Single Network** / **Bulk List (CSV)**)
  is now hidden for non-admin users. It defaults to `display:none` and `showApp()` reveals it only
  when `_role === 'admin'`. Non-admins are pinned to Single Network on login.
- Server: `POST /api/network/bulk/run` and `POST /api/network/bulk/{id}/resume` now require the
  admin role (`Depends(require_admin)` → 403 otherwise). Listing bulk runs and downloading the
  enriched CSV are unchanged (any authenticated user).
- History → National Entity Runs: the **↻ Resume** action is only rendered for admins (Delete
  already was). Scores CSV download still shows for everyone.
- Release note copy updated to say "admins can switch to Bulk List".

### Files changed
`server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Non-admin view**: log in as a non-admin user → Hospital Network page shows the title,
subtitle, then the Step 1 form directly. No Single/Bulk toggle. The bulk upload card is not visible.
**T2 — Admin view**: log in as admin → toggle appears above Step 1, defaults to Single Network;
clicking Bulk List (CSV) swaps in the upload card exactly as before.
**T3 — Server gate**: with a non-admin token, `curl -X POST /api/network/bulk/run` (any file) and
`POST /api/network/bulk/<id>/resume` return **403 "Admin access required"**. Same calls with an
admin token behave as before.
**T4 — History actions**: non-admin History → National Entity Runs shows only "Scores CSV" for
finished runs and no actions for unfinished ones. Admin sees Resume (unfinished) and Delete.
**T5 — Role switch in one browser**: log in as admin, click Bulk List, log out, log in as non-admin
→ page is back on Single Network with the Step 1 form visible (no stale bulk state).

### Regression Checks
- **R1** Single Network report run (all three variants) unchanged for both roles.
- **R2** Admin bulk run end-to-end: upload → progress → enriched CSV download still works.
- **R3** `GET /api/network/bulk/runs` and `GET /api/network/bulk/<id>/csv` still work for non-admins.
- **R4** The "Override today's cache lock (Admin only)" checkbox visibility is unchanged.

### Acceptance Checklist
- [ ] T1 non-admin: no toggle, Step 1 visible
- [ ] T2 admin: toggle works
- [ ] T3 403 for non-admin bulk run/resume
- [ ] T4 History actions by role
- [ ] T5 no stale bulk state after role switch
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. Requires one admin and one non-admin account. No data migration.

## COMPOSITE-AUTO-SCOPE — Practice Composite table auto-scoped to the confirmed Locations list

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic (Specialty Practice / service line), Compare Two, Event Prep · **Type:** correctness + UX

### What changed
- `analyze_practice` now records the sibling roster used for the aggregate analysis
  (`_aggregate_siblings`: confirmed siblings from the UI, or the service-line / practice sibling
  discovery result) and reuses it as the Practice Composite reputation roster. Previously the
  composite table came from a separate **system-wide** `discover_practices` call, so a
  "Houston Methodist Orthopedics" run could list imaging / primary-care sites in the table.
- Deep Diagnostic UI (Specialty type only): checking **Practice Composite** no longer calls
  `/api/practice/discover`. The composite panel shows the anchor row (+ physician sub-rows if
  that option is on) and the note "The reputation table automatically uses the locations checked
  in the Locations list above." The count line reads "N locations from the Locations list".
- Hospital type is unchanged (still discovers affiliated practices and shows the checkbox list).
- Stream shows `Reputation table scoped to the N confirmed location(s)` when auto-scoping applies.

### Files changed
`perception/practice_analyzer.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Service line + composite**: Deep Diagnostic → Specialty Practice, HOUSTON METHODIST
ORTHOPEDICS / HOUSTON / TX, Specialty ORTHOPEDICS → Search → "Analyze as service line". In the
Locations list uncheck one clinic. Check Practice Composite → no discovery spinner call for
practices; note text + "N locations from the Locations list" shown. Run. The PDF's Practice
Composite table has exactly the anchor + the checked clinics, and the unchecked clinic is absent.
No non-ortho Houston Methodist facilities appear.
**T2 — Independent practice + composite**: a standalone multi-location practice (not a service
line). Composite table rows = anchor + the locations checked in the Locations list.
**T3 — Physicians sub-option**: T1 with "Include Physicians in Composite" checked → physician
sub-rows still render under the anchor row in the panel and in the PDF.
**T4 — Hospital type**: Hospital / Health System with Practice Composite → the affiliated-practice
checkbox list still populates via discovery and pruning there still controls the table.
**T5 — Event Prep practice row with a service line** (e.g. `Houston Methodist Orthopedics,
Houston,TX,,Orthopedics` with the combined practice option) → composite table, if produced, is
limited to the discovered ortho clinics.

### Regression Checks
- **R1** Anchor row still pinned first with the header star rating; "Not established" rows unchanged.
- **R2** Single-location specialty run (no siblings) with composite → table has the anchor only.
- **R3** Compare Two with service-line sides unaffected in scores.
- **R4** Unit suite: same 4 pre-existing failures as before this commit (practice_reputation URL
  columns + rebrand token), 108 pass in the practice/service-line selection.

### Acceptance Checklist
- [ ] T1 auto-scoped table matches Locations list
- [ ] T2 independent practice
- [ ] T3 physicians
- [ ] T4 hospital path unchanged
- [ ] T5 Event Prep
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. T1 runtime roughly 5–8 minutes. Compare the PDF table against the
Locations list you confirmed before running.

## DD-SPECIALTY-STREAMLINE — Deep Diagnostic specialty flow: fewer decisions before Run

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic · **Type:** UX streamlining (mirrors the Hospital Network "always generate" change)

### What changed
1. **Teaser always produced** (all Deep Diagnostic types). The "Create Teaser Report" checkbox is
   gone; `_submitIndividual` sends `teaser_report: true`. A note under Options says both the report
   and the Teaser are produced automatically.
2. **Practice Composite always on for Specialty Practice.** The "Practice Composite" checkbox is
   hidden and forced on for the Specialty type; "Include Physicians in Composite" is now a
   top-level opt-in (enabled without a parent checkbox). The reputation roster is the confirmed
   Locations list (see COMPOSITE-AUTO-SCOPE). Hospital type keeps the explicit checkbox and the
   affiliated-practice list exactly as before.
3. **Service line auto-accepted.** The "Service Line Detected" decision card is removed. When
   detection resolves a parent system, discovery runs immediately as that service line and the
   Selected Organization box shows a **"Service line"** badge with "treat as entered instead". After
   opting out, the box offers an "analyze as the X service line of Y" link to re-apply it.
4. **Practice Profile as a badge.** The options panel shows "auto-classified as <Label> · change".
   The dropdown is hidden until "change" is clicked; changing it updates the badge label + description.
- Help modal step 6 and the "If the badge doesn't appear" heading updated to match.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Service line auto-accept**: Specialty Practice, HOUSTON METHODIST ORTHOPEDICS / HOUSTON / TX,
Specialty ORTHOPEDICS → Search. No decision card. Locations discovery starts immediately; the
Selected Organization box shows the teal "Service line" badge naming Orthopedics / Houston Methodist
and the "treat as entered instead" link. Locations list is ortho clinics only.
**T2 — Opt out / re-apply**: click "treat as entered instead" → discovery re-runs for the listing as
entered; the box now says "Analyzed as entered · analyze as the Orthopedics service line of Houston
Methodist". Click that link → back to service-line discovery with the badge.
**T3 — Profile badge**: options panel shows "auto-classified as Procedural · change" with no
dropdown. Click change → dropdown appears (link hides); pick Relationship → badge reads
"Relationship" and the description line updates. Run → report uses the chosen profile.
**T4 — Composite implicit**: no "Practice Composite" checkbox for Specialty; note says the
per-location reputation table is included. Run without touching anything → PDF contains the
Practice Composite table scoped to the checked locations, plus a teaser PDF.
**T5 — Physicians opt-in**: check "Include Physicians in Composite" (enabled, top-level) → composite
panel appears with anchor row + physician sub-rows; uncheck → panel hides. Run with it checked →
physician sub-rows in the PDF.
**T6 — Pulse Briefing interplay**: check Briefing → physicians forced on + panel shown; uncheck →
physicians cleared but still enabled (specialty).
**T7 — Hospital type unchanged**: Hospital / Health System → "Composite analysis related hospitals
only", "Practice Composite" checkbox (physicians disabled until checked), no profile row, no badge.
Teaser produced automatically.
**T8 — Type switching**: pick Specialty then Hospital then Specialty → controls reset correctly each
time (composite hidden/forced for Specialty, shown/unchecked for Hospital).

### Regression Checks
- **R1** Community Health (FQHC) flow unaffected (intake step, no composite, teaser produced).
- **R2** Search Again / navigating away and back resets the badge, profile dropdown, and composite panel.
- **R3** Compare Two and Event Prep untouched.
- **R4** History shows the Teaser download for new Deep Diagnostic runs of every type.

### Acceptance Checklist
- [ ] T1 auto-accept + badge
- [ ] T2 opt out / re-apply
- [ ] T3 profile badge + change
- [ ] T4 composite implicit + teaser
- [ ] T5 physicians opt-in
- [ ] T6 briefing interplay
- [ ] T7 hospital unchanged
- [ ] T8 type switching
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. Front-end only; no server or schema changes in this commit. Pair with
COMPOSITE-AUTO-SCOPE (previous commit) for the end-to-end service-line run.

## DD-SERVICE-LINE-TYPE — "Hospital Service Line" is its own Deep Diagnostic analysis type

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic, History · **Type:** feature + UX

### What changed
- Analysis Type toggle is now **Hospital · Hospital Service Line · Specialty Practice · Community
  Health (FQHC)**. The first label was "Hospital / Health System"; health-system analysis is the
  Hospital Network report's job. (Other pages' toggles are unchanged.)
- **Hospital Service Line** type: fields become **Health System** + Location + **Service Line**.
  Search queries Google Places for "<system> <line>" to pick the flagship anchor listing (top match
  auto-selected). The profile auto-classifies from the service line, then location discovery runs
  **directly** with `service_line` + `parent_system` (`/api/practice/siblings`) — no LLM
  detect-service-line call and no opt-out link. Selected Organization shows the "Service line" badge.
- **Specialty Practice** no longer auto-accepts a detected service line. If detection fires, the
  Selected Organization box shows an amber hint "This looks like the X service line of Y ·
  **switch to Hospital Service Line**" which resets, flips the type, prefills both fields and
  re-runs the search. Otherwise the practice is analyzed as entered.
- Both practice types share the run path (entity_type `practice`, composite implicit, teaser
  always). The service-line type also sends `service_line` + `parent_system` in the analyze body.
- Persistence: `AnalysisResult.service_line` / `parent_system` → new nullable columns
  `analysis_runs.service_line`, `analysis_runs.parent_system` (auto-migrated in `init_db`), written
  by `_save_practice_extras`, returned by `query_history` (network rows return null).
- **History badge**: service-line runs show `Service Line · ORTHOPEDICS` (gold, parent system in
  the tooltip) instead of the bare specialty badge.
- Help modal rewritten for the new type; page subtitle + help link text updated.

### Files changed
`web/index.html`, `server.py`, `perception/practice_analyzer.py`, `perception/models.py`,
`perception/db.py`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Toggle + fields**: Deep Diagnostic → four types, first reads "Hospital". Pick Hospital Service
Line → labels read Health System / Service Line, a note explains the type, placeholders change.
Switch back to Hospital → labels revert and the Specialty field hides.
**T2 — Service line run**: Health System HOUSTON METHODIST, HOUSTON / TX, Service Line ORTHOPEDICS →
Search. Candidates come back for "Houston Methodist Orthopedics"; top auto-selected. Locations
discovery starts immediately (no detection pause); the "Service line" badge names Orthopedics /
Houston Methodist with **no** opt-out link. Locations list = ortho clinics only. Run → practice-rubric
report titled "HOUSTON METHODIST ORTHOPEDICS" with the per-location table + teaser.
**T3 — Validation**: Service Line type with an empty Service Line → "Please enter the service line";
empty system → "Please enter the health system name".
**T4 — Specialty hint**: Specialty Practice, HOUSTON METHODIST ORTHOPEDICS / HOUSTON / TX, Specialty
ORTHOPEDICS → Search. Discovery runs for the listing as entered; the Selected Organization box shows
the amber "This looks like the Orthopedics service line of Houston Methodist · switch to Hospital
Service Line" hint. Click it → type flips, fields prefilled (HOUSTON METHODIST / ORTHOPEDICS), search
re-runs on the service-line path (T2 state).
**T5 — Standalone specialty unchanged**: an independent practice (no parent system) → no hint,
analyzed as entered, composite scoped to its Locations list.
**T6 — History**: after T2, History shows a gold "Service Line · ORTHOPEDICS" badge (hover shows
Houston Methodist). The T4-as-entered run (if completed) shows the plain "ORTHOPEDICS" badge.
**T7 — Migration**: on first start after deploy, `init_db` adds `service_line` / `parent_system`
to `analysis_runs`; History loads for admin and non-admin without errors; older rows show as before.

### Regression Checks
- **R1** Hospital type: composite checkbox + affiliated-practice list unchanged; teaser produced.
- **R2** FQHC flow unchanged.
- **R3** Compare Two / Event Prep service-line paths unchanged (they pass service_line explicitly).
- **R4** Network rows in History unaffected (service_line null).
- **R5** Unit suite: same 4 pre-existing failures (practice_reputation URL columns + rebrand token).

### Acceptance Checklist
- [ ] T1 toggle/labels
- [ ] T2 service-line run end-to-end
- [ ] T3 validation
- [ ] T4 specialty hint → switch
- [ ] T5 standalone specialty
- [ ] T6 History badge
- [ ] T7 migration
- [ ] R1–R5

### Notes for the testing agent
NEEDS BROWSER TESTING. The Postgres migration was NOT smoke-tested locally (only a shared
DATABASE_URL is configured); it uses the existing add-column-if-missing loop in `init_db`. T7 is
the first thing to check after deploy.

## DD-SERVICE-LINE-MARKET — Hospital Service Line type: no City/ZIP toggle, "Market" city + state only

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic · **Type:** UX

### What changed
- When Analysis Type = **Hospital Service Line**, the By City / By ZIP Code toggle is hidden and
  the form is pinned to city mode. The section label reads **Market** with the hint "The metro to
  analyze. Clinics of this service line within about 50 miles are discovered." City + State inputs
  are unchanged (they still feed the anchor search and scoped discovery).
- Switching to any other type restores the toggle and the "Location" label.
- Help modal step 2 explains that for a multi-state system the city picks the metro.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Toggle hidden**: pick Hospital Service Line → no By City / By ZIP buttons; label "Market" +
hint; City and State inputs visible; ZIP row hidden.
**T2 — Pinned to city**: choose Hospital, switch to By ZIP Code, then pick Hospital Service Line →
the form flips to City/State (ZIP row hidden). Search with city/state works as in
DD-SERVICE-LINE-TYPE T2.
**T3 — Restore**: from Hospital Service Line switch to Specialty Practice → toggle back, label
"Location", hint gone, city mode still selected.
**T4 — Page reload / navigate away and back**: defaults to Hospital with the toggle visible.

### Regression Checks
- **R1** Hospital / Specialty / FQHC types: By ZIP Code search unchanged.
- **R2** Service-line validation messages unchanged (system + service line required).

### Acceptance Checklist
- [ ] T1 hidden toggle + Market label
- [ ] T2 pinned to city after ZIP
- [ ] T3 restore on type switch
- [ ] T4 defaults
- [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Front-end only.

## DD-TYPE-FIRST — Deep Diagnostic: Analysis Type moved to the top of the search form

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic · **Type:** UX

### What changed
- Step 1 field order is now: **Analysis Type** → Organization Name / Health System → Specialty /
  Service Line (practice types only) → Location / Market (city + state, or ZIP) → Search.
  Previously the type toggle sat third, after the user had already typed a name and location whose
  labels/behavior then changed.
- The service-line explainer note now sits directly under the toggle ("Enter the health system and
  the service line below"). No ids, handlers, or validation changed.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Order**: open Deep Diagnostic → Analysis Type is the first control; Organization Name next;
Location last before Search. Specialty/Service Line field appears between name and location only
for Specialty Practice / Hospital Service Line.
**T2 — Type-first adaptation**: pick Hospital Service Line before typing → labels already read
Health System / Service Line / Market with the note under the toggle; no City/ZIP toggle.
**T3 — Enter key**: pressing Enter in any input still runs Search.
**T4 — Search paths**: one Hospital run by ZIP and one Specialty run by city behave as before.

### Regression Checks
- **R1** Page reset (navigate away/back) still defaults to Hospital, city mode, empty fields.
- **R2** Compare Two, Event Prep, Content Analysis forms untouched.

### Acceptance Checklist
- [ ] T1 order
- [ ] T2 type-first adaptation
- [ ] T3 Enter key
- [ ] T4 search paths
- [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Markup reorder only.

## DD-CONFIRM-SIMPLE — Deep Diagnostic confirmation page simplified; Google-seeded locations; name dedupe

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic (all types; mainly Specialty / Service Line) · **Type:** UX + correctness

### What changed
**Correctness**
- **Service-line name dedupe.** If the Health System field already contains the service line
  ("HOUSTON METHODIST ORTHOPEDICS" + "ORTHOPEDICS"), the line is stripped (also for spelling
  variants such as ORTHOPAEDICS) and the field is rewritten to the bare system. The brand /
  report title is no longer doubled.
- **Locations seeded from Google.** After discovery, same-brand Google candidates from the search
  (token overlap ≥ 0.6 with the chosen listing, other streets) are added to the Locations list
  with their address, rating, and **place_id**. Same-name listings are labelled "Name (street)".
  Discovery results that describe the same listing are dropped in favour of the seeded row.
- **Pinned Google profiles server-side.** `search_entity_candidates` now returns `place_id` and
  `maps_url`. `collect_platform_data` uses an entry's `place_id`/rating/count verbatim ("pinned")
  before any name-based lookup, so five same-name clinics keep five distinct profiles. The chosen
  anchor listing is passed as `anchor_listing` (new `AnalyzeRequest` field) and pinned too; the
  anchor-duplicate check trusts a distinct `place_id`.

**Layout (confirmation card)**
- Candidate cards collapse into one line once a listing is chosen: "Flagship listing: X · address
  · 4.6★ (157) · change". "change" re-opens the list. ("Google listing" wording for other types.)
- One card: **Report Title** (single editable input, prefilled) → context line (service-line badge
  or the "switch to Hospital Service Line" hint) → **Practice profile: Procedural · change** (one
  line; description is a tooltip; dropdown only on change) → **Locations (N)** list with
  address + rating per row → **Advanced options** (collapsed `<details>`: composite/physicians,
  Pulse Briefing, Website URL, admin toggles, composite table) → **Run Diagnostic** with a
  one-line "Produces the report and a teaser, plus the per-location reputation table".
- Removed: Selected Organization box, "Search anchor" line, Parent Organization block, title
  preview, the duplicate Search Again next to Run, the standalone "every run produces" paragraph.

### Files changed
`web/index.html`, `server.py`, `perception/data/places.py`, `perception/practice_reputation.py`,
`perception/practice_analyzer.py`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Dedupe**: Hospital Service Line, Health System "HOUSTON METHODIST ORTHOPEDICS", Service Line
ORTHOPEDICS, HOUSTON/TX → Search. The system field rewrites to "HOUSTON METHODIST"; the title reads
"HOUSTON METHODIST ORTHOPEDICS" (no doubled word); the context line says "ORTHOPEDICS service line
of HOUSTON METHODIST".
**T2 — Seeded locations**: same search returns ~5 "Houston Methodist Orthopedics & Sports Medicine"
listings. Locations shows the flagship row plus the other four with street addresses and star
ratings (labelled "(street)" where names repeat), plus any additional AI-discovered clinics. No
"single-location" message.
**T3 — Pinned profiles**: run T2. The PDF's per-location reputation table shows a distinct Google
rating/review count for each seeded clinic (matching the search cards), not "Not established" for
the same-name rows.
**T4 — Candidate collapse**: after Search the cards fold to one summary line with "change";
clicking change re-opens the cards; selecting another card re-collapses and updates the flagship
row + title.
**T5 — Card layout**: confirmation card shows only Title, context line, profile line, Locations,
"Advanced options" (collapsed), Run. Expanding Advanced reveals physicians / briefing / URL (+ admin
toggles for admin). Title edits still flow to the report.
**T6 — Profile change**: "change" reveals the dropdown; choosing Relationship updates the badge and
its tooltip.
**T7 — Hospital type**: single-listing hospital → collapsed "Google listing" line; card shows Title
+ Advanced (with the composite checkbox inside) + Run; no Locations section.
**T8 — Specialty hint**: Specialty Practice search that resolves to a department shows the amber
"switch to Hospital Service Line" line under the title.

### Regression Checks
- **R1** Standalone specialty practice with several Google listings → seeded locations appear;
  unchecking one removes it from the run.
- **R2** Compare Two / Event Prep untouched (no anchor_listing; name-based resolution as before).
- **R3** FQHC flow untouched.
- **R4** Unit suite: same 4 pre-existing failures; 108 pass in the practice/places selection.

### Acceptance Checklist
- [ ] T1 dedupe
- [ ] T2 seeded locations
- [ ] T3 pinned profiles in PDF
- [ ] T4 collapse/expand
- [ ] T5 card layout
- [ ] T6 profile change
- [ ] T7 hospital type
- [ ] T8 specialty hint
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. T3 is the key end-to-end check (5–8 min run). The Places field mask now
requests `places.id` and `places.googleMapsUri`; if candidates come back without ratings, check
the API key restrictions first.

## DD-RUN-GATED-ON-DISCOVERY — Run Diagnostic disabled while locations are being discovered

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic (Specialty Practice / Hospital Service Line) · **Type:** UX guard

### What changed
- When location discovery starts (`irStartDiscovery`), the **Run Diagnostic** button is disabled
  and relabelled "⟳ Discovering locations…" (tooltip: waiting for the Locations list).
- It returns to "Run Diagnostic →" and re-enables when the list renders **or** when discovery
  errors (a failed discovery still allows a single-location run).
- Re-triggers (picking another candidate via "change", or the "switch to Hospital Service Line"
  hint) disable it again for the new discovery.
- Hospital and FQHC types have no discovery and are unaffected. The optional physician roster
  lookup does not gate Run.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Gated**: Service Line search → during the 10–20 s discovery the Run button is gray with
"Discovering locations…"; clicking does nothing. When the Locations list appears the button reads
"Run Diagnostic →" and is enabled.
**T2 — Error path**: force discovery to fail (e.g. block `/api/practice/siblings` in devtools) → the
error shows under Locations and the Run button is enabled; running works as single-location.
**T3 — Re-trigger**: click "change" on the flagship line and pick another card → button gates
again, then re-enables with the refreshed list.
**T4 — Hospital / FQHC**: button never gates.

### Regression Checks
- **R1** Run still disables/relabels to "Starting…" on click and restores on error, as before.
- **R2** Physicians checkbox lookup does not disable Run.

### Acceptance Checklist
- [ ] T1 gated during discovery
- [ ] T2 error re-enables
- [ ] T3 re-trigger
- [ ] T4 other types unaffected
- [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Front-end only.

## PRACTICE-PILLAR2-ROSTER — Reviews & Reputation pillar computed from the confirmed location roster

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic (Specialty / Service Line), Compare Two, Event Prep practice rows · **Type:** scoring correctness

### What changed
Root cause: Pillar 2 was rewritten after the LLM from ONE Google text search on the anchor name.
The name matcher strips "hospital/medical/center", so "Houston Methodist Orthopedics" bound to
*Houston Methodist Hospital* (thousands of reviews → band 92) while the per-location table showed
the real clinics. The two had no shared data.

1. **Roster Google pre-pass** (`_resolve_roster_google`, before the narrative): every confirmed
   location (anchor + siblings) is pinned to one Google listing. Seeded entries with a `place_id`
   are used verbatim; others are name-resolved under a **strict gate** (strong name match AND
   healthcare category) with a collision rule (a place_id backs one entry; a sibling that resolves
   to the anchor's listing is flagged `_anchor_dup` and excluded). Emits
   `Google reviews: X.X★ across R of N location(s), T reviews total`.
2. **Pillar 2 = reviews_band(roster weighted-avg rating, roster total reviews)** whenever the
   roster aggregate exists; the single front-door listing is only the fallback. The evidence block
   now carries a "Roster Google reviews … Use THIS for Reviews & Reputation" line for the narrative.
3. **Front door pinned / strict**: when the search step supplied `anchor_listing`, the header
   rating comes from that exact place_id (no re-search). Without a pin (Compare Two / Event Prep),
   a name-searched listing must pass the strict gate or is treated as unverified.
4. **Canonical score cache is roster-aware**: `entity_scores.roster_key` (new column,
   auto-migrated) fingerprints the roster; a cached practice score is adopted only when the
   fingerprint matches, and new seeds record it.
5. Composite table reuses the same pinned dicts → the table's Google numbers equal the inputs to
   the pillar by construction. `_anchor_dup` rows are dropped from the table.

### Files changed
`perception/practice_analyzer.py`, `perception/db.py`, `tests/test_roster_reputation.py`,
`docs/qa/test-battery.md`

### Test Cases
**T1 — Service line end-to-end**: Hospital Service Line → HOUSTON METHODIST / HOUSTON / TX /
ORTHOPEDICS → confirm the seeded clinics → Run. Stream shows the "Google reviews: … across R of N
location(s)" line. In the PDF, Pillar 2 (Reviews & Reputation) is consistent with the table on the
composite pages: e.g. clinics averaging ~4.3★ with a few hundred reviews total → band in the
70s–80s, NOT 92. The header Google rating equals the flagship listing chosen in the search.
**T2 — Pillar vs table math**: sum the Google review counts and compute the count-weighted average
rating from the composite table rows (Google column) → apply the band (≥4.5→88, ≥4.0→75, ≥3.5→60;
+4 ≥400 reviews, +2 ≥100, −4 <100, −8 <25). Matches the printed Pillar 2.
**T3 — No hospital capture**: a Specialty Practice run for "<System> Orthopedics" with NO
seeded listings (block the seeding by choosing "Use This Name") → stream/PDF must not show the
parent hospital as the matched listing; front door is either the clinic or "not verified".
**T4 — Cache guard**: run T1 twice with different Locations selections (uncheck one clinic on the
second run) → the second run's pillar reflects the smaller roster (not the first run's cached score).
Running a third time with the same roster as the second adopts the cached score (same numbers).
**T5 — Compare Two / Event Prep practice**: unchanged flows still complete; the reputation pillar
for a service-line side/row is computed from the discovered clinics (strict gate) — check the
stream line.
**T6 — Migration**: first start adds `entity_scores.roster_key`; History and reports load.

### Regression Checks
- **R1** Hospital-type Deep Diagnostic unaffected (different analyzer).
- **R2** Composite table rows: seeded clinics keep distinct Google data; anchor row rating equals
  the header rating.
- **R3** Unit: `tests/test_roster_reputation.py` (4 new) pass; suite otherwise at baseline (same 4
  pre-existing failures).

### Acceptance Checklist
- [ ] T1 pillar consistent with table
- [ ] T2 math check
- [ ] T3 no hospital capture
- [ ] T4 cache guard
- [ ] T5 Compare/Event
- [ ] T6 migration
- [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING. Compare against the report that prompted this (Houston Methodist Orthopedics,
2026-09-14) — the new Pillar 2 should be materially lower and explainable from the table. Not
smoke-tested against Postgres locally (shared DATABASE_URL only); migration is the standard
add-column-if-missing pattern.

## DD-HELP-REWRITE — Deep Diagnostic help pop-over rewritten for the service-line workflow

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic · **Type:** copy

### What changed
- The "How to analyze a hospital service line →" pop-over now describes the current flow: type
  first; Health System (system only, auto-trim), Service Line, Market; flagship listing line with
  "change"; Locations list (Google-seeded + AI, Run disabled until loaded); prefilled title and
  profile "change"; Run produces report + teaser + per-location table; Advanced options.
- Adds a short "How the reviews pillar is scored" paragraph (roster-weighted, never the parent
  hospital's listing) and keeps the "started as a Specialty Practice" switch note.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1**: Deep Diagnostic → click "ℹ How to analyze a hospital service line →" → modal opens with
title "Analyze a Hospital Service Line", three sections (Set up the search / Confirm and run /
reviews pillar) and steps 1–8; ✕ and "Got it" close it; clicking the backdrop closes it.
**T2**: Follow the steps literally with HOUSTON METHODIST / ORTHOPEDICS / HOUSTON / TX — every UI
element named in the text exists and behaves as described.

### Regression Checks
- **R1** Event Prep help modal unchanged.

### Acceptance Checklist
- [ ] T1 modal content/close
- [ ] T2 steps match the UI
- [ ] R1

### Notes for the testing agent
NEEDS BROWSER TESTING (copy only).

## DD-HOSPITAL-CONTENT-FOLDED — Hospital Deep Diagnostic includes content analysis; standalone Content Analysis panel removed

**Shipped:** 2026-09-14 · **Area:** Deep Diagnostic (Hospital type), History · **Type:** feature + UX

### What changed
- **Hospital-type Deep Diagnostic runs now include the content analysis.** After the base run,
  `_finalize_hospital_combined` runs the verified content checks (website from the user's URL
  override → Google listing website → LLM guess; Wikidata; Wikipedia; reputation from the base
  run's Google data; Leapfrog/CMS safety), **drafts the prescription** for every draftable finding,
  and renders **Report 1** (Deep Diagnostic + Content Improvement Keys, becomes the run's PDF) and
  **Report 2** (detailed Content Report with the prescription). Recorded as a
  `content_analysis_runs` row bound to the run so History's Downloads menu shows
  "Deep Diagnostic (PDF)" + "Content Report (PDF)" exactly as before.
- Applies only to `individual_report` hospital runs with an entity name and PDF output. Market
  reports, FQHC, practice (already combined) and admin "Skip PDF" runs are unchanged. Fail-soft:
  a content error logs `⚠ Content analysis failed … base report kept`.
- **Removed from the Deep Diagnostic page**: the "🔎 Content Analysis" button, the contained
  Content Analysis panel (search / candidates / URLs / progress / results) and its "What is Content
  Analysis?" modal. The Run note now reads "Produces the report (with content analysis and a
  drafted prescription) and a teaser…".
- Kept: `/api/content-analysis/*` endpoints and the History re-render / draft actions for
  existing content runs (their JS remains; the old panel functions are simply unreachable).

### Files changed
`server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Hospital run end-to-end**: Deep Diagnostic → Hospital → e.g. INTERMOUNTAIN MEDICAL CENTER /
MURRAY / UT → Run. Stream shows the base phases, then "Analyzing content…", "Drafting the content
prescription", "Building the report with Content Improvement Keys", finding bullets. Completion
offers the report; History row's Downloads shows **Deep Diagnostic (PDF)** (has the Content
Improvement Keys section at the end) and **Content Report (PDF)** (contents page + findings with
drafted content), plus Teaser.
**T2 — Website override**: same run with Advanced → Website URL set to the hospital's real site →
the content report's website findings reference that URL.
**T3 — Page**: no Content Analysis button/panel anywhere on Deep Diagnostic; no console errors on
load or when switching types.
**T4 — History legacy**: an older content-analysis run still shows its files and the re-render /
draft actions still work.
**T5 — Fail-soft**: with an unreachable website (bogus URL override), the run still completes with
the base report and the ⚠ line in the stream.

### Regression Checks
- **R1** Practice / Service Line runs: unchanged (their own combined report).
- **R2** FQHC and Market (Patient Pulse) runs: no content phase.
- **R3** Admin "Skip PDF": no content phase.
- **R4** Unit: suite at baseline (same 4 pre-existing failures); `import server` succeeds.

### Acceptance Checklist
- [ ] T1 end-to-end + History files
- [ ] T2 URL override
- [ ] T3 page clean
- [ ] T4 legacy content runs
- [ ] T5 fail-soft
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. Hospital runs now take roughly 2–4 minutes longer.

## REBRAND-AI-REPUTATION — "AI Visibility" → "AI Reputation" across reports, UI, emails and pages (v1.09)

**Shipped:** 2026-09-14 · **Area:** all report PDFs, web app, Learn/Methodology pages, emails · **Type:** branding

### What changed
- **Names**: product tagline "AI Reputation Intelligence"; report name "AI Reputation Report";
  score labels "AI Reputation Score" and "Pulse Score (AI Reputation)"; score descriptor
  "AI Reputation". Central constants live in `perception/strings.py`.
- **PDF renderers** (Deep Diagnostic/market, practice combined, comparison, content deep-dive,
  Hospital Network standard/content/Full Detail, FQHC, Student Health, content report): all cover,
  footer, scorecard and section headings updated. Each render entry point now calls
  `rebrand_result(...)`, a **display-time** walker that rewrites any "AI Visibility" phrasing in the
  model-written text (verdict, overview, assessment, roadmap items, ai_says, findings) — the
  **LLM prompts are intentionally unchanged**. Ids, paths and URLs are skipped. Old runs re-rendered
  from History are rebranded too.
- **Web app**: sidebar/login tagline (all three brands), Hospital Network subtitle/tooltips, Trends
  page title/labels/chart, Compare Two label, bulk-list help/CSV column text. **Release-notes history
  left as-is**; new **Version 1.09** block added at the top ("Reports Are Now AI Reputation Reports").
  `_APP_VERSION` → 1.09.
- **Public Learn / Methodology pages**: HTML title, meta description, heading, lede, footer.
- **Emails** (public network request): subject, ready-notice heading/body, confirmation text.
- **Seed content** (`learn_seed.py`) updated. Live rows are admin-edited in Postgres, so a one-off
  script is provided: `scripts/rebrand_learn_articles.py` (idempotent `replace()` on
  title/body/category). **Must be run once against production after deploy.**
- Briefing copy + config, config/env comments, and `tests/test_rebrand.py` expectations updated.
- Not changed: code identifiers (`ai_visibility_score`, `ai_visibility_verdict`, DB columns, API
  JSON), prompts, previously generated PDFs on disk.

### Files changed
`perception/strings.py`, `perception/pdf.py`, `perception/network_pdf.py`, `perception/fqhc_pdf.py`,
`perception/student_health_pdf.py`, `perception/content_report_pdf.py`, `perception/briefing.py`,
`perception/briefing_config.json`, `perception/email_utils.py`, `perception/learn_seed.py`,
`perception/config.py`, `.env.example`, `server.py`, `web/index.html`, `tests/test_rebrand.py`,
`scripts/rebrand_learn_articles.py`, `docs/qa/test-battery.md`

### Test Cases
**T1 — App chrome**: sidebar and login show "AI Reputation Intelligence"; Release Notes opens with
Version 1.09 / September 14, 2026 and the naming entry; footer version reads 1.09; older release
entries still say "AI Visibility" (intentional).
**T2 — Deep Diagnostic PDF** (any hospital or practice): cover sub-line "AI Reputation Report", score
badge descriptor "AI Reputation", methodology box "Pulse AI Reputation", roadmap heading "AI
Reputation Improvement Roadmap". Search the PDF text for "Visibility" → no hits.
**T3 — Hospital Network PDFs** (standard, Teaser, Full Detail): cover "Hospital Network / AI
Reputation Report", "Network AI Reputation", "AI Reputation Score", "System-Level AI Reputation",
footer line. No "Visibility" in the PDF text.
**T4 — FQHC + Student Health PDFs**: scorecard/verdict/assessment headings and the ranked sub-line
read "AI Reputation".
**T5 — Model text**: in a fresh run's PDF, the Verdict/Assessment prose shows "AI Reputation" even
though the model still writes "AI Visibility" (display-time rewrite). Re-render an OLD run from
History → also rebranded.
**T6 — Compare Two + Trends**: labels "Pulse Score (AI Reputation)"; Trends page title "AI Reputation
Trends".
**T7 — Public pages**: /learn and /methodology titles, headings and footer say AI Reputation.
Then run `scripts/rebrand_learn_articles.py` once on production → article bodies updated; admin
Learn editor shows the new wording.
**T8 — Email**: trigger a public network request → confirmation and ready emails say "AI Reputation
Report".

### Regression Checks
- **R1** Scores, pillars, grades unchanged (pure naming).
- **R2** URLs/links in PDFs intact (walker skips path/url fields).
- **R3** Full unit suite: identical failure set to before (15 pre-existing), 326 pass.
- **R4** API JSON field names unchanged (`ai_visibility_score` etc.).

### Acceptance Checklist
- [ ] T1 chrome + release note
- [ ] T2 Deep Diagnostic PDF
- [ ] T3 Network PDFs
- [ ] T4 FQHC / Student Health PDFs
- [ ] T5 model text rewritten (new + old run)
- [ ] T6 Compare / Trends
- [ ] T7 public pages + live article script
- [ ] T8 emails
- [ ] R1–R4

### Notes for the testing agent
NEEDS BROWSER TESTING. After deploy, run once: `.venv/bin/python scripts/rebrand_learn_articles.py`
(with production DATABASE_URL) — until then the live Learn/Methodology article bodies still say
"AI Visibility" while the page chrome says "AI Reputation".

## HOME-PAGE — New Home landing page with admin-editable featured (video) block

**Shipped:** 2026-09-14 · **Area:** App shell, Learn content system · **Type:** feature

### What changed
- **Home page** (`#page-home`, nav "Home" at the top of the sidebar). Login now lands on Home
  instead of Hospital Network. Hero: RLDatix mark (hidden for partner brands, like page titles),
  brand wordmark, headline **AI Reputation Intelligence**, one-line lede, three CTAs (Hospital
  Network / Deep Diagnostic / How it works). Below: **featured block** and six "Start a report"
  cards (Hospital Network, Deep Diagnostic, Competitors Rankings, Compare Two, Event Prep, History).
- **Featured block** is content from a new `home` content page in the existing Learn system.
  `GET /api/learn?page=home` (public; pages limited to learn/methodology/home). Admins manage it
  under Learn → Manage Content → **Home page (in-app landing)**; "Load starter articles" seeds a
  "Welcome to Pulse" block (`HOME_ARTICLES`). Empty state shows a 16:9 placeholder "A short welcome
  video is coming soon" (+ an admin hint).
- **Video embeds in Markdown**: a line containing only a YouTube (`watch?v=`, `youtu.be`, `shorts`,
  `live`), Vimeo (incl. unlisted `/id/hash`) or Loom (`share`/`embed`) link renders as a responsive
  16:9 iframe (`youtube-nocookie`, `dnt=1` for Vimeo). Works on Home, in-app Learn and the public
  /learn and /methodology pages (CSS added to both).
- Release note "New Home Page" added under 1.09.

### Files changed
`web/index.html`, `server.py`, `perception/learn.py`, `perception/learn_seed.py`,
`docs/qa/test-battery.md`

### Test Cases
**T1 — Landing**: sign in → Home page shows: RLDatix logo (white on teal hero), "PULSE" kicker,
"AI Reputation Intelligence" headline, lede, three buttons; placeholder featured block; six cards.
Sidebar "Home" is highlighted first in the list. Cards and CTAs navigate to the right pages.
**T2 — Admin content**: as admin, Learn → Manage Content → select "Home page (in-app landing)" →
"Load starter articles" → a "Welcome to Pulse" article appears. Edit it: put a YouTube link on its
own line (e.g. `https://www.youtube.com/watch?v=dQw4w9WgXcQ`) → Save → Home shows the title and an
embedded, playable 16:9 player with the surrounding text. Repeat with a Vimeo and a Loom link.
**T3 — Non-video URL**: a line with `https://example.com/page` stays a plain paragraph/link.
**T4 — Unpublish**: unpublish the home article → Home shows the placeholder again.
**T5 — Partner brands**: log in under extension1 / extension2 → no RLDatix mark in the hero; the
brand's own wordmark appears in the kicker (Ashleigh Jane: reverse image).
**T6 — Public pages**: paste a video link into a /learn article → /learn renders the embed with
correct sizing.
**T7 — API**: `GET /api/learn?page=home` returns the published home content; `?page=bogus` → 400.

### Regression Checks
- **R1** All other pages/nav unchanged; `navigate('network')` deep links (e.g. bulk "New Bulk Run")
  still work.
- **R2** In-app Learn (page=learn) content unchanged; Methodology admin management unchanged.
- **R3** Unit suite: 326 pass / same 15 pre-existing failures; learn/markdown tests pass.

### Acceptance Checklist
- [ ] T1 landing + navigation
- [ ] T2 admin content + embeds (YouTube / Vimeo / Loom)
- [ ] T3 plain URL
- [ ] T4 unpublish
- [ ] T5 partner brands
- [ ] T6 public page embed
- [ ] T7 API
- [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING. Check the hero on a narrow window (≤760px) — headline scales down.

## HOME-NAV-RAIL — Sidebar collapses to an icon rail on the Home page

**Shipped:** 2026-09-14 · **Area:** App shell / Home · **Type:** UX

### What changed
- On the Home page (desktop, viewport > 860px) `#app` gets class `rail`: the sidebar becomes a 64px
  icon strip (icons only, "P" monogram in place of the wordmark, no section labels / user block).
- **Hover** expands it to the full 224px sidebar as an overlay (negative margin), with a shadow —
  the page content does not reflow. Leaving the rail collapses it again.
- Navigating to any other page removes `rail` → normal full sidebar everywhere else. Returning to
  Home re-collapses it. Tablet/mobile breakpoints are unchanged.
- Release note text for the Home page mentions the rail.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Rail on Home**: sign in → Home shows the narrow icon rail; hero spans the freed width.
**T2 — Hover expand**: move the pointer over the rail → it widens over the content with labels and
section headers; content behind does not shift; move away → collapses.
**T3 — Navigate**: click any rail icon or a Home card → destination page shows the full sidebar;
click Home → rail again.
**T4 — Active state**: "Home" icon is highlighted in the rail; on other pages the correct item is
highlighted in the full sidebar.
**T5 — Widths**: at ≤860px the existing tablet rail applies on every page (unchanged); at ≤540px
the top bar applies (unchanged).
**T6 — Partner brands**: extension1/extension2 → rail shows the "P" monogram only when collapsed,
brand wordmark when expanded.

### Regression Checks
- **R1** Progress/overlay screens unaffected (rail only toggles with `navigate('home')`).
- **R2** No horizontal scrollbar appears on Home when the rail is expanded.

### Acceptance Checklist
- [ ] T1 rail on Home
- [ ] T2 hover overlay, no reflow
- [ ] T3 navigate restores full sidebar
- [ ] T4 active states
- [ ] T5 breakpoints
- [ ] T6 partner brands
- [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. CSS/JS only.

## REBRAND-FOLLOWUP — Uppercase / split "AI VISIBILITY" headers caught in a case-insensitive sweep

**Shipped:** 2026-09-14 · **Area:** Content Report PDF, Student Health PDF, public Learn page, Network stream · **Type:** branding fix

### What changed
- Content Report header badge (top-right of the embedded/standalone content report, also inside the
  practice combined PDF and Hospital Network Full Detail): `AI VISIBILITY / REPORT` → **AI REPUTATION
  / REPORT**.
- Student Health ranking PDF header badge: `AI VISIBILITY RANKING` → **AI REPUTATION RANKING**.
- Public /learn and /methodology header sub-line: `AI VISIBILITY INTELLIGENCE` → **AI REPUTATION
  INTELLIGENCE**.
- Hospital Network progress stream line: "Analyzing AI reputation for <network>".
- Remaining "AI Visibility" text in the repo is limited to: release-notes history (intentional),
  code comments/docstrings, and LLM prompt / extraction-tool descriptions (intentionally unchanged).

### Files changed
`perception/content_report_pdf.py`, `perception/student_health_pdf.py`, `server.py`,
`perception/network_analyzer.py`, `docs/qa/test-battery.md`

### Test Cases
**T1**: run a Specialty Practice / Service Line Deep Diagnostic → in the combined PDF, the Content
Analysis header (dark band with the RLDatix logo) reads "AI REPUTATION REPORT" top-right.
**T2**: Hospital Network Full Detail PDF → same header reads "AI REPUTATION REPORT".
**T3**: Student Health Clinics ranking PDF → header badge "AI REPUTATION RANKING".
**T4**: open /learn (signed out) → sub-line under the wordmark reads "AI REPUTATION INTELLIGENCE".
**T5**: start a Hospital Network run → stream shows "Analyzing AI reputation for …".
**T6**: text-search each PDF above for "Visibility" → no hits.

### Regression Checks
- **R1** Suite unchanged (326 pass / same 15 pre-existing failures).

### Acceptance Checklist
- [ ] T1–T6
- [ ] R1

### Notes for the testing agent
NEEDS BROWSER TESTING. Note: PDFs generated by production before the v1.09 deploy keep the old
wording — regenerate to verify.

## VIDEO-END-RESET — Embedded videos return to their poster frame when they finish

**Shipped:** 2026-09-14 · **Area:** Home page, in-app Learn, public /learn & /methodology · **Type:** UX

### What changed
- Vimeo embeds now use `title=0&byline=0&portrait=0` (no uploader chrome) in addition to `dnt=1`;
  YouTube embeds add `enablejsapi=1`. Each embed iframe carries `data-host`.
- A small handler (`_bindVideoEmbeds`) uses the hosts' postMessage player APIs (no SDKs): on Vimeo
  `ended` it sends `unload` (player returns to the poster + play button, so Vimeo's "more videos"
  end screen never shows); on YouTube `playerState === 0` it sends `stopVideo` (thumbnail + play).
  Loom has no end-screen API; unchanged.
- Bound after Home and in-app Learn render, and on the public pages via an inline script.

### Files changed
`perception/learn.py`, `web/index.html`, `server.py`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Vimeo**: put a short Vimeo link in the Home content → play to the end → the player snaps
back to the poster frame with the play button; no "more from this user" grid; clicking play
restarts from 0:00. No uploader name/avatar overlay while playing.
**T2 — YouTube**: same with a YouTube link → at the end the player shows the thumbnail + play
(no suggested-video wall); play restarts.
**T3 — Public page**: same Vimeo link in a /learn article → same end behaviour signed out.
**T4 — Multiple embeds**: two videos on one page → each resets independently.

### Regression Checks
- **R1** Pages without embeds: no console errors (handler is a no-op).
- **R2** Suite unchanged (326 pass / same 15 pre-existing failures).

### Acceptance Checklist
- [ ] T1 Vimeo reset
- [ ] T2 YouTube reset
- [ ] T3 public page
- [ ] T4 multiple embeds
- [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Use a video shorter than a minute for the end-of-video checks.

## VIDEO-END-RESET-2 — Hard reset of the player frame on video end

**Shipped:** 2026-09-14 · **Area:** Home / Learn / public pages · **Type:** fix

### What changed
- Vimeo embed URL adds `api=1` so the player reliably emits `ended` over postMessage.
- On `ended` (Vimeo) / `playerState 0` (YouTube) the page now also **reloads the iframe** (blank →
  original src) — guaranteed poster frame + play button on any host, even if the host ignores the
  `unload` / `stopVideo` command. Applied in the app and on the public pages.

### Test Cases
**T1**: Vimeo video plays to the end → player returns to the poster (brief reload flicker is
acceptable); no end-screen grid; play restarts from 0:00. **T2**: same for YouTube.
**T3**: local dev — restart the server after pulling (Python change) and hard-refresh.

### Acceptance Checklist
- [ ] T1 Vimeo · [ ] T2 YouTube · [ ] T3 restart note

### Notes for the testing agent
NEEDS BROWSER TESTING. Production must be on ≥ this commit; earlier builds show the host end screen.

## VIDEO-END-RESET-3 — Vimeo end event is "finish" under the messaging API

**Shipped:** 2026-09-14 · **Area:** Home / Learn / public pages · **Type:** fix (verified headless)

### What changed
- With `api=1`, the Vimeo player uses its legacy postMessage protocol and reports the end of
  playback as `{"event":"finish"}` (not `ended`). The handler now subscribes to and acts on BOTH
  `ended` and `finish`. Verified with a headless Chromium probe against the real welcome video:
  `finish` → `unload` → iframe reload → fresh `ready` (poster state). No "More from …" screen.

### Test Cases
**T1**: Home page (local: hard-refresh; public pages need a server restart) → play the welcome
video to the end → returns to poster + play button; click play → restarts at 0:00.

### Acceptance Checklist
- [ ] T1

### Notes for the testing agent
NEEDS BROWSER TESTING (headless-verified for the event flow; confirm visually).

## PLATFORM-NAME — Tagline is now "AI Reputation Analysis Platform"

**Shipped:** 2026-09-14 · **Area:** app chrome, Home, public pages, PDF product subtitle · **Type:** branding

### What changed
- "AI Reputation Intelligence" → **"AI Reputation Analysis Platform"** everywhere it was the product
  descriptor: sidebar + login sub-line (all three brands), Home hero headline, browser tab title
  ("Pulse · AI Reputation Analysis Platform"), public /learn & /methodology titles / header sub-line
  / footer, Learn seed intro, PDF `PRODUCT_SUBTITLE` (cover product line), the 1.09 release-note text.
- "Pulse" stays the short product name; "AI Reputation Report" / "AI Reputation Score" unchanged.
- Sidebar sub-line wraps to two lines cleanly (line-height + slightly tighter tracking); Home
  headline reduced from 48px to 44px so the longer phrase fits on one line at desktop width.
- `scripts/rebrand_learn_articles.py` also maps the old taglines to the new one for live rows.

### Test Cases
**T1**: sidebar under PULSE reads "AI REPUTATION ANALYSIS PLATFORM" on two neat lines; login card
sub-line the same; tab title "Pulse · AI Reputation Analysis Platform".
**T2**: Home hero headline "AI REPUTATION ANALYSIS PLATFORM" on one line at ≥1100px wide; wraps
gracefully narrower.
**T3**: /learn and /methodology: page title, header sub-line and footer show the new phrase.
**T4**: any new PDF cover: product line reads "AI Reputation Analysis Platform"; report line still
"AI Reputation Report".
**T5**: after deploy run `scripts/rebrand_learn_articles.py` again → live Learn intro says
"Pulse is the AI Reputation Analysis Platform for healthcare".

### Acceptance Checklist
- [ ] T1–T5

### Notes for the testing agent
NEEDS BROWSER TESTING.

## HISTORY-FAST — History page: no per-row storage stats, instant cached paint, collapsed batch control, chunked rows

**Shipped:** 2026-09-15 · **Area:** History (server + client) · **Type:** performance + UX

### What changed
1. **Server** (`GET /api/history`): the per-row `Path.exists()` checks (one stat per PDF and per
   briefing — ~1,000 Cloud Storage FUSE round trips per load) are replaced by `_existing_files`:
   ONE `os.listdir` per distinct parent folder, cached 30 s in-process. Rows created in the last
   10 minutes fall back to a direct check so a just-finished run shows its download immediately.
   Network rows unchanged (path presence only).
2. **Instant paint**: the last `/api/history` + content-run payload is kept in `sessionStorage`
   and rendered immediately on the next visit, then refreshed from the server (stale-while-
   revalidate).
3. **Batch control**: Event Runs, National Entity Runs and Student Health Runs now live inside ONE
   collapsed `<details>` ("Batch runs — Event Prep · Bulk lists · Student Health", Show/Hide).
   Their three endpoints are fetched only when it is expanded; open state persists for the session;
   counts and a "Running" badge appear in the summary once loaded. The 8-second poll runs only while
   the control is open and something is running.
4. **Chunked rows**: the All Reports table renders the first 60 rows synchronously and streams the
   rest in chunks of 120 on subsequent ticks (guarded so a re-render/sort/search supersedes a
   stale stream).

### Files changed
`server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Speed (prod)**: open History → table visible in well under a second (previously many
seconds). Network tab: initial load makes 2 calls (`/api/history`, `/api/content-analysis/runs`).
**T2 — Cached paint**: navigate away and back → table appears instantly (no spinner), then
refreshes. New browser tab (new session) → normal load with spinner.
**T3 — Batch control**: collapsed by default; expand → "Loading batch runs…" then the three
sections with counts in the summary; Event/Bulk/Student inner "View All"/"Hide" toggles still
work; collapse → stays collapsed after re-render; reload the tab → remembered open/closed state.
**T4 — Running badge + poll**: start an Event Prep run → expand Batch runs → summary shows
"Running"; the row updates every ~8 s; collapse → polling stops (Network tab).
**T5 — Fresh download**: run any report → on completion open History → its Report (PDF) link
is present immediately (fresh-file fallback), and after 30 s (cache expiry) too.
**T6 — Chunked rows**: with >60 rows, the first 60 show at once and the rest fill in without a
visible pause; sorting and searching re-render correctly (no duplicated rows); scroll to the
bottom shows the total count of rows.
**T7 — Downloads**: Deep Diagnostic (PDF) / Content Report / Teaser / Briefing links on rows
behave as before; a row whose PDF file was deleted from storage shows no Report link.

### Regression Checks
- **R1** Empty database (fresh account with no runs) → "No reports yet" state still renders after
  the batch control has loaded.
- **R2** Admin Event/Bulk row actions (Edit/Replace/Delete/Resume) unchanged inside the control.
- **R3** Unit suite at baseline (same 15 pre-existing failures).

### Acceptance Checklist
- [ ] T1 speed + 2 initial calls
- [ ] T2 cached paint
- [ ] T3 batch control
- [ ] T4 running badge / poll
- [ ] T5 fresh download link
- [ ] T6 chunked rows + sort/search
- [ ] T7 downloads
- [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING. The big win (item 1) only shows on production where REPORTS_DIR is a GCS
FUSE mount; locally the difference is small.

## HISTORY-WINDOW — 45-day default window, "Show older runs", search across all history

**Shipped:** 2026-09-15 · **Area:** History (server + client) · **Type:** UX

### What changed
- `GET /api/history` now returns `{runs, has_more, since, until, total}` and accepts `days`
  (default 45), `before=<iso>` + `days` (next older slice), `q` (search across ALL history, no
  window) and `all=1`. The file-existence pass runs only on the returned slice.
- History loads the **last 45 days** by default. Footer: "Showing runs since <date> · N runs.
  Older runs are kept for Trends and can be loaded here." with **Show older runs (90 days)**
  (appends the next slice, repeatable) and **Show all**. Nothing is deleted by the window.
- **Search** filters loaded rows instantly, then (300 ms debounce) asks the server for matches
  across all history; clearing the box returns to the window. Footer: "N matches across all
  history".
- Session cache stores the window payload with has_more/since.

### Files changed
`server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Window**: History shows only the last 45 days; footer states the since-date and count;
Network tab shows `/api/history?days=45`.
**T2 — Older**: "Show older runs (90 days)" appends older rows (no duplicates) and moves the
since-date back; repeat until the buttons disappear. "Show all" → every run.
**T3 — Search across history**: type part of an entity name older than 45 days → it appears
within ~0.5 s with "N matches across all history"; clear → back to the window.
**T4 — Sort**: column sorts work on the window, on appended slices, and on search results.
**T5 — Cached paint**: navigate away/back → instant paint of the window; older-slice state resets.

### Regression Checks
- **R1** Trends unaffected (reads the database directly, not the window).
- **R2** Batch control, chunked rows, download links from HISTORY-FAST unchanged.
- **R3** Local endpoint sanity: 45-day window 209 of 543 runs (has_more true); older 90-day slice
  334 (has_more false); search "houston" 14; all == total.

### Acceptance Checklist
- [ ] T1 window · [ ] T2 older/all · [ ] T3 search · [ ] T4 sort · [ ] T5 cache · [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING.

## HISTORY-ADMIN-DELETE — Admin can delete a single run (data + report files)

**Shipped:** 2026-09-15 · **Area:** History · **Type:** admin capability (explicitly approved; permanent)

### What changed
- Admin-only **🗑 Delete run** action in a History row's Downloads menu → confirm dialog →
  `DELETE /api/reports/{run_id}` (`require_admin`; 403 otherwise; 404 if unknown).
- Server cascade (`delete_analysis_run` / `delete_network_run` in `perception/db.py`):
  ranked_providers, content-analysis runs bound to the run (+ their report files) and
  content_findings, practice reputation runs/practices/physicians/log, FQHC intake/audit/battery,
  the run's own canonical `entity_scores` row; loose pointers are nulled (event_entities,
  gbp_identity, public_report_requests). Files unlinked: PDF, teaser, briefing, markdown, content
  report 1/2 (network: standard/teaser/full-detail). The storage listing cache is cleared so
  History reflects it immediately.
- Client removes the row from the loaded set, search results and the session cache, then re-renders.
- This is a cleanup tool for junk/test runs. The 45-day window (HISTORY-WINDOW) is the retention
  answer; nothing is deleted automatically.

### Files changed
`server.py`, `perception/db.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Admin delete (Deep Diagnostic)**: create a throwaway run → History → Downloads → "🗑 Delete
run" → confirm → row disappears at once; reload → still gone; the old PDF URL returns 404; Trends
for that entity no longer includes the run.
**T2 — Hospital Network run**: same flow on a network row → standard / Teaser / Full Detail files
removed; row gone.
**T3 — Cancel**: click Delete then Cancel → nothing changes.
**T4 — Non-admin**: no Delete item in the menu; a direct `DELETE /api/reports/<id>` returns 403.
**T5 — Unknown id**: `DELETE /api/reports/does-not-exist` as admin → 404.
**T6 — Dependent data**: for a practice run with a composite table and content analysis, after
delete the content report links are gone and no orphaned content-analysis row remains in
`/api/content-analysis/runs`.

### Regression Checks
- **R1** Deleting one run leaves other runs of the same entity intact (Trends still shows them).
- **R2** Event Prep / bulk / student-health deletes unchanged (separate endpoints).
- **R3** Unit suite at baseline; helpers return None for unknown ids.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING. Deletion is permanent — use throwaway runs only.

## TRENDS-LIST — Trends list redesigned: full width, latest score + delta, sparklines, badges

**Shipped:** 2026-09-15 · **Area:** Trends · **Type:** UX

### What changed
- Page uses full width (`#page-track` max-width none; list container 1180px) and the standard
  History-style card + `.tbl` table.
- Columns: **Entity** (name link; location · specialty · notes as a muted second line) ·
  **Latest score** (last Pulse Score, ▲/▼ delta vs previous run, "no change", or "no runs yet") ·
  **Trend** (inline SVG sparkline of the last ≤12 scores on a fixed 0–100 scale; hover shows
  date: score list) · **Schedule** badge · **Last run** / **Next run** as short non-wrapping dates
  ("Sep 11"; hover shows full date + relative) · **Runs** right-aligned · **Status** badge (Active
  teal / Paused gray) · single **Pause/Resume** action (View removed — the name opens the trend).
- Rows sorted active first by soonest next run, paused last (dimmed). Count line: "N tracked
  entities · M paused".
- Data: `list_tracked_entities()` adds `recent_scores`, `latest_score`, `score_delta` via ONE query
  across all tracked names (no per-entity round trips).

### Files changed
`perception/db.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Layout**: Trends fills the content width; no wrapped dates/locations; card styling matches
History; headers left-aligned (Runs right).
**T2 — Scores**: an entity with ≥2 runs shows the latest score with a green ▲ or red ▼ delta and
a sparkline; hover on the sparkline lists dates and scores; an entity with 1 run shows the score
and no delta; a new entity shows "no runs yet" and "—".
**T3 — Sorting**: active entities on top ordered by next run date; paused entities last and dimmed;
count line shows "· N paused" when any are paused.
**T4 — Actions**: clicking the entity name opens the trend view; Pause/Resume toggles and the row
moves/dims accordingly; "+ Track New Entity" flow unchanged.
**T5 — Dates**: last/next run show "Sep 11" style (year appended when not the current year);
tooltip shows the full date and "in N days"/"N days ago".

### Regression Checks
- **R1** Entity trend detail view unchanged.
- **R2** `/api/track/entities` still returns all previous fields (+ the three new ones).
- **R3** Suite at baseline.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING. Production has 8 tracked entities — good coverage for T2/T3.

## TRENDS-CONFIG — Per-entity configuration panel (safe edits only), run now, track-as-new, snapshot settings

**Shipped:** 2026-09-15 · **Area:** Trends · **Type:** feature

### What changed
- **⚙ Details** on each Trends row expands an inline configuration panel:
  - Locked identity (🔒): Entity, Market (city/state), Specialty, Scope (all locations / single),
    plus "Tracking since" (date · creator). A note explains why they're locked and offers
    **track a new entity** (prefills the add flow from this one; notes say "Supersedes …").
  - Editable: **Cadence** (weekly/monthly/manual), **Next run** (date), **Notes** → **Save**.
  - **Run now** starts an immediate snapshot run from the list.
- Server: `PUT /api/track/entities/{id}` no longer accepts `aggregate` (identity locked);
  accepts `next_run_at` (ISO date). Entity name/city/state/specialty were never editable.
- Trend detail: new **Settings** column per snapshot ("All locations · Orthopedics · Houston, TX ·
  practice_procedural") with a ⚠ when a snapshot's settings differ from the entity's current
  configuration. `get_entity_trend` returns `run_aggregate`, `run_specialty`, `run_location`,
  `run_profile`.

### Files changed
`perception/db.py`, `server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Panel**: Trends → ⚙ Details on a row → panel shows the four locked fields with 🔒 and
tooltip, Tracking since, and the editable Cadence / Next run / Notes with Save and Run now.
**T2 — Safe edits**: change cadence to Weekly, set Next run to tomorrow, edit Notes → Save →
"Saved."; row updates (Schedule badge, Next run, second line); reload → persisted; the trend line
is unchanged.
**T3 — Locked**: no controls to change name/market/specialty/scope; `PUT` with `{"aggregate":
false}` is ignored (field not in the model); `next_run_at: "not-a-date"` → 400.
**T4 — Track as new**: click "track a new entity" → add flow opens prefilled (name, city, state,
specialty, cadence, scope, "Supersedes …" note); complete it → a second entity appears; the
original keeps its history.
**T5 — Run now**: click → message "Run started…"; a new snapshot appears in the trend detail when
the run finishes; Last run / Runs update.
**T6 — Snapshot settings**: open an entity's trend → Settings column filled per row; for an entity
whose scope/specialty changed historically (or after T4 on the old entity), the differing rows
show ⚠ with tooltip.

### Regression Checks
- **R1** Pause/Resume and the sparkline list (TRENDS-LIST) unchanged.
- **R2** Suite at baseline.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING.

## TRENDS-VIEW-BUTTON — View button restored on Trends rows

**Shipped:** 2026-09-15 · **Area:** Trends · **Type:** fix

### What changed
- Each row's actions are now **View · ⚙ Details · Pause/Resume**. View opens the full trend (the
  entity name link still does too). Footer note updated.

### Test Cases
**T1**: Trends → View on a row opens the trend detail; Details and Pause/Resume unchanged.

### Acceptance Checklist
- [ ] T1

### Notes for the testing agent
NEEDS BROWSER TESTING.

## TRENDS-REPORT-PDF — AI Reputation Trend Report (downloadable PDF per tracked entity)

**Shipped:** 2026-09-15 · **Area:** Trends · **Type:** feature (part 1 of 2; email delivery follows)

### What changed
- New `perception/trend_pdf.py`: builds a customer-ready **AI Reputation Trend Report** from the
  tracked entity + its snapshot history. Sections: cover band (RLDatix mark, entity, market ·
  specialty) · meta strip (snapshots, first/latest snapshot dates, scope, prepared date) ·
  **Executive summary** (latest score + quartile, change since previous / since first, most
  improved pillar, largest decline, and a 3–4 sentence **analyst paragraph written by the model
  from the computed numbers only** — fail-soft, omitted on any error/refusal) · **Pulse Score over
  time** (line chart with shaded quartile bands) · **Pillar trends** (four small charts +
  first/latest/change table; practice-rubric labels when the latest run used a practice profile) ·
  **Google reputation over time** (rating + review count) · **Snapshots** table (date, score, Δ,
  pillars, Google, run settings with ⚠ drift flag) · **Notable changes** (3 largest moves + any
  drift) · methodology/disclaimer box. Charts are inline SVG (no CDN); rendered via Playwright.
- `GET /api/track/entities/{id}/report.pdf` (auth; token query supported): renders on demand into
  `REPORTS_DIR/trends/trend_<id>_<latest_run_id>.pdf` and serves it; cached until a new snapshot
  exists. 404 when the entity has no snapshots.
- Trend detail header: **⬇ Trend Report (PDF)** button next to Run Now.

### Files changed
`perception/trend_pdf.py` (new), `server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Download**: Trends → View an entity with ≥2 snapshots → "Trend Report (PDF)" → a PDF named
`<Entity>_AI_Reputation_Trend_Report.pdf` downloads within a few seconds; status text clears.
**T2 — Content**: cover shows the entity/market; meta strip counts match the detail view; latest
score/quartile and the deltas match the list row and snapshot table; score chart shows all
snapshots with quartile bands; four pillar charts + table; Google chart; snapshot table rows =
detail table rows (same values); notable changes lists the 3 largest moves; drift rows carry ⚠.
**T3 — Analyst paragraph**: present, 3–4 sentences, references only numbers that appear in the
summary/pillar table (no invented events). If the model call fails the report still renders
without the paragraph.
**T4 — Cache**: download twice → second is instant (same file); run the entity (Run Now) and
download again → a new file with the new snapshot.
**T5 — Edge**: entity with 1 snapshot → report renders (charts show "Not enough snapshots",
deltas "—"); entity with 0 → 404 with a clear message.
**T6 — Practice entity**: a tracked specialty practice → pillar labels are the practice rubric's.

### Regression Checks
- **R1** Other PDFs unchanged. **R2** Suite at baseline.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Rendered locally for "Usa Health University Hospital" (33 snapshots):
layout verified from a screenshot; PDF 230 KB in ~1 s (+ a few seconds for the paragraph).

## TRENDS-REPORT-EMAIL — Email the Trend Report after each run (opt-in) and "Send report…" now

**Shipped:** 2026-09-15 · **Area:** Trends, email · **Type:** feature (part 2 of 2)

### What changed
- Tracked entity gains **email_report** (bool) and **report_emails** (JSON list) — auto-migrated
  columns; editable via `PUT /api/track/entities/{id}` (addresses validated, de-duplicated,
  lower-cased; 400 if none valid when a non-empty list is sent).
- **Configuration panel** (⚙ Details): "Email the Trend Report after each run" checkbox +
  Recipients (comma-separated). Checking it prefills the creator's email when the field is empty;
  Save refuses to enable delivery with no recipients. Recipients are visible to any user who opens
  the panel (an admin can see who receives what).
- **After-run hook**: Run now and the Cloud Scheduler run both go through
  `_run_tracked_and_notify` — when the snapshot job finishes with status "done" and delivery is on,
  the refreshed Trend Report is rendered (or reused) and emailed to each recipient with the PDF
  **attached** (Resend attachments), a latest-score line (+ delta), and an "Open Trends in Pulse"
  button. Failures are logged (`[trend-email] …`) and never affect the run.
- **Send now**: trend detail header "✉ Send report…" prompts for addresses (prefilled with the
  entity's recipients or the creator) → `POST /api/track/entities/{id}/report/send` → status
  "Sent to …". 400 with no valid address; 502 if the email service rejects.
- `email_utils._send` accepts attachments; new `send_trend_report`.

### Files changed
`perception/db.py`, `perception/email_utils.py`, `server.py`, `web/index.html`,
`docs/qa/test-battery.md`

### Test Cases
**T1 — Settings**: Trends → ⚙ Details → check "Email the Trend Report after each run" → creator's
email prefills → add a second address → Save → "Saved."; reopen → both persisted; enabling with no
recipients is blocked with a message.
**T2 — Run now → email**: with delivery on, click Run now (panel or detail) → when the run
finishes, each recipient gets "Pulse — Trend Report — <Entity>" with the PDF attached, the latest
score line, and the Open Trends button; the attachment opens and matches the download.
**T3 — Scheduled**: an entity due for its schedule (or trigger `/api/track/scheduled` with the
secret) → same email after the run.
**T4 — Send now**: detail → "✉ Send report…" → default addresses shown → OK → "Sent to …"; the
email arrives with the current report. Cancel → nothing sent. Empty/invalid → message, no send.
**T5 — Delivery off**: uncheck → Run now → no email.
**T6 — Validation**: `PUT` with `report_emails: ["bad"]` → 400; `["A@b.co","a@b.co"]` → stored
once, lower-cased.
**T7 — Failure path**: temporarily break RESEND_API_KEY → Run now still completes; server log
shows `[trend-email] FAILED …`; Send now returns 502 with a clear message.

### Regression Checks
- **R1** Run now / scheduled runs still create snapshots and advance next_run_at.
- **R2** Public network-request emails unchanged (no attachments).
- **R3** Suite at baseline; dry run with the sender stubbed: 2 valid of 3 addresses sent, PDF
  attached (~320 KB base64).

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] T7 · [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING + a real inbox. Resend must have RESEND_API_KEY configured in the
environment; attachments count toward Resend's 40 MB message limit (reports are ~0.3 MB).

## TRENDS-REPORT-PDF-2 — Trend Report page flow fixed

**Shipped:** 2026-09-15 · **Area:** Trends PDF · **Type:** layout fix

### What changed
- Pillar trends now start page 2 as a whole block (previously the four charts were sliced across
  the page 1/2 break); every section keeps its heading with its content (`break-inside: avoid`);
  the snapshot table flows naturally behind the Google chart with its header repeated on each
  page (was forced to its own page, leaving half of page 2 empty); the methodology box stays whole;
  snapshot rows are slightly tighter. Google chart: review-count line no longer hugs the top edge
  and the "N reviews" label no longer clips.
- Cache filename bumped (`_v2`) so previously rendered reports are rebuilt.

### Test Cases
**T1**: download the report for an entity with ~30 snapshots → 3 pages: p1 summary + score chart;
p2 pillar charts + table + Google chart + start of snapshots; p3 rest of snapshots + notable
changes + methodology, with no chart or box split across pages and no near-empty page.

### Acceptance Checklist
- [ ] T1

### Notes for the testing agent
NEEDS BROWSER TESTING. Verified locally on the 33-snapshot entity.

## TRENDS-RUBRIC — Tracked entities have a type; practices/service lines run on the practice rubric; rubric shown per snapshot; admin delete tracking

**Shipped:** 2026-09-15 · **Area:** Trends (server, DB, UI, PDF) · **Type:** correctness + admin

### What changed
- **Entity type** on tracked entities (`entity_type`: hospital | practice | service_line, plus
  `service_line`/`parent_system`), auto-migrated with **default 'hospital'** so every existing
  row keeps the rubric it was actually scored on. Identity stays locked (not editable).
- **Add flow**: Analysis Type now Hospital · Hospital Service Line · Specialty Practice (Health
  System + Service Line fields for the service line, name dedupe as in Deep Diagnostic; aggregate
  toggle hidden for practice types — always rolled up). Submit sends the type.
- **Run routing** (`_launch_tracked_run`): hospital → hospital analyzer (unchanged); practice and
  service line → practice analyzer (practice rubric, roster-based reviews pillar, auto profile),
  data-only snapshot. Used by create, Run now, and the scheduler.
- **Rubric per snapshot**: `get_entity_trend` returns `rubric` from each run's weighting profile;
  the list carries `latest_rubric` + `rubrics`. UI: "Hospital rubric"/"Practice rubric" badge on
  each row with ⚠ when it differs from the entity's type and "mixed" when the history has both;
  detail meta shows type + rubric; pillar labels (summary card, chart legend, table headers)
  follow the history's rubric, or neutral "Pillar 1–4" labels with per-row H/P badges when mixed.
  PDF: H/P badges in the snapshot table and a "Rubric change" note when mixed.
- **Nothing re-scored or relabelled**: old points keep hospital labels; new practice series start
  via "track a new entity" (type carried over).
- **Admin: Delete tracking…** in the configuration panel → `DELETE /api/track/entities/{id}`
  (now admin-only, hard delete; previously any user and only paused). Two confirms: remove the
  tracking instance; then optionally `?purge_runs=1` to also delete its snapshot runs (individual
  runs matched by entity name) and files. Non-admins can still Pause.

### Files changed
`perception/db.py`, `server.py`, `perception/trend_pdf.py`, `web/index.html`,
`docs/qa/test-battery.md`

### Test Cases
**T1 — Existing entities after deploy**: every current row shows Type "Hospital" and a "Hospital
rubric" badge (no ⚠); trend lines and detail unchanged; scheduled runs continue on the hospital
analyzer.
**T2 — New practice**: Track New Entity → Specialty Practice → e.g. Orthosouth, Memphis TN,
ORTHOPEDICS → first snapshot completes; row shows Type "Specialty Practice" and "Practice rubric";
detail pillar labels are the practice rubric's; the PDF labels match.
**T3 — Service line**: Track New Entity → Hospital Service Line → HOUSTON METHODIST / HOUSTON / TX /
ORTHOPEDICS → snapshot runs on the practice analyzer; Type shows "Hospital Service Line —
ORTHOPEDICS of HOUSTON METHODIST".
**T4 — Track as new from an old practice**: ⚙ Details on Columbia Orthopaedic Group → "track a new
entity" → add flow prefilled with type Hospital (its current type); switch to Specialty Practice →
complete; the old entity keeps its history; pause it later.
**T5 — Mixed history display**: (only reachable via a manual DB edit or an old entity whose type
was changed) → neutral pillar labels, H/P badges per row, "mixed" tag in the list, "Rubric change"
note in the PDF.
**T6 — Admin delete**: ⚙ Details → "Delete tracking…" → first confirm → Cancel on purge → entity
gone from Trends, its runs still in History. Repeat on another with OK on purge → runs gone from
History too. Non-admin: no Delete button; `DELETE` → 403.
**T7 — Email + PDF still work** for a practice entity (report labels practice pillars).

### Regression Checks
- **R1** Hospital entities: Run now / scheduled / email unchanged.
- **R2** Suite at baseline; local: existing entity → type hospital, latest_rubric hospital;
  simulated mixed history renders the note and badges.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] T7 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Production has 5 practice entities currently scored on the hospital rubric —
after deploy they will (correctly) show "Hospital rubric" with no ⚠ because their type defaults to
Hospital; re-track them as Specialty Practice when ready.

## TRENDS-SENT-REPORTS — Sent Trend Reports kept as artifacts; download cache cleaned; delete handling

**Shipped:** 2026-09-15 · **Area:** Trends · **Type:** data retention / UX

### What changed
- New `trend_reports` table: every emailed Trend Report (scheduled, after Run now, or Send now)
  is copied to `REPORTS_DIR/trends/sent/<entity>_<timestamp>.pdf` and recorded with entity,
  snapshot run_id, sender ("scheduled run" or the user), recipients that were delivered, kind,
  snapshot count, latest score, sent time. Kept permanently.
- Trend detail: **Sent reports** card (hidden when none) — Sent · To · By · Snapshot · Score ·
  ⬇ PDF (`GET /api/track/reports/{id}/pdf`, exact file that went out). Refreshes after Send now.
  `GET /api/track/entities/{id}/reports` lists them.
- On-demand download cache: rendering a new version deletes the entity's older cache files, so
  the trends folder holds one cached PDF per entity (plus the sent archive).
- Delete tracking: always removes the entity's cache files; with purge it also deletes the sent
  reports (rows + files). Without purge, sent reports are retained.
- History is unchanged (trend reports live with the entity on Trends).

### Files changed
`perception/db.py`, `server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Send now → archive**: detail → "✉ Send report…" → after "Sent to …", a Sent reports card
appears with today's row (To = the addresses, By = your name, snapshot count + score); ⬇ PDF
downloads a file named `<Entity>_AI_Reputation_Trend_Report_<date>.pdf` identical to what was
emailed.
**T2 — Scheduled send**: with delivery on, after a scheduled run the row shows By = "Scheduled
run".
**T3 — Exact file retained**: Run now (new snapshot) → download the current report (differs) →
the earlier Sent row's PDF is still the old version.
**T4 — Cache cleanup**: after T3, the trends folder contains one `trend_<id>_*.pdf` for the entity
(older cache removed) and the sent copies under `trends/sent/`.
**T5 — Delete without purge**: admin deletes tracking, Cancel on purge → cache file gone; sent
files remain on disk (rows retained). **T6 — Delete with purge**: sent rows and files removed too.
**T7 — Partial delivery**: with one bad + one good address, the row lists only the delivered one.

### Regression Checks
- **R1** Download button unchanged; **R2** email content unchanged; **R3** suite at baseline;
  local dry run (mailer stubbed): 1 send → 1 row (send_now, 33 snapshots, score 67), sent file
  exists, 1 cache file; test artifact removed.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] T7 · [ ] R1–R3

### Notes for the testing agent
NEEDS BROWSER TESTING + inbox.

## TRENDS-EMAIL-COPY — Trend Report email rewritten for an outside reader (no app button)

**Shipped:** 2026-09-15 · **Area:** Trends email · **Type:** UX fix

### What changed
- Removed the "Open Trends in Pulse" button (recipients rarely have a login and landed on the
  sign-in screen). Replaced by a quiet muted footer: "Sent from Pulse by <sender>. Pulse users
  can sign in to see the full trend." (plain-text link).
- Body now: heading with the entity; "Attached is the AI Reputation Trend Report for <entity>
  covering <first> to <latest> (N snapshots). It shows how AI assistants currently present the
  organization, how that has moved over time, and which pillars are driving the change." Latest
  Pulse Score line with "(+N since the previous snapshot)". "Questions about the report? Just
  reply to this email and it will reach <sender>."
- Sender: Send now → the signed-in user's name; scheduled/after-run → whoever set up the tracking
  (email local part shown as a name). Internal phrasing ("tracked in Pulse with report delivery
  turned on") removed.

### Test Cases
**T1**: Send now → email has no button; footer line present with a plain "sign in" link; body shows
period, snapshot count, score + delta, and "reach <your name>"; PDF attached.
**T2**: scheduled send → "reach <tracker's name>" / "Sent from Pulse by <tracker's name>".

### Acceptance Checklist
- [ ] T1 · [ ] T2

### Notes for the testing agent
NEEDS INBOX TESTING. Dry run (mailer stubbed) produced the expected text; no button in the HTML.

## COMPOSITE-HELP — "What is Practice Composite?" explainer wherever the checkbox appears

**Shipped:** 2026-09-15 · **Area:** Deep Diagnostic (Hospital), Compare Two (both sides), Event Prep (FQHC) · **Type:** UX

### What changed
- Every Practice Composite checkbox now has (a) a hover tooltip with a one-sentence summary and
  (b) an inline **ⓘ What is this?** link that opens a shared pop-over ("What is Practice
  Composite?") describing what the table contains (affiliated practices with Google, Healthgrades,
  Vitals, WebMD, Yelp, RateMDs ratings + links, weighted averages), where the list comes from,
  when to use it, that it does not change the Pulse Score, the runtime cost, the optional physician
  rows, that Specialty/Service Line runs include it automatically, and what the FQHC variant means.
- Clicking ⓘ does not toggle the checkbox (event is stopped).

### Test Cases
**T1**: Deep Diagnostic → Hospital → Advanced options → hover "Practice Composite" shows the
summary; click "ⓘ What is this?" → pop-over opens; ✕ / Got it / backdrop close it; the checkbox
state is unchanged by clicking ⓘ.
**T2**: Compare Two → both sides (Hospital type) show the same tooltip and link.
**T3**: Event Prep → Community Health → "Include Practice Composite" has the ⓘ link (same pop-over,
which mentions the FQHC meaning).

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3

### Notes for the testing agent
NEEDS BROWSER TESTING.

## HELP-EVERYWHERE — "ⓘ What is this?" links and tooltips on every decision point (Tiers 1–3)

**Shipped:** 2026-09-15 · **Area:** all report pages, Trends · **Type:** UX

### What changed
- One shared help pop-over (`#help-modal`) driven by a topic map (`_HELP_TOPICS`); `showHelp(topic)`
  from any link; clicking a link inside a label never toggles the checkbox. The earlier
  Practice Composite pop-over is now one topic of this system.
- **Tier 1 (changes what is measured)**: "Which type?" link on every Analysis Type / Type toggle
  (Competitors Rankings, Deep Diagnostic, Compare Two both sides, Trends add flow) →
  "Which analysis type should I pick?" with the four types, the market-page types, and the two
  common mistakes. "Composite analysis related hospitals only" (Deep Diagnostic, Compare Two A/B),
  the market aggregate toggle and the Trends aggregate toggle → tooltip + "Composite analysis of
  related hospitals" topic. **Cache overrides standardized** to one label everywhere — "Refresh
  from scratch — ignore cached results (Admin only)" — with one tooltip and the "Cached results and
  refreshing from scratch" topic (Competitors Rankings, Student Health, Deep Diagnostic, Hospital
  Network, Compare Two, Event Prep).
- **Tier 2**: Report Format ("Which format?"), Include Physicians in Composite, Pulse Briefing,
  Hospital Network service-line scorecard, Facility Type, Create Teaser (Compare Two) and teaser
  per entity (Event Prep), Event Prep state-discrepancy confirmation → "Event Preparation options".
- **Tier 3**: By City / By ZIP tooltips on every market page + "Location" link on Deep Diagnostic
  ("By City or By ZIP Code"); Compare Two service-line toggles ("Analyze as a service line");
  Trends Collection Schedule (add flow) and Cadence (Details panel) → "Tracking schedule and scope".

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Every link opens the right topic**: walk each page and click every ⓘ link (30 in total);
the pop-over title matches the control; ✕ / Got it / backdrop close it; no checkbox toggles when
clicking a link inside its label; no console errors.
**T2 — Cache label**: on every page the admin cache option reads "Refresh from scratch — ignore
cached results (Admin only)" with the same tooltip; behaviour unchanged (still admin-gated).
**T3 — Type links**: Deep Diagnostic / Compare Two / Trends / Competitors Rankings type toggles
show "ⓘ Which type?"; the pop-over lists Hospital, Hospital Service Line, Specialty Practice,
Community Health and the two mistakes.
**T4 — Deep Diagnostic location label** keeps its ⓘ link after switching types (Market ↔ Location).
**T5 — Tooltips**: hover By City / By ZIP on each market page; hover the related-hospitals,
teaser, physicians, briefing and service-line scorecard labels.

### Regression Checks
- **R1** Practice Composite link (COMPOSITE-HELP) still opens its content.
- **R2** Form behaviour unchanged everywhere (links are display-only).

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING.

## HISTORY-COMPARE-TWO — Compare Two reports are persisted and downloadable from History

**Shipped:** 2026-09-15 · **Area:** Compare Two, History · **Type:** bug fix

### What changed
- Root cause: a head-to-head run analyzed each side with PDF generation skipped (or reused a
  cached run, creating no row) and kept the combined PDF path only in the in-memory job. History
  therefore showed 0–2 clinic rows with nothing downloadable, and the result-screen link died on a
  server restart (an old release note acknowledged the 404).
- New `comparison_runs` table; `_job_run_comparison` records every comparison (both entities,
  markets, specialty, PDF path, teaser flag, role, ran_by). Side runs created only for the
  comparison (no PDF of their own) are tagged `analysis_runs.comparison_id` and hidden from
  History; cached full runs stay visible.
- History shows one row per comparison: name "A vs B", badge **Compare Two · <specialty>**,
  Analyzed = 2, Downloads → **Comparison (PDF)** (or "Comparison — Teaser (PDF)"). Search/window/
  older-runs all include them. Admin Delete run works on them.
- `GET /api/compare/{id}/pdf` accepts the persisted id (History) or a live job id (result screen).
- `scripts/backfill_comparisons.py` recovers rows for comparison PDFs generated before this change
  (dry run by default; `--apply` writes). Locally: 2 found.

### Files changed
`perception/db.py`, `server.py`, `web/index.html`, `scripts/backfill_comparisons.py`,
`docs/qa/test-battery.md`

### Test Cases
**T1 — New comparison**: run Compare Two (two practices) → result screen download works → History
shows "A vs B" with the Compare Two badge and Comparison (PDF); the download is the same combined
report. No empty clinic rows for the two sides (unless a side was a prior full run with its own
PDF, which stays).
**T2 — Restart**: restart the server → the History download still works.
**T3 — Teaser**: run with "Create Teaser version" → label reads "Comparison — Teaser (PDF)".
**T4 — Backfill (prod)**: after deploy, run `scripts/backfill_comparisons.py` (dry) then
`--apply` → older comparison PDFs appear in History with their original dates and users.
**T5 — Delete**: admin Delete run on a comparison row removes it and its PDF.

### Regression Checks
- **R1** Trends still counts comparison side runs as snapshots (they remain in analysis_runs).
- **R2** Suite at baseline; round-trip create → history → delete verified locally.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Run the backfill on production once after deploy.

## COMPARE-STREAMLINE — Compare Two rebuilt on the Deep Diagnostic pattern (type-first, flagship line, Locations per side, implicit composite)

**Shipped:** 2026-09-15 · **Area:** Compare Two · **Type:** UX + scoring parity

### What changed
- Each side now starts with **Analysis Type** (Hospital · Hospital Service Line · Specialty
  Practice); fields adapt (Health System / Service Line / Market for a service line; name dedupe
  as on Deep Diagnostic). "Hospital / Health System" label retired.
- **Candidates collapse** to one line after search ("Flagship listing: X · addr · 4.6★ (change)");
  the top match is auto-selected; "change" reopens the cards; "use the name as entered" kept.
- **Locations per side** (practice types): discovery via `/api/practice/siblings` (scoped to the
  service line when applicable) **seeded from same-brand Google candidates** with address, rating
  and place_id; inline prune with checkboxes; count badge. Hospital sides show no list.
- **Practice Composite is implicit** for practice types (hidden, always on; note explains); the
  explicit checkbox remains for Hospital sides only. Aggregate is implicit for practice types.
- **Specialty Practice detection is a hint** ("switch to Hospital Service Line") instead of an
  inline toggle; the service-line type sets the system/line explicitly.
- **Run Compare Two is disabled** ("Discovering locations…") while either side's discovery is in
  flight; re-enabled on success or error.
- API: `confirmed_siblings_a/b` and `anchor_listing_a/b` added to the compare request and passed to
  the practice analyzer → each practice side gets the **roster-based Reviews & Reputation pillar
  and pinned per-location composite table**, exactly like Deep Diagnostic. Practice sides also now
  receive `practice_composite` (previously only hospital sides did).
- Shared helper `_seedFromCandidates(selected, candidates, city, state, discovered)` used by both
  pages.

### Files changed
`web/index.html`, `server.py`, `perception/analyzer.py`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Two practices**: A: Specialty Practice, ORTHOSOUTH / MEMPHIS / TN / ORTHOPEDICS → flagship
line + Locations list (seeded rows with addresses/ratings). B: Specialty Practice, CAMPBELL CLINIC
… → same. Uncheck one location on B. Run → comparison PDF; each side's composite table lists
exactly the checked locations; Reviews pillar consistent with each table.
**T2 — Service line vs practice**: A: Hospital Service Line, HOUSTON METHODIST / HOUSTON / TX /
ORTHOPEDICS (badge "Service line … of HOUSTON METHODIST"); B: a practice → runs.
**T3 — Hospital vs hospital**: both Hospital → no Locations list; related-hospitals + Practice
Composite checkboxes present (composite off by default); runs as before.
**T4 — Hint**: Specialty Practice search that resolves to a department shows the amber hint;
clicking it flips the side to Hospital Service Line, prefills, re-searches.
**T5 — Gating**: during discovery the Run button reads "Discovering locations…" and is disabled;
enabled once both sides finish; a failed discovery on one side still allows running.
**T6 — Change listing**: "change" reopens cards; picking another re-collapses and re-discovers.
**T7 — Start Over** resets both sides to Hospital with empty fields.

### Regression Checks
- **R1** Comparison persists to History (HISTORY-COMPARE-TWO) unchanged.
- **R2** Suite at baseline.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] T7 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. T1 is the key parity check against a Deep Diagnostic of the same practice.

## COMPARE-SUMMARY-FIX — Comparison Summary no longer prints raw JSON; layout and naming fixed

**Shipped:** 2026-09-15 · **Area:** Compare Two PDF · **Type:** bug fix

### What changed
- Root cause: the model occasionally writes the comparison `verdict` as several bare strings
  instead of one string, which made the whole JSON invalid; the old parser then dumped the raw
  text into the report (empty "Where they are similar"/"Key differences" panels, JSON on the page).
- `synthesize_comparison` + `parse_comparison_text`: strict JSON first (fences/newlines repaired),
  then **field-level salvage** with regexes — headline, both bullet arrays and the verdict (joined
  from several strings if needed) are recovered independently, so one malformed field can't blank
  the rest. Stray markup is stripped. A schema-enforced tool call was tried and rejected: with the
  installed SDK/model pairing the arrays came back mangled.
- PDF: the summary's title + headline + panels stay together as one block (verdict may flow);
  panels are omitted only when there are genuinely no bullets; the comparison text now goes
  through the display-time AI Reputation rename (headline said "AI Visibility").
- Verified by regenerating the Orthosouth vs Campbell Clinic comparison from the stored side
  results: 5 similarities, 6 differences, 3-paragraph verdict, panels rendered, no "AI Visibility"
  text anywhere in the PDF.

### Test Cases
**T1**: run any Compare Two → page with "Comparison Summary" shows a headline, two filled panels and
a multi-paragraph verdict; no braces/quotes/JSON keys visible.
**T2**: text-search the PDF for "Visibility" → none.
**T3**: unit: `parse_comparison_text` on a verdict-as-multiple-strings payload → 3 verdict
paragraphs and intact bullets (checked locally).

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3

### Notes for the testing agent
NEEDS BROWSER TESTING.

## RANKINGS-STREAMLINE — Competitors Rankings form: type first, Market Summary default, collapsed prospect step, Advanced options, output note

**Shipped:** 2026-09-15 · **Area:** Competitors Rankings (Enter Location mode) · **Type:** UX

### What changed
1. **Field order**: Analysis Type → Specialty (practice only) → Location of Analysis (By City /
   By ZIP + Radius, map preview unchanged) → Report Format → Prospect (Enticement only) → Advanced
   options → Run. (Was: Location → Type → Format → Prospect → Options.)
2. **Report Format defaults to Market Summary**; the Prospect block is hidden unless Enticement is
   chosen. Page reset also returns to Market Summary.
3. **Prospect step**: after Resolve (or Enter), the best Google match is auto-selected and shown as
   "✓ Prospect: X — address · change"; the candidate list collapses ("change" reopens it; "Use
   This Name" still available). **Run is disabled** while Enticement has no confirmed prospect,
   with the note "Resolve the prospect above to enable the run."
4. **Advanced options** (collapsed): the aggregate toggle and the admin refresh override.
5. **Output note** beside Run: "Produces the Market Summary: …" / "…the Enticement report: …" /
   "…the Full Report: …", updated as the format changes.
- Upload Spreadsheet and Student Health modes are unchanged.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Order/default**: open Competitors Rankings → Analysis Type is first; Market Summary is
selected; no Prospect field; note reads "Produces the Market Summary…"; Run enabled.
**T2 — Enticement**: pick Enticement → Prospect block appears; Run disabled with the resolve note;
type a name + Enter → best match auto-selected and shown on one line; Run enabled; "change" reopens
the list; picking another updates the line. Run → Enticement report with that prospect.
**T3 — Full Report**: pick Full → note updates; Run enabled; run works.
**T4 — Specialty**: Specialty Practice → Specialty field appears directly under the type; ZIP mode
map still works; a service-line prospect still auto-fills specialty and switches type as before.
**T5 — Advanced**: expand → aggregate (checked) and, for admins, the refresh override; both still
apply to the run.
**T6 — Reset**: navigate away after a completed run and back → form back to Hospital Market /
By City / Market Summary with empty fields.

### Regression Checks
- **R1** Upload Spreadsheet + Student Health flows unchanged. **R2** JS parses; element ids unchanged.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING.

## HISTORY-FILTERS — History filter bar: search + Type + Run By + date range, AND-combined, server-backed

**Shipped:** 2026-09-15 · **Area:** History · **Type:** UX

### What changed
- The single "Search by location or name" box is now a **filter bar**: search text (name, location,
  specialty), **Type** (All / Hospital / Hospital Network / Hospital Service Line / Specialty
  Practice / Community Health / Compare Two / Event), **Run By** (Everyone / Mine / each user who
  has runs), and **Date range** (Last 45 days / 90 days / 12 months / All time). All AND-combine.
- Filters are sent to the server (`type`, `ran_by`, `mine=1`, `days` / `all=1`, `q`) so a filtered
  view spans all history, not just the loaded rows; the loaded rows are filtered instantly first.
- Active filters show as chips with **Clear all**; the footer reads "N runs match in the last 45
  days / across all history · Clear filters" when any filter is active.
- Search text and filters persist for the session (coming back to History keeps them). The instant
  session cache is used only for the unfiltered 45-day default.
- `GET /api/history` also returns `filtered_total` and `ran_by_options`; the endpoint now reads the
  user payload (for Mine) instead of the bare role.

### Files changed
`server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — Type**: pick "Compare Two" → only comparison rows; "Hospital Network" → only network rows;
chip appears; footer count; Clear all resets.
**T2 — Run By**: pick a user → only their runs; "Mine" → only yours (matches your email on the
run); combine with Type → both apply.
**T3 — Search + filters**: type "ortho" with Type = Specialty Practice → practice rows matching
"ortho" across all history (older than 45 days included); clear the text → back to the type filter
within the date range.
**T4 — Date range**: "All time" → every run (Show older buttons hidden); "Last 12 months" → window
widens; "Last 45 days" → default.
**T5 — Persistence**: set filters, navigate to Home and back → filters and chips still applied.
**T6 — Sort** still works on filtered results; Downloads menus unchanged; admin Delete run works.

### Regression Checks
- **R1** Unfiltered default load unchanged (instant paint, 45-day window, Show older runs).
- **R2** Suite at baseline; local: type buckets sum to the total (547); mine/ran_by/q combine.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Older runs may have no Run By recorded, so "Mine" only matches runs made
since run attribution was added.

## TRENDS-FILTERS — Trends list filter bar, "Needs attention", sort options; zigzag icon

**Shipped:** 2026-09-15 · **Area:** Trends · **Type:** UX

### What changed
- Sidebar/empty-state icon for Trends is now an inline zigzag trend-line arrow.
- Filter bar above the tracked list (client-side, instant, AND-combined, persisted per session):
  search (name, market, specialty/service line, parent system, notes) · Type (Hospital / Hospital
  Service Line / Specialty Practice) · Status (**Active** default / Paused / All) · Cadence ·
  Tracked by (anyone / Mine / each creator) · **Needs attention** checkbox · Sort (active first /
  latest score / biggest change / next run / name).
- "Needs attention" = score fell since the previous snapshot, a scheduled run is >24 h overdue, or
  the history is on a different rubric than the entity's type. Matching rows get an amber badge
  whose tooltip lists the reasons.
- Chips + Clear all; "N of M tracked entities match" line when filtering; empty-match state with a
  Clear all link; search keeps focus while typing. "Mine" resolves your email via /api/auth/me.

### Files changed
`web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1**: type part of an entity name → list narrows instantly; type a client label from Notes → the
entity appears; clear → all active entities.
**T2**: Status = Paused → only paused; All → both; default Active hides paused.
**T3**: Type + Tracked by → both apply; "Mine" → entities you created.
**T4**: Needs attention → only entities with a drop / overdue run / rubric mismatch; each shows the
amber badge with reasons on hover.
**T5**: Sort by latest score, biggest change, next run, name → order changes accordingly.
**T6**: navigate away and back → filters/chips persist; Clear all resets to defaults.
**T7**: sidebar Trends icon is the zigzag arrow in the full sidebar and in the Home rail.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] T7

### Notes for the testing agent
NEEDS BROWSER TESTING.

## TRENDS-ROSTER — Practice tracking uses a fixed, confirmable Locations roster; "Find more locations" for large groups

**Shipped:** 2026-09-15 · **Area:** Trends (add flow, runs, list/Details), Places search · **Type:** feature + data quality

### What changed
- **Track New Entity** (Specialty Practice / Hospital Service Line): after the listing is chosen, a
  **Locations to track** step discovers the practice's locations (`/api/practice/siblings`, scoped to
  the service line when applicable), seeds them from the same-brand Google candidates with address,
  rating and pinned place_id, and lets you uncheck strays. "Add to Trends" is disabled while
  discovery runs.
- **Find more locations** (`POST /api/practice/find-more`): widens the Google search — brand alone,
  brand + state, brand near the market city, plus the brand's **acronym** ("IBJI …" listings) —
  then **snowballs** through every city seen in a found address (capped at 14 city queries).
  Results merge by place_id, keep only brand/acronym name matches, and append as checked rows
  tagged "new". Illinois Bone & Joint Institute: 1 → 70 listings across 25 cities in ~14 s.
- **Saved roster**: `tracked_entities.confirmed_roster` (JSON) + `anchor_listing` (auto-migrated).
  Every snapshot (create, Run now, scheduler) passes the saved roster and flagship listing to the
  practice analyzer, so **each snapshot measures the same locations** (no roster drift between
  runs). Entities created before this keep discovering each run and are labelled so.
- List row sub-line shows "N locations" (or "locations discovered each run"); Details panel has a
  locked **Locations** field with a "view" toggle listing the flagship + roster. The roster is part of
  the locked identity (track a new entity to change it).

### Files changed
`perception/db.py`, `server.py`, `web/index.html`, `docs/qa/test-battery.md`

### Test Cases
**T1 — IBJI**: Track New Entity → Specialty Practice → ILLINOIS BONE AND JOINT INSTITUTE / CHICAGO /
IL / ORTHOPEDICS → Search → listing auto-selected → Locations to track shows the discovered rows;
click **Find more locations** → dozens more append (tagged new, checked), many named "IBJI Doctors'
Office - <city>"; uncheck physical-therapy/rehab rows if unwanted → Add to Trends → row shows
"N locations"; first snapshot runs; Details → Locations "N fixed locations · view" lists them.
**T2 — Stability**: Run now twice → both snapshots use the same roster (composite table rows
identical set); score movement only reflects reputation.
**T3 — Service line**: Hospital Service Line add → Locations scoped to the line; Find more works.
**T4 — Hospital**: no Locations step; unchanged.
**T5 — Legacy**: an older practice entity shows "locations discovered each run" and Details says
to track a new entity to fix the roster.
**T6 — Gating**: Add to Trends disabled ("Discovering locations…") until discovery completes; a
failed discovery still allows adding (roster = whatever is listed).

### Regression Checks
- **R1** Deep Diagnostic / Compare Two Locations steps unchanged (shared seeding helper).
- **R2** Suite at baseline; find-more exercised live against Google Places.

### Acceptance Checklist
- [ ] T1 · [ ] T2 · [ ] T3 · [ ] T4 · [ ] T5 · [ ] T6 · [ ] R1–R2

### Notes for the testing agent
NEEDS BROWSER TESTING. Find more makes up to ~22 Places queries (a few cents); results depend on
Google's listing names — prune physical-therapy / rehab locations if they shouldn't count.

## HANDOFFS + COMPLETION EMAILS — next steps after every run, "Runs in progress" on Home

Commit: handoffs at the end of every run (Track in Trends / Compare against… / Email report…) on the completion screen and in each History Downloads menu; completion emails for long runs with a per-user preference; "Runs in progress" strip on Home. NEEDS BROWSER TESTING.

**T1 — Completion screen shows a Next row (Deep Diagnostic hospital).** Run a hospital Deep Diagnostic to completion. Below the Download / New Deep Diagnostic buttons, a "NEXT" row shows: 📈 Track in Trends, ⇄ Compare against…, ✉ Email report…, History.
- [ ] All four buttons visible; existing Download / Teaser / Briefing buttons unchanged.

**T2 — Track in Trends prefills and searches.** Click Track in Trends on the T1 screen. Trends opens on the Track New Entity form with type Hospital, name/city/state filled from the report, a flash message at the bottom, and the Locations/listing search already running.
- [ ] Type, name, city and state match the run; step 2 (listing candidates) appears without typing anything.

**T3 — Track in Trends for a practice and a service line.** Repeat T1/T2 for a Specialty Practice run and for a Hospital Service Line run.
- [ ] Practice: type Specialty Practice, specialty filled. Service line: type Hospital Service Line, name = health system, Service Line = the line.

**T4 — Compare against… prefills side A.** On a Deep Diagnostic completion screen click Compare against…. Compare Two opens reset, side A type/name/city/state(/specialty) prefilled, the side-A search runs, flash message shown.
- [ ] Side A candidates appear; side B is empty and ready for input.

**T5 — Email report… (completion screen).** Click Email report…, enter one valid address and an optional note. Button shows "Sending…", then a flash "Report sent to …".
- [ ] Email arrives with subject "<Kind> — <Title>", the PDF attached, the note (if any) at the top, and "Sent from Pulse by <name>" in the footer. No app button.

**T6 — Email validation.** Enter `not-an-email` → alert "Please check these addresses". Cancel either prompt → nothing sent.
- [ ] Both behaviors.

**T7 — History Downloads menu actions.** Open History → Downloads ▾ on: a Deep Diagnostic row, a Compare Two row, a Hospital Network row, a Competitors Rankings row.
- [ ] Deep Diagnostic row: 📈 Track in Trends, ⇄ Compare against…, ✉ Email report… present (admin also sees Delete).
- [ ] Compare Two and Network rows: ✉ Email report… only (no Track / Compare).
- [ ] Rankings row (no single organization): no Track / Compare; Email present when a PDF exists.
- [ ] Track/Compare from History prefill from the row (city/state parsed from "City, ST").

**T8 — Email endpoint for network and comparison rows.** Use ✉ Email report… on a Network row and a Compare Two row.
- [ ] Network email attaches Report + Teaser + Full Detail when they exist; comparison email attaches the head-to-head PDF.

**T9 — Completion email for a long run (preference on).** On Home, ensure "Email me when a long run finishes" is checked. Start a Deep Diagnostic and let it finish.
- [ ] Email "Ready — <Title>" arrives to the signed-in user with the report PDF(s) attached, a line "It took about N minutes" (when ≥1 min), and an "Open Pulse" button.
- [ ] Also for: Hospital Network (base + teaser + full detail attached), Compare Two (one PDF), Community Health, Competitors Rankings (report PDF), Event Preparation (no attachment; "Sign in to download it").

**T10 — Preference off.** Uncheck the Home checkbox (flash "Completion emails turned off"), reload Home (stays unchecked), run a short Deep Diagnostic.
- [ ] No completion email. Re-check → next run emails again. `GET /api/me/prefs` reflects the value.

**T11 — Runs in progress strip.** Start a Network run, then click Home while it runs.
- [ ] "1 run in progress" card above the featured video with a spinner, the network name, and elapsed minutes; it refreshes every ~20 s; after completion it shows ✓ with "Open in History".
- [ ] With nothing running the card still shows the preference checkbox and the "No runs in progress" note.

**R1 — Notification never breaks a run.** Temporarily break RESEND_API_KEY locally and run a Deep Diagnostic.
- [ ] The run completes normally; the server log shows `[notify] failed:` and nothing else changes.

**R2 — Existing completion buttons and History downloads.** Regress: Download Report, Teaser, Briefing, Full Detail links, Delete run (admin), MQCR battery, content-plan actions all unchanged.

**R3 — Users table migration.** Fresh boot on a DB without `users.notify_complete`: the column is added with DEFAULT TRUE; `/api/me/prefs` returns `{"notify_complete": true}` for a user with no row.

## CHOOSER + DUPLICATE WARNING + SCORE EVIDENCE + ADMIN OPERATIONS

Commit: "Which report do I need?" helper on Home; duplicate-run warning before Deep Diagnostic / Hospital Network / Compare Two; score confidence ("Score Evidence") on the completion screen, History rows, Trends list and the Deep Diagnostic PDF; Admin → Operations tab (all users' runs on this server); PDF print-flow rules. NEEDS BROWSER TESTING.

**T1 — Chooser renders and recommends.** Home shows a "Which report do I need?" card under the hero with "I'm looking at…" pills. Pick "One hospital" → an "I want to…" row appears. Pick "Diagnose it and get the fixes".
- [ ] A green recommendation box shows "→ Deep Diagnostic" with a one-line reason and an "Open Deep Diagnostic →" button.

**T2 — Chooser routing matrix.** For each combination check the page opened and the type preselected:
- [ ] Whole health system + Diagnose → Hospital Network. + Compare → Compare Two (both sides Hospital). + Track → Trends (Hospital).
- [ ] One hospital + Rank → Competitors Rankings (Hospital). Practice + Rank → Competitors Rankings (Specialty Practice).
- [ ] Service line + Diagnose → Deep Diagnostic with Hospital Service Line selected. Practice + Compare → Compare Two with Specialty Practice on both sides. Practice + Track → Trends add flow with Specialty Practice.
- [ ] Community health center + Rank/Compare/Track → Deep Diagnostic (Community Health) with the explanatory note.
- [ ] A list of organizations → Event Preparation immediately (no second question).
- [ ] Clear resets both rows.

**T3 — Duplicate warning (Deep Diagnostic).** Run a Deep Diagnostic for a hospital to completion. Start a new Deep Diagnostic for the same listing and click Run Diagnostic.
- [ ] An amber notice appears above the Run button: "<Name> was already run today by <user>…" with "⬇ Open that report", "History" and "Run anyway →". The run has NOT started.
- [ ] "Open that report" downloads the earlier PDF. "Run anyway →" starts the run normally; the notice disappears.
- [ ] Changing the listing (different organization) → no notice.

**T4 — Duplicate warning (Network, Compare Two).** Repeat with a Hospital Network run of the same system name, and a Compare Two of the same pair (also with A/B swapped).
- [ ] Both show the notice; swapped pair still matches; "Run anyway" proceeds.

**T5 — Duplicate warning role scope.** As a non-admin user, a run by another role is not reported; as admin, all roles' runs are reported.

**T6 — Score Evidence on completion.** Finish a Deep Diagnostic.
- [ ] The completion stats show a "Score Evidence" box (High / Medium / Low, colored) with a note like "640 reviews" or "only 12 reviews; no CMS star rating on record".
- [ ] Community Health completion also shows the box. Competitors Rankings, Network and Compare Two do not.

**T7 — Score Evidence in History and Trends.** Open History: Deep Diagnostic rows run after this deploy show a small "High/Medium/Low evidence" chip after the type badge (hover shows the note). Older rows show nothing. Open Trends: entities whose latest snapshot has evidence show the chip after the score.

**T8 — Score Evidence in the PDF.** Open the Deep Diagnostic PDF (hospital and practice).
- [ ] Under the score's profile chip: "Evidence: High · 312 reviews across 4 locations" (color by level). Practice reports count reviews across confirmed locations.

**T9 — Admin → Operations.** As admin open Admin → Operations.
- [ ] Table of runs: Started, Kind (Deep Diagnostic / Competitors Rankings / Community Health / Compare Two / Hospital Network), Run, User, Status, Elapsed; header line "N running · N done · N failed · vX · up N m · N workers".
- [ ] With a run in progress the table refreshes every ~15 s; Refresh button works; finished rows show a History button.
- [ ] Integrations Admin role does not see the Operations tab.

**T10 — PDF print flow.** Generate a Deep Diagnostic, Hospital Network and Community Health PDF.
- [ ] No heading stranded at the bottom of a page, table rows not split across pages, table headers repeat on continued pages, no single orphan lines.

**R1 — Regression.** Existing Home cards, "Runs in progress" strip, Run buttons (with no earlier run → no notice, run starts directly), Admin Users / Requests / Integrations tabs unchanged.

**R2 — Fail-soft.** If the recent-match call fails (e.g. offline), the Run button proceeds without a notice. If confidence computation fails, the run completes and the log shows `[confidence] failed:`.

**R3 — Migration.** Fresh boot adds `analysis_runs.confidence` and `confidence_note`; History and Trends load with NULLs (no chip).

## HOME-VIDEO-SIZE — welcome video at about a third of the card width

Commit: Home featured video is floated right at ~36% of the card width (max 360px); the text wraps beside it. Learn/Methodology embeds are unchanged. NEEDS BROWSER TESTING.

**T1 — Home layout.** Sign in → Home. The welcome video sits on the right at roughly a third of the card width; the title and text fill the space to its left; nothing overlaps the "Which report do I need?" card below.
- [ ] Video plays; the end-of-video reset still works (returns to poster, no host end screen).

**T2 — Narrow window.** Below ~760px wide the video returns to full width above/below the text.

**R1 — Learn pages.** Video embeds in Learn / Methodology articles stay full width.

## CHOOSER-EVERYWHERE — "Which report do I need?" from the sidebar, every report page, and the completion screen

Commit: one shared chooser modal; sidebar "✦ Start a report" entry at the top of Report Creation; "Not sure this is the right report?" link beside the title of Hospital Network, Deep Diagnostic, Competitors Rankings, Compare Two and Event Preparation; "✦ Start another report…" in the completion screen's Next row. NEEDS BROWSER TESTING.

**T1 — Sidebar entry.** On any page click "✦ Start a report" (teal button under Report Creation).
- [ ] A modal titled "Which report do I need?" opens with the two-question chooser; picking answers shows the recommendation; "Open <Report> →" closes the modal and opens that page with the type preselected (same matrix as the Home card).
- [ ] Esc, ✕ and clicking outside close it. The sidebar highlight stays on the page you navigated to, not on Start a report.
- [ ] On Home (icon rail) the entry shows as the ✦ icon only and expands on hover with the rest of the rail.

**T2 — Page title links.** Each of the five report pages shows a small "Not sure this is the right report?" link after the title. Click it → same modal, starting blank.
- [ ] Present on Hospital Network, Deep Diagnostic, Competitors Rankings, Compare Two, Event Preparation. Not on History, Trends, Admin, Learn, Feedback.

**T3 — Completion screen.** Finish any run. The Next row includes "✦ Start another report…" → modal → recommendation → navigates.

**T4 — Shared state.** Pick answers on the Home card, then open the modal from the sidebar: the modal starts blank (each open resets). Pick answers in the modal, close it, look at the Home card: it shows the modal's answers (one chooser, two views). Clear on the Home card resets both.

**R1 — Help modal and other modals unchanged.** ⓘ links still open the help modal; Esc closes the chooser only (help modal keeps its own close behaviour).

## HOME-VIDEO-HERO — welcome video moved into the green hero, small, right column

Commit: the Home hero is two columns — title/lede/buttons on the left, the welcome video (≈280px wide, "WELCOME" label) on the right. The white featured card below is hidden when the Home content is only the video (and its title); any text the admin added stays in the card without the video. NEEDS BROWSER TESTING.

**T1 — Hero layout.** Sign in → Home. The video sits inside the green box on the right, small, with a soft shadow; the title, lede and three buttons are on the left; the "Which report do I need?" card follows immediately below with no empty white card in between.
- [ ] Video plays; end-of-video reset still returns it to the poster.

**T2 — Home content with text.** As admin add a paragraph of text to the Home content (Learn → Manage Content → Home page) alongside the video link.
- [ ] The video still moves to the hero; the white card below shows the title + text only.
- [ ] Remove the text again → the card disappears.

**T3 — No Home content.** With no Home content published, the hero has no right column and the placeholder card ("A short welcome video is coming soon") shows as before.

**T4 — Narrow window.** Below ~860px the hero stacks: text first, video below at up to 360px wide.

## TRENDS-NOTES — dated notes on a tracked entity: markers on the score chart and in the Trend Report

Commit: new `trend_annotations` table; GET/POST `/api/track/entities/{id}/annotations`, DELETE `/api/track/annotations/{aid}` (author or admin); Notes card under the Pulse Score chart with add/delete; numbered dashed markers on the score chart (web) and in the Trend Report PDF with a Notes list under the chart; "+ note" shortcut per snapshot row; PDF cache fingerprints the notes so adding/removing one rebuilds the report. NEEDS BROWSER TESTING.

**T1 — Add a note.** Open Trends → an entity with ≥2 snapshots. In the Notes card, pick a date between two snapshots, type "New website launched", click Add note (or press Enter).
- [ ] The note appears in the list numbered ①, with date and author; a flash confirms; the score chart shows a dashed amber vertical line at the interpolated date with a "1" badge at the top.
- [ ] A second note gets ② and a second marker; order is by date, not by entry order.

**T2 — Dates outside the snapshot range.** Add a note dated before the first snapshot and one after the last.
- [ ] Markers clamp to the first/last snapshot positions (no marker off-canvas).

**T3 — Snapshot shortcut.** In All Snapshots click "+ note" on a row → the Notes date field is set to that snapshot's date and the text box is focused.

**T4 — Delete permissions.** The author sees ✕ on their notes; another non-admin user does not; admin sees ✕ on all. Deleting removes the marker immediately.

**T5 — Validation.** Empty note → focus stays in the box, nothing sent. 301+ characters blocked by the input (maxlength). Bad date → 400 shown in the status area.

**T6 — Trend Report PDF.** Download the Trend Report after adding notes.
- [ ] The Pulse Score chart shows the same numbered markers; a "NOTES" list sits under the chart with "① Mar 3, 2026 — New website launched".
- [ ] Add another note and download again → the PDF includes it (cache rebuilt). Remove it → rebuilt again without it.
- [ ] Send report… and scheduled emails carry the notes too (same renderer).

**R1 — Entities with one snapshot / none.** One snapshot: marker sits on the single point. No snapshots: the charts area (and Notes card) stays hidden as before.

**R2 — Tier and Google charts unchanged** (markers only on the score chart).

## ADMIN-MAINTENANCE — server-side one-off cleanups (dry run / apply)

Commit: `GET /api/admin/maintenance` lists tasks; `POST /api/admin/maintenance/{task}?apply=` runs one (admin only). Tasks: rebrand-learn, backfill-comparisons, retrack-practices. Admin → Operations gains a Maintenance card with Dry run / Apply per task and an output box. NEEDS BROWSER TESTING.

**T1 — List + dry runs.** Admin → Operations → Maintenance shows three tasks with descriptions. Click Dry run on each: the output box shows "DRY RUN — <task>" and what would change; nothing is written (re-running shows the same).

**T2 — Apply confirm.** Click Apply → a confirm dialog; Cancel does nothing. OK runs it and the box shows "APPLIED — <task>" with the lines; a second Apply reports nothing to do (idempotent).

**T3 — Non-admin.** Integrations Admin / user roles get 403 on both endpoints and do not see the Operations tab.

## ORG-TYPEAHEAD — smart organization fields + "Find an organization" on Home

Commit: `GET /api/entities/suggest?q=&kinds=` returns organizations the team already analyzed or tracked (Deep Diagnostic runs, Hospital Network runs, Trends entities; role-scoped for non-admins; deduped by kind+name+city; most recent first, prefix matches first). A shared typeahead is attached to: Deep Diagnostic name, Compare Two A and B names, Competitors Rankings prospect, Hospital Network name, Trends Track-New name, and the new Home "Find an organization" box. Picking a suggestion sets the analysis type, fills city/state/specialty (or health system + service line) and starts that form's listing search. NEEDS BROWSER TESTING.

**T1 — Deep Diagnostic typeahead.** Type 3+ letters of an organization you ran before into Organization Name.
- [ ] A dropdown "Already analyzed by your team" lists matches with kind, city/state, specialty, "last run N days ago by <user>" and "tracked in Trends" where applicable. Arrow keys move, Enter picks, Esc closes, clicking picks; blur closes.
- [ ] Picking a Specialty Practice sets the type to Specialty Practice, fills city, state and specialty, and the listing search runs. Picking a Service Line fills Health System + Service Line. Picking a Community Health entry sets that type. Picking a Hospital Network entry opens as Hospital type.
- [ ] Typing an unknown name shows no dropdown and the form works exactly as before.

**T2 — Other forms.** Same behaviour on Compare Two side A and B (search runs for that side), Competitors Rankings prospect (type + market city/state filled, Resolve runs), Hospital Network name (uppercased, HQ city/state filled, Find Hospitals runs), Trends → Track New Entity (type + fields filled, listing search runs).
- [ ] Enter on a highlighted suggestion picks it and does not also fire the field's own Enter action (e.g. Network's Find).

**T3 — Home "Find an organization".** On Home, type a known name.
- [ ] Same dropdown plus a last row "＋ Analyze “<typed>” as a new organization →". Picking a known one shows a green card with the name, meta and buttons: Deep Diagnostic, Compare against…, Track in Trends, History (Hospital Network entries also get a Hospital Network button; Community Health entries have no Track button).
- [ ] Each button opens the right page prefilled and searching; History opens filtered to that name. The "new organization" row opens Deep Diagnostic with the typed name in the field.

**T4 — Role scoping.** As a non-admin role, suggestions only include that role's runs (Trends entities are shared).

**R1 — Regression.** All six forms still submit normally when typing without picking; the Learn editor's category picker unchanged.

## PERF-INIT-ONCE — schema check once per process; suggestion index cached

Commit: `init_db()` now runs its ~80 DDL/migration statements once per server process (it was re-run on every request by ~100 handlers, costing seconds against the remote Postgres); `init_db(force=True)` re-runs it. The typeahead index (analyzed + tracked organizations) is loaded once per role and cached for 2 minutes; each keystroke filters in memory. NEEDS BROWSER TESTING.

**T1 — Typeahead speed.** On any organization field type 3 letters: the first dropdown after a deploy may take ~1 s; subsequent keystrokes and other forms respond in well under a second.

**T2 — Freshness.** Run a new Deep Diagnostic for a never-before-analyzed organization; within 2 minutes it appears in the suggestions (or immediately after a server restart).

**T3 — General responsiveness.** History, Trends and Admin pages load noticeably faster on production (each request no longer re-runs the schema migrations).

**R1 — Fresh database.** First request after boot still creates/migrates all tables (Home, History, Trends, Admin all load on an empty database).

## FORM-SIMPLIFY — fewer decisions on the report pages

Commit: (1) By City / By ZIP toggles removed from Deep Diagnostic and Competitors Rankings — ZIP mode is a checkbox under Advanced options; Deep Diagnostic report title shown as text with a "rename" link instead of an editable field; Competitors Rankings Report Format (Enticement / Market Summary / Full) moved under Advanced with Market Summary default. (2) Compare Two Entity B inherits Entity A's type, specialty/service line and city/state once A is confirmed, with a "Same as Entity A" hint; Teaser + Refresh moved under Advanced on Compare Two and Event Preparation. (3) Community Health intake prefilled with typical Section 330 answers (sliding fee, no one turned away, Medicaid, Medicare, uninsured, enrollment assistance, new patients, 330 grantee); HRSA prefill now sets 330 / look-alike explicitly. NEEDS BROWSER TESTING.

**T1 — Deep Diagnostic location.** Main form shows Location with City + State only (no By City / By ZIP buttons). Advanced options → "Search by ZIP code instead of city" → ZIP field replaces City/State; unchecking restores them. Hospital Service Line hides the ZIP option entirely.
- [ ] A run by ZIP still works end-to-end.

**T2 — Deep Diagnostic title.** After confirming a listing, the Confirm card shows the title as teal text with a small "rename" link. Click rename → editable field, type, Enter or click away → text updates and the PDF uses the new title. Esc also closes.

**T3 — Competitors Rankings.** Main form: type, specialty (practice), city/state, then Run. Advanced options contains Report Format (Market Summary selected), "Search by ZIP code & radius instead of city", Aggregate, and (admin) Refresh. Choosing Enticement in Advanced reveals the Prospect / Target field in the main form as before.
- [ ] Runs in all three formats and in ZIP mode still work.

**T4 — Compare Two inherits.** Set Entity A to Specialty Practice, Orthopedics, Memphis TN, search and confirm. Entity B appears with Specialty Practice selected, Orthopedics, Memphis, TN filled and the teal hint "Same as Entity A: Specialty Practice · Orthopedics · Memphis, TN…". The name field is focused.
- [ ] Change B's city, then re-confirm a different A listing → B's edits are NOT overwritten. Start Over → B blank, hint hidden.
- [ ] Hospital A → B Hospital with A's city/state. Service Line A → B Hospital Service Line with the service line and market filled.

**T5 — Compare Two / Event Prep advanced.** Teaser and Refresh checkboxes now sit under an "Advanced options" disclosure on the Compare Two run card and on Event Preparation. Defaults unchanged (unchecked).

**T6 — Community Health intake.** Choose Community Health, confirm a center: the attestation step opens with the eight typical boxes already checked and Look-Alike unchecked. When HRSA finds the center, 330 / look-alike reflect HRSA; policy fields reflect the web-searched values. Uncheck any box → the run's intake reflects it.

**R1 — Regression.** History, Trends, Home unchanged; typeahead still attached to every form; the ⓘ help links on moved controls still open the right topics.

## FORM-SIMPLIFY-2 — Network, Event Prep, Compare Two and Trends: fewer steps and defaults

Commit: (1) Hospital Network: Facility Type, Website URL, service-line scorecard and Refresh moved under Advanced options; Headquarters City/State optional and inferred from the facilities found (teal hint, editable); Confirm Roster collapsed by default with a preview and "Review or exclude facilities" link, opens automatically when discovery reports low confidence. (2) Event Preparation: Event Name optional (defaults from the file name) and Date optional; Entity Type moved to the Confirm step and auto-detected from the file (rows with a specialty → Specialty Practice); the practice content and FQHC composite options are always on and no longer shown. (3) Compare Two: the per-side hospital options (aggregate related hospitals, Practice Composite) moved under the single Advanced options disclosure on the run card. (4) Trends → Track New Entity: monthly schedule stated as a sentence with "change"; Notes removed from the add flow (still editable in the configuration panel). NEEDS BROWSER TESTING.

**T1 — Network main form.** Only Organization Name, Headquarters City (optional), State and the Find button are visible, plus the three-reports note and an Advanced options disclosure holding Facility Type (Hospital Network default), Website URL, "Also analyze individual departments" and (admin) Refresh.
- [ ] Leave HQ blank, enter a system name, Find → after discovery City/State are filled from the facilities and a teal hint says so; the run's location in History matches.
- [ ] Enter HQ yourself → it is kept, no hint.
- [ ] Non-hospital facility types still work from Advanced (labels/buttons update as before).

**T2 — Network roster.** After discovery the card shows "N hospitals found across M states", a preview of the first names, and "Review or exclude facilities ▾"; the checklist is hidden. Click the link → list opens; uncheck one → Run analyzes the rest. When discovery returns a confidence note, the list is open by default.

**T3 — Event Prep step 1.** Fields: Event Name (optional), Event Date (optional), CSV. Choosing a file fills an empty Event Name from the file name (underscores/dashes → spaces, no extension). Upload with the name cleared → a name is derived; History shows it.

**T4 — Event Prep confirm step.** A type row sits above the entity table with the three radios and a detected-type hint:
- [ ] File with a specialty on most rows → Specialty Practice selected, hint "Detected from the file: X of N rows have a specialty…". Practice attendees get the combined report with content findings (no checkbox).
- [ ] File without specialties → Hospital selected with the "No specialty column" hint. Switch to Community Health → runs FQHC with sites included (no checkbox).
- [ ] Discrepancy confirmation still appears only when a CSV state differs from the resolved state.

**T5 — Compare Two advanced.** Run card → Advanced options shows Entity A / Entity B sections (aggregate related hospitals, Practice Composite — hospital type only) and a Report section (Teaser, admin Refresh). Practice-type sides hide their hospital options as before; the aggregate flags reach the run.

**T6 — Trends add flow.** Step 3 reads "Collected monthly and charted over time. change"; clicking change reveals the schedule select; Notes field gone. Add an entity → schedule monthly (or the changed value); notes editable in the configuration panel. Reopen Track New Entity → line resets to monthly.

**R1 — Regression.** Typeahead on np-name / cmp names / te-name unchanged; Hospital Network bulk (CSV) mode for admins unchanged; Event Prep progress and results unchanged.

## HELP-CATCHUP — help topics, page modals and Learn articles updated for the simplified forms

Commit: rewrote 4 stale help topics (location mode, report format, event options, tracking schedule), noted "under Advanced options" on 4 more (teaser, facility type, related hospitals, practice composite); added 4 topics — Score Evidence, Organization suggestions, Already run recently, Trend notes — linked from the evidence chip/box, the suggestions dropdown header, the duplicate notice and the Trends Notes card; fixed the Service Line and Event Prep page modals; rewrote the Learn articles "How to run a report", "Competitors Rankings", "Compare Two", "Event Preparation"; new maintenance task `apply-learn-content` publishes the seed to the live articles. NEEDS BROWSER TESTING.

**T1 — Help topics.** Open each ⓘ link: Deep Diagnostic Advanced → "Search by ZIP code" ⓘ (describes the checkbox, not a toggle); Competitors Rankings Advanced → "Which format?" (Market Summary default, others under Advanced); Event Prep discrepancy ⓘ (type detected on Confirm step, no practice checkbox); Trends add flow schedule ⓘ (monthly default, change link). Teaser / Facility type / Related hospitals / Practice Composite topics end with the "under Advanced options" note.

**T2 — New topics.** Click a Score Evidence chip in History or Trends, the "What is this?" beside Score Evidence on a completion screen, the ⓘ in the suggestions dropdown header, "Why this check?" in a duplicate-run notice, and ⓘ on the Trends Notes card → each opens the matching pop-over; the chip click does not navigate; the dropdown ⓘ does not close the dropdown before the pop-over opens.

**T3 — Page modals.** Deep Diagnostic → "How to analyze a service line" step 7 mentions the rename link (not an editable field) and step 2 mentions suggestions. Event Prep → help modal steps: 1 name optional, 2 upload, 3 type detected on Confirm.

**T4 — Learn articles (after apply-learn-content on production).** Learn → "How to run a report" describes Home (Find an organization, Which report do I need?), Advanced options, the 14-day check, completion emails, handoffs and Score Evidence. "Competitors Rankings" leads with Market Summary. "Compare Two" has the Setting it up line. "Event Preparation" mentions type detection and optional name/date.

**T5 — Maintenance task.** Admin → Operations → Maintenance lists apply-learn-content; Dry run lists the 4 articles; Apply updates them; a second Dry run reports 0.

**R1 — Existing topics unchanged in behaviour** (14 originals still open from their links); admin-edited custom Learn articles untouched by the task.

## REVIEWS-BAND-TOP — reviews pillars can reach 100 (was capped at 92)

Commit: both deterministic review bands (hospital Experience & Reviews in scoring.experience_band; practice Reviews & Reputation in practice_scoring.reviews_band) now split the top band — 4.5–4.6★ → 86, 4.7–4.8★ → 90, 4.9★+ → 94 — with the existing volume nudges (hospital +4 at 1,000+, practice +4 at 400+) and penalties unchanged. Maximum is now 98 with volume (100 reachable only via the inactive physician-panel blend). Lower bands, Community Health (reuses the hospital band) and Student Health are otherwise unchanged. Prompts untouched. NEEDS BROWSER TESTING.

**T1 — Unit tests.** tests/test_reviews_band_top.py passes (4 tests); the suite stays at baseline apart from the 4 new passes.

**T2 — Practice run.** Run a Deep Diagnostic for a practice whose confirmed roster averages ≥4.7★ with 400+ reviews (e.g. a large orthopedic group). Reviews & Reputation shows 94 (4.7–4.8) or 98 (4.9+) in the PDF pillar bars and the per-location table still agrees with the rating/volume used.
- [ ] A practice at 4.5★ / 50 reviews still lands at 82 (thin-volume penalty intact).

**T3 — Hospital run.** A hospital with a 4.9★ front door and 1,000+ reviews shows Experience & Reviews 98; one at 4.5★ / 200 reviews shows 86. Composite moves ≤1 point for hospitals (10% weight) and ≤3 points for relationship-profile practices (23%).

**T4 — Cached scores.** Organizations scored before this deploy keep their canonical score for the 30-day window (or until Refresh from scratch / Run anyway); Trends shows the step at the next snapshot, not retroactively.

**R1 — Ratings below 4.5** produce exactly the same pillar values as before (77/62/47/33 hospital bases; 75/60/45/30 practice bases).

## DUP-CHECK-REFRESH — duplicate-run check skipped when Refresh from scratch is checked

Commit: the 14-day duplicate notice no longer appears on Deep Diagnostic, Hospital Network or Compare Two when the admin "Refresh from scratch — ignore cached results" box is checked (an explicit fresh-run request). Notice wording no longer says "by someone on your team" when the earlier run has no recorded user, and it points at Run anyway / Refresh from scratch. NEEDS BROWSER TESTING.

**T1.** As admin, re-run an organization run today with Refresh from scratch checked → no amber notice; the run starts immediately.
**T2.** Same with the box unchecked → notice appears; Run anyway starts the run.
**T3.** Notice for a run with a recorded user reads "…was already run today by <name>"; for one without, "…was already run today." with no attribution.

## TREND-PDF-V4 — Trend Report redesigned to be read at a glance

Commit: perception/trend_pdf.py rewritten. Header with the organization as the largest text and a one-line meta strip; a headline sentence ("Up 5 points since July, now Upper Middle at 70. Experience & Reviews moved most (+14) and is still the weakest pillar."); three big tiles (latest score + quartile badge, change since tracking began, pillar that moved most); analyst paragraph at 12.5pt; score chart zoomed to the data range (never narrower than 30 points) with named quartile bands, first/last values, thicker line, 10pt axis labels; "What changed" right under the chart (quartile crossings that stick, moves ≥5, notes, setting drift; otherwise a stability sentence); one pillar chart with four coloured labelled lines + a bar table with a plain-English line per pillar (the weakest ends "This is the lever."); Google reputation as two facts with sparklines; snapshots collapsed one-per-day (re-run ×N tag) on their own page; body 11.5pt, tables 11pt, headings 15pt title case. Cache suffix bumped to _v4_. NEEDS BROWSER TESTING.

**T1 — Download.** Trends → an entity with ≥3 snapshots → Trend Report (PDF). Page 1: header, headline, three tiles, analyst paragraph, zoomed score chart with quartile bands, What changed. Page 2: four-pillar chart with right-hand labels (value, name, change), bar table with meaning lines, Google rating and reviews cards. Page 3: Every snapshot table + About the Pulse Score.
- [ ] No heading stranded at a page bottom; the snapshot table starts on its own page; fonts readable without zooming.

**T2 — Same-day re-runs.** An entity run twice on one day shows one row for that day tagged "re-run ×2", one chart point, and no duplicate date labels; the header reads "N snapshots (M runs)".

**T3 — Noise filter.** A 2-point wobble across a quartile boundary that returns next snapshot does not appear in What changed; a crossing that holds does. With no large moves the section reads "No large moves. The score has stayed within N points (lo–hi)…".

**T4 — Notes.** Add a Trends note → numbered marker on the score chart and a numbered line in What changed; removing it rebuilds the PDF (new cache key).

**T5 — Practice entity.** Pillar labels are the practice rubric's; the reviews line cites the Google rating/review count; "Scored on the practice rubric." caption present.

**T6 — Edge cases.** One snapshot: tiles say "First snapshot — no change to report yet", chart shows a single labelled point, pillar chart says not enough snapshots. No Google data: Google section shows the "No Google reputation snapshots yet" line.

**R1 — Emailed and scheduled reports** use the same renderer; Sent reports artifacts are unchanged.

## TRENDS-DISPLAY-NAME — editable entity name in Trends (report title)

Commit: tracked_entities.display_name (new column). Trends → row → Details: "Display name (report title)" field beside the locked "Tracked as" identity; saved with the configuration. Used in the Trends list, the detail header, the Trend Report title (with "tracked as <original>" in the meta line when it differs), the analyst paragraph, the PDF filename and the email subject. Blank → falls back to the tracked name. The identity (entity_name) stays locked; the PDF cache key includes the display name so a rename rebuilds the report. NEEDS BROWSER TESTING.

**T1.** Open Details on an entity, change Display name to "USA Health — University Hospital", Save. The list row and the detail header show the new name (hover shows the tracked name). Download the Trend Report → the header title is the new name and the meta line ends "tracked as Usa Health University Hospital"; the file name uses the new name.
**T2.** Send report… → subject uses the new name. Run now → the snapshot still attaches to the trend (identity unchanged).
**T3.** Clear the field and Save → list, header and PDF revert to the tracked name; no "tracked as" note.
**T4.** Search in the Trends filter bar matches the display name and the tracked name.
**R1.** Notes, schedule, email settings save as before; the "Tracked as" value is read-only.

## TRENDS-DISPLAY-NAME-ECHO — Save echoes the stored display name

Commit: after Save in a Trends Details panel the status reads "Saved — shown as “<exact stored value>”", the detail header updates immediately, and the Display name field has autocapitalize/autocorrect off. NEEDS BROWSER TESTING.

**T1.** Change the display name to "USA Health University Hospital" (capital USA) and Save → status shows exactly that string; list row and detail header show it; the Trend Report title matches after download.
**T2.** Clear the field and Save → status says the tracked name is shown and the display name is blank.

## TRENDS-INTRO-NOTE — how entries get into Trends

Commit: a pale note under the Trends list header explains the two ways to start tracking — "Track in Trends" on a Deep Diagnostic completion screen or in a History row's Downloads menu (fields carry over), or "+ Track New Entity" — and links the schedule help topic. NEEDS BROWSER TESTING.

**T1.** Open Trends: the note sits between the count / Track New Entity row and the filter bar; the ⓘ link opens "Tracking schedule and scope". The note is not shown inside the Track New Entity flow or the entity detail view.

## TRENDS-ENABLE-6 — roster flag + fix, next-run countdown + Run all due, detail handoffs, evidence per snapshot, change alerts, compare on one chart

Commit: (1) practice/service-line entities with no confirmed roster show an amber "⚠ Confirm locations" badge (also a Needs-attention reason); the Details panel gains a "Locations not confirmed" box with Find locations → checklist (flagship pinned) → Save roster, which sets confirmed_roster/anchor_listing once (PUT refuses if a roster already exists). (2) Next run column shows "in N days" / "N days overdue" (amber); admins get "▶ Run all due" (POST /api/track/run-due, same as the scheduler). (3) Entity detail header adds "⬇ Latest Deep Diagnostic" (PDF of the newest snapshot, hidden if none) and "⇄ Compare against…" (prefills Compare Two side A). (4) Snapshot table gains an Evidence column (Score Evidence chip per snapshot; get_entity_trend now returns confidence). (5) Details panel: "Email when the score moves 5+ points or changes quartile" (tracked_entities.alert_on_change); after a tracked run, if the last two daily snapshots differ by ≥5 or cross a quartile, recipients (report recipients, else the creator) get a same-day alert email with the Trend Report attached. (6) List rows have a checkbox; ticking 2–4 shows a bar with "Compare on one chart" → a view with all selected score lines on one 0–100 chart (one point per day per entity, gaps spanned), a colour legend, and a table (snapshots, first, latest, change, quartile, evidence). NEEDS BROWSER TESTING.

**T1 — Roster flag.** On production the 8 practice entities show "⚠ Confirm locations" in their row and "locations not confirmed" under Needs attention. Click the badge → Details opens scrolled to the amber box. Find locations → flagship + discovered locations with ratings; uncheck one; Save roster → flash, badge replaced by "N locations", the roster appears under Locations in Details; Run now measures those locations. A second attempt to set a roster is refused (400).
**T2 — Next run.** Rows show "in N days" under the date; an overdue one shows "N days overdue" in amber. Admin sees ▶ Run all due; with none due it flashes "No tracked entities are due right now."; with due entities it confirms the list and starts them; non-admins do not see the button.
**T3 — Detail handoffs.** Open an entity: "⬇ Latest Deep Diagnostic" downloads the newest snapshot's PDF (hidden for entities with no PDF); "⇄ Compare against…" opens Compare Two with side A prefilled and searching (service line → Hospital Service Line with system + line).
**T4 — Evidence column.** All Snapshots shows an Evidence chip for snapshots run since Score Evidence shipped; older ones show —. The chip opens the help topic.
**T5 — Change alerts.** Tick the alert box on an entity with recipients, Save (status echoes). Force a snapshot that moves ≥5 points or crosses a quartile (Run now with Refresh from scratch on a Deep Diagnostic of the same entity, or wait for a scheduled run) → email "▲ N — <name> AI Reputation score now S" with the Trend Report attached; a 2-point move sends nothing; a same-day re-run never alerts. Without recipients the alert goes to the creator's address if it is an email.
**T6 — Compare.** Tick two rows → bar "2 selected"; tick a fifth → flash "Up to 4". Compare on one chart → chart with a line per entity in distinct colours, x axis = union of snapshot days, gaps spanned; table lists first/latest/change/quartile/evidence with links back to each entity; ← All Entities returns with the selection kept; Clear empties it.
**R1.** Existing Details fields (display name, cadence, next run, notes, email delivery) save as before; Trend PDF unchanged (evidence column not added to the PDF).

## TRENDS-ATTENTION-EXPLAIN — Needs attention / mixed explained with actions in Details

Commit: "Needs attention" and "mixed" badges are clickable and open the Details panel at a "Needs attention — N items" box that lists each reason for this entity with a plain explanation and the action that clears it: score fell (View trend / Open that snapshot's report), run overdue (Run now), latest snapshot on the wrong rubric (Run now / Open in History), mixed rubric history (dates of the off-rubric snapshots; Open in History / Track as new entity), locations not confirmed (Find locations). NEEDS BROWSER TESTING.

**T1.** Campbell Clinic (score ▼5 + mixed): click "Needs attention" → Details opens scrolled to the box with two items: the fall (with the two scores and date, View trend + report buttons) and Mixed rubric history (listing the hospital-rubric snapshot dates, Open in History + Track as new entity).
**T2.** An entity with no flags shows no box. The badge hover text still lists the reasons.
**T3.** Each button does what it says: View trend opens the entity; Open in History lands on History filtered to the name; Run now starts a run and reports in the status line; Find locations scrolls to the roster box.

## TRENDS-TASK-FEEDBACK — visible refresh after a Details task

Commit: after Save roster or Save in a Trends Details panel, the list re-fetches, the panel stays open, the row scrolls into view and flashes green for ~2.5 s, and the panel status line reads (in green) what changed — e.g. "Roster confirmed — 11 locations will be measured on every snapshot from now on. The "Confirm locations" flag is cleared." NEEDS BROWSER TESTING.

**T1.** Confirm a roster on an unconfirmed practice → the row flashes green, its "⚠ Confirm locations" badge and the "locations not confirmed" attention reason are gone, Locations reads "N fixed locations", the panel shows the green confirmation, and a flash appears at the bottom.
**T2.** Save a display name / cadence change → same behaviour with "Saved — shown as “…”".

## TRENDS-ATTENTION-REVIEWED — mark a Needs-attention reason as reviewed

Commit: new `trend_acks` table (entity + reason + fingerprint + who/when); POST/DELETE `/api/track/entities/{id}/ack[/{reason}]`; the entities list returns `acks`. In Details each attention item has "✓ Reviewed"; reviewed items show grey with "Reviewed by <name> on <date>" and Undo. Reviewed reasons no longer count toward the badge, the "mixed" marker, or the Needs-attention filter. The acknowledgement is tied to the data that raised the flag (snapshot date for a fall / wrong rubric, due date for overdue, the off-rubric snapshot dates for mixed, "roster" for locations), so it reappears when the condition recurs with new data. NEEDS BROWSER TESTING.

**T1.** Campbell Clinic: click Needs attention → in the box click ✓ Reviewed on "Score fell 5" → row flashes, the badge stays only if other reasons remain; the item shows grey "Reviewed by … on …" with Undo. Filter "Needs attention" no longer matches it for that reason.
**T2.** ✓ Reviewed on "Mixed rubric history" → the amber "mixed" marker disappears from the row; Undo brings both back.
**T3.** Recurrence: after the next snapshot, if the score falls again the flag returns (new date); if it does not, nothing shows. Running a Deep Diagnostic of that name as the wrong type adds a new off-rubric date → the mixed flag returns.
**T4.** Reviewing is available to any signed-in user; the recorded name is the reviewer's.

## TRENDS-NETWORK — track a Hospital Network's system score over time

Commit: new tracked entity type `hospital_network` (tracked_entities.facility_type / source_url added). Snapshots are full Hospital Network runs on a fixed facility roster (teaser, full detail and the service-line scorecard off); history comes from network_runs (`get_network_trend`) so existing Network reports for the same system name become the first snapshots. Trends list/detail, Trend Report, change alerts, compare-on-one-chart and the Reviewed flags all work for networks. Entry points: "📈 Track network in Trends" on the Hospital Network completion screen (roster carried over from the run) and in Network rows' History Downloads menu (roster rediscovered); Trends → Track New Entity → Hospital Network type (Find hospitals via AI → confirm roster → Add). Monthly by default; weekly asks for confirmation. NEEDS BROWSER TESTING.

**T1 — From a completion.** Run a Hospital Network report; on the completion screen click "📈 Track network in Trends" → Trends add flow opens with type Hospital Network, the system name and HQ filled, and the roster from the run already listed (no discovery). Uncheck one facility, keep monthly, Add to Trends → the entity appears (label "Hospital Network · N facilities"), its first snapshot starts, and earlier Network runs of the same system name already show as history (sparkline/latest score).
**T2 — From History.** Network row → Downloads ▾ → "📈 Track network in Trends" → add flow prefilled; Find hospitals runs discovery; confirm; Add.
**T3 — From scratch.** Track New Entity → Hospital Network → name only (HQ blank) → Find hospitals via AI → HQ inferred and filled, roster listed by state → Add. Weekly cadence prompts a cost confirmation.
**T4 — Detail + report.** View the network entity: pillar chart uses the hospital pillar labels; snapshot table shows Google rating/review totals across facilities; "⬇ Latest Network report" downloads the network PDF; Compare against… is hidden. Trend Report PDF header reads "Hospital Network · N facilities" (no rolled-up/single-location scope line).
**T5 — Alerts + compare.** Change alert checkbox works for a network; compare-on-one-chart can mix a network with hospitals/practices.
**T6 — Suggestions.** Typing a tracked network's name in Track New Entity name field offers it as a Hospital Network suggestion; picking it selects the type and starts discovery.
**R1.** Hospital/practice/service-line tracking unchanged; a network entity never gets the "Confirm locations" flag; Run all due launches network snapshots too.
