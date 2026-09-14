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
