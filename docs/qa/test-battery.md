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
- Stages 2–3 will add entries `DD-CONSOLIDATION-STAGE2/3`; do not pre-test that behavior here.
