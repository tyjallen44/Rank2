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
