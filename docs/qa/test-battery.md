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
