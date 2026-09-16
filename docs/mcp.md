# The Pulse MCP endpoint

One Model Context Protocol endpoint at `POST /mcp`, so an AI assistant can run and
read Pulse reports directly instead of a person driving the web UI. It is additive
and off by default.

---

## 1. What this adds

- **`POST /mcp`** — a single Streamable HTTP MCP endpoint, mounted on the existing
  FastAPI app, so it shares this service's database, keys and renderers.
- **Nine tools**, in the order a rep needs them:

  | Tool | What it does | Spends |
  |---|---|---|
  | `pulse_find_entity` | Google listings matching a name, to confirm the right organization | one Places search |
  | `pulse_content_check` | Website crawl, JSON-LD, `llms.txt`, `robots.txt`, Wikidata, Wikipedia | HTTP crawl, no model call |
  | `pulse_run_report` | A full hospital / practice / FQHC report, start to finish | a full run |
  | `pulse_get_report` | Score, pillars and PDF links for a finished single-organization run | nothing |
  | `pulse_compare` | Head-to-head on two organizations | a full run |
  | `pulse_network_report` | A whole hospital network | a full run |
  | `pulse_history` | The caller's own runs, market reports excluded (section 6) | nothing |
  | `pulse_trend` | Score movement across the caller's own snapshots | nothing |
  | `pulse_content_draft` | Remediation copy for a run's saved content findings | one or more model calls |

- **Three new modules** under `perception/`: `mcp_server.py` (tools and mount),
  `mcp_auth.py` (token verification), `mcp_usage.py` (the daily cap).
- **One new table**, `mcp_daily_usage`, created on first use like the others.
- **This document.**

With `PULSE_MCP_ENABLED` unset, `server.py` imports nothing new, registers no route,
and the app is byte-for-byte the app you deploy today. `tests/test_mcp_mount.py`
asserts both halves of that.

---

## 2. Turn it on

The new variables go into the **existing comma-joined string** at `deploy.sh:158`.
`--set-env-vars` REPLACES the whole list, so a separate `--update-env-vars` call
would be dropped by the next deploy, and so would anything you leave out of the
line below. This PR does not edit `deploy.sh`; the line is here for you to paste.

Your line today, verbatim:

```bash
  --set-env-vars="REPORTS_DIR=/data/reports,APP_URL=${APP_URL},RESEND_FROM_DOMAIN=${RESEND_FROM_DOMAIN},GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}" \
```

The same line with the four new variables appended, and nothing else changed:

```bash
  --set-env-vars="REPORTS_DIR=/data/reports,APP_URL=${APP_URL},RESEND_FROM_DOMAIN=${RESEND_FROM_DOMAIN},GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},PULSE_MCP_ENABLED=1,PULSE_MCP_OAUTH_ISSUER=<RLDatix issuer>,PULSE_MCP_OAUTH_AUDIENCE=<the resource id>,PULSE_MCP_OAUTH_JWKS_URL=<RLDatix JWKS url>" \
```

`REPORTS_DIR=/data/reports` is the one that must not be dropped: it points at the
mounted GCS volume, and without it every generated PDF is written inside the
container and every report link 404s after the next restart.

**`ACCESS_PASSWORD`, `ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY`, `GOOGLE_CLIENT_SECRET`,
`RESEND_API_KEY` and `DATABASE_URL` are not environment variables** — they are
`--set-secrets` entries on `deploy.sh:159`, a separate flag. Nothing here moves them,
and moving one into `--set-env-vars` would put a secret in the service description.

The two packages are installed unconditionally in the `Dockerfile`, so flipping the
flag does not need an image rebuild.

---

## 3. Environment contract

| Variable | Default | Meaning |
|---|---|---|
| `PULSE_MCP_ENABLED` | `0` | `1` mounts `/mcp` and the two well-known routes. Anything else: nothing is imported or registered. |
| `PULSE_MCP_OAUTH_ISSUER` | unset | RLDatix authorization server issuer. All three OAuth variables must be set or the OAuth path is off entirely. |
| `PULSE_MCP_OAUTH_AUDIENCE` | unset | The `aud` our tokens carry, and the `resource` in the RFC 9728 document. |
| `PULSE_MCP_OAUTH_JWKS_URL` | unset | JWKS endpoint. `http`/`https` only, checked when the verifier is constructed — which `mount_mcp` does at import, so a malformed URL is a revision that does not start. |
| `PULSE_MCP_DAILY_CAP` | `20` | Metered tool calls per email per UTC day. |
| `PULSE_MCP_RUN_TIMEOUT_SECONDS` | `900` | How long a run tool blocks before returning the "still running" message. Set it below the MCP client's own tool timeout. |
| `PULSE_MCP_MAX_WORKERS` | `2` | Size of the MCP's own thread pool. It never uses `server._pool`. |
| `PULSE_MCP_PDF_LINK_TTL_SECONDS` | `600` | Lifetime of a minted PDF download token. Ten minutes, because that token is a Pulse session credential — see section 8. |
| `PULSE_MCP_ROLE_MAP` | unset | JSON object merged over the default role map. An entry whose target is `admin` or `integrations_admin` is refused and logged. Its value is JSON, and `--set-env-vars` splits its whole argument on commas, so a plain comma-joined line truncates the object at the first entry: use gcloud's alternate delimiter instead, for example `--set-env-vars=^;^PULSE_MCP_ROLE_MAP={"marketing":"salesteam","support":"salesteam"};APP_URL=${APP_URL}`. |
| `APP_URL` | existing | Reused, not added. Already set at `deploy.sh:158`. |

With `PULSE_MCP_ENABLED=1` and no OAuth variables, only Pulse's own session tokens
work — a valid configuration for your team alone.

---

## 4. Who can call it

Two kinds of bearer token, presented in the `Authorization` header. The endpoint
never accepts `?token=`: your REST routes do, deliberately, so EventSource works,
but a token in a URL lands in every access log and `Referer` between the client and
Cloud Run, and this is the credential a connector presents on every call.

**(a) RLDatix OAuth tokens.** RS256 JWTs from RLDatix's authorization server,
verified against its public JWKS with the configured issuer and audience. The
verifier allowlists RS256 twice (before the key lookup and again in `jwt.decode`),
requires `exp`/`iat`/`iss`/`aud`/`sub`, refuses RSA keys under 2048 bits, bounds the
JWKS body, and refetches on an unknown `kid` at most once every five minutes so a
forged-kid flood cannot turn Pulse into a fetch amplifier. A failed fetch empties the
key cache rather than keeping keys it can no longer confirm.

The token's `email` is looked up with `perception.auth.get_user_by_email`; the row
must exist and be active. The token proves who is asking — the Pulse `users` row is
what grants access — so inviting and deactivating stay entirely yours.

Roles map onto your groups:

| RLDatix role | Pulse role |
|---|---|
| `ae`, `bdr`, `bdr_lead`, `sales_lead` | `salesteam` |
| `cs`, `cs_lead` | `customersuccess` |
| `cro`, `executive`, `admin` | `rldatix` |

Two rules in that table are deliberate. **Our `admin` maps to `rldatix`, never to
your `admin`** — Pulse admin can delete runs, read every user's history and manage
users, and no rep's connector should hold that. **Anything not listed is refused**,
`marketing` and `support` included: a role nobody has thought about must not land
silently in a group. Adding one is a deploy-time change through `PULSE_MCP_ROLE_MAP`,
not a code change — and that variable may not name `admin` or `integrations_admin` as
a target, so the first rule survives a typo on a deploy line as well as a code review.

**That mapped group is the access gate and nothing else.** What a run is stamped with
(`analysis_runs.user_role`) and what a minted download token carries is the caller's
own role on your `users` row — `account_role` in the code. The two questions are
different: "may this RLDatix role reach Pulse" is ours to answer, "what does this
person hold in Pulse" is yours, and only the second one grants anything. Concretely,
a Google-approved account created at your OAuth callback has role `user`; using the
mapped group would have handed that person a working `salesteam` credential and, with
it, every salesteam run's history, `ran_by` emails and PDFs. Nothing the MCP mints or
writes can now exceed what that person's own Pulse login already gives them.

**(b) Your own session tokens.** `_verify_token_full`, unchanged, so your team can
use the MCP with the token their browser already holds, and their role passes
through unmapped — that IS your identity rather than a mapping of ours. The `users`
row is checked on this path too: a session token lives thirty days, so without it a
person you deactivated would keep spending the shared keys for up to a month.

**Tokens minted from the five shared team passwords are refused.** They carry no
email, and an identity-less caller cannot be scoped to its own runs or metered by the
cap — the two things this endpoint is built around. Your Google SSO and native
email+password tokens carry `email`, so you and your team are unaffected. There is no
environment escape hatch for this.

Refusals come back as the status the reason deserves:

| Refusal | Status | Headers |
|---|---|---|
| No token, expired, wrong issuer or audience, bad signature, forbidden algorithm | `401` | `WWW-Authenticate: Bearer realm="pulse"`, plus `resource_metadata` when OAuth is configured |
| No Pulse account for that email; the Pulse account is deactivated | `403` | none |

The split matters in practice: a connector answers `401` plus a challenge by re-running
the OAuth dance, which cannot conjure a Pulse account. A rep in that state gets the
plain message once instead of an endless sign-in loop.

---

## 5. Cost controls

`mcp_daily_usage(email, day, runs)`, default 20 runs per email per UTC day, settable
with `PULSE_MCP_DAILY_CAP`.

- **Counts 1:** `pulse_run_report`, `pulse_compare`, `pulse_network_report`,
  `pulse_content_draft`.
- **Counts 0:** `pulse_find_entity`, `pulse_content_check`, `pulse_get_report`,
  `pulse_history`, `pulse_trend`. Those either make no paid call or make one bounded
  Places lookup. `pulse_content_check` is free on purpose: it is the tool the model is
  told to reach for first, and metering it would push callers straight to the
  expensive one.
- **Reserved before the run starts**, in a single `INSERT ... ON CONFLICT DO UPDATE
  ... WHERE runs < ? RETURNING runs`, so two concurrent calls cannot both read "19
  used" and both slip under a cap of 20. No row comes back when the `WHERE`
  suppresses the update, which is how the refusal is detected without a second read.
- **Never refunded.** A run that fails has usually already spent Anthropic and Places
  budget; the counter measures spend, not success.
- The day boundary is `date.today()` on the server, i.e. UTC on Cloud Run — the same
  boundary `count_public_requests_today` already uses.
- **Roster discovery is checked against the cap before it runs.**
  `pulse_network_report` with no `facilities` calls `discover_hospitals_by_name`,
  which is a real Claude call plus a real Gemini call — so the caller's usage is read
  before that, not after. The reservation itself stays where it was, after the roster
  is known, so an oversized-roster refusal still costs nothing.
- A refused argument never costs a run. Everything the tools validate — the state
  abbreviation, the organization length, the specialty and `hq_location` ceilings, the
  URL guard, the facility shape — is checked before `reserve_run`.

The refusal names the caller's own limit and nobody else's usage.

### What may cross to Pulse

The tools take an organization name, a city, a state, a ZIP, a specialty, an entity
kind, a report type, a run id, a facility roster and a front-door URL. There is no
`notes`, `context` or `account_id` parameter and there will not be one;
`tests/test_mcp_tools.py` fails if anybody adds one. Two of those needed more than a
name check, because a name check only constrains the key:

- **`facilities` entries are reduced to `{name, city, state}`.** They are dicts with
  no schema of their own, so without this they are the one place on the surface a
  model could put a contact, a note or deal context and have it travel to Places and
  Anthropic. An entry carrying any other key is refused rather than quietly trimmed.
- **`website_url` and `source_url` must be public `http(s)` front doors.** Every
  address the hostname resolves to is checked; loopback, private, link-local,
  reserved, multicast and unspecified are refused, as is a query string or a fragment.
  Those fetches happen inside your Cloud Run service on behalf of a model that can be
  steered by the pages it reads: unguarded, the parameter is an internal
  host-and-port scanner (the findings echo `fetch_status`, `pages_crawled` and the
  origin back to the caller) and a query string on it is a way to carry arbitrary
  text out of the model's context through your server.

`specialty` is capped at 80 characters and `hq_location` at 120 — both name a thing,
not a description.

---

## 6. Ownership

Every read tool returns only rows whose `ran_by` matches the caller, case-
insensitively: `pulse_get_report`, `pulse_history`, `pulse_trend` and
`pulse_content_draft`. There is no "my team's runs" mode.

Two functions are deliberately NOT used:

- **`query_history(role)`** filters on `user_role`, and our role mapping puts every
  AE, BDR and sales lead into the single string `salesteam` — a role-wide read would
  show one rep every other rep's runs.
- **`get_entity_trend(entity_name)`** has no per-user filter at all.

A run started over MCP is still visible in the web UI to that rep's whole role group,
because `set_run_role` writes `user_role` as well as `ran_by` — and the role it writes
is the caller's own Pulse `users.role`, so the run lands in exactly the group that
person's browser session already reads. That is your existing model, unchanged.
**The MCP is simply stricter than the web UI, never looser.**

"Someone else's run" and "no such run" return the identical message, so a caller
cannot probe for which run ids exist.

**Market reports are out of scope for these tools, deliberately.** `_RUN_SQL` joins
`ranked_providers` on `rank = 1`, which for a Deep Diagnostic is the requested
organization and for a market report is the top-ranked *competitor* — and
`analyzer.py` writes `entity_name = NULL` for those runs. So `pulse_get_report`
answers a market run id with a message saying what it is rather than rendering a
rival's score under the header "Unknown", and `pulse_history` filters on
`individual_report = TRUE` so the model is never handed an id the other tool cannot
read correctly. Market reports stay in Pulse History, where they render as what they
are.

---

## 7. What it does not change

- `deploy.sh`: untouched.
- `Dockerfile`: two packages appended to the existing `pip install` line. `CMD`,
  `WORKDIR`, `ENV`, the Playwright line and `COPY` are untouched.
- `pyproject.toml`: one new optional-dependency group. `requires-python`,
  `dependencies` and `[tool.pytest.ini_options]` are untouched, so `pip install -e .`,
  `pip install -e ".[dev]"` and a bare `pytest` behave exactly as before.
- No existing route, job function or `perception/` module is modified.
  **`perception/db.py` in particular is not touched** — the new SQL lives in the new
  modules and goes through `get_connection()`.
- `server.py`: one gated block, placed next to the `/assets` route because it has to
  precede the SPA catch-all for the same reason that one does.
- No schema change to an existing table; one new table, created on first use.
- **The event loop.** `authenticate()` does blocking I/O — a `psycopg` round trip on
  every call and, on a JWKS refetch, a `urllib` fetch with a five-second timeout held
  under a lock. It runs on a worker thread (`anyio.to_thread.run_sync`), not inline,
  so an MCP request cannot stall the one loop that also serves every browser user.

The report tools do not re-implement anything. They build a job record exactly the
way `POST /api/analyze` does and call `_job_run_single` / `_job_run_practice` /
`_job_run_fqhc` / `_job_run_comparison` / `_job_network_analyze`. So they inherit
`_backfill_teaser_pdf`, `set_run_role`, `_run_confidence`,
`_finalize_hospital_combined`, `_finalize_practice_combined`, `_notify_run_complete`
and every cache rule — and they keep inheriting them when you change those functions.
`force_rerun` and `override_today_lock` are `False` unconditionally: the same-day and
90-day caches are the main brake on repeat spend, and no MCP caller gets to bypass
them.

MCP jobs land in `_jobs` tagged `source="mcp"`, so they appear in Admin -> Operations
and in a user's own `/api/jobs/mine`. Finished ones older than six hours are swept at
the start of each MCP run; entries this module did not create are never touched. MCP
runs use their own two-thread pool, never `server._pool`, so a burst cannot make the
web UI's queue slower.

---

## 8. PDF links

**Read this paragraph before the rest of the section.** The token in a download link
is **not a PDF-scoped capability — it is a Pulse session token**. `_create_token` is
the same function your login uses, and `require_auth` accepts its output on every REST
route, in the header or as `?token=`. Anyone holding that link for its lifetime can do
what its role can do, not just fetch the one PDF. There is no run-scoped alternative
in the codebase today, so the mitigations are the two below, and they are why the
default TTL is ten minutes rather than an hour.

1. **The role is the caller's own Pulse `users.role`, never the mapped RLDatix group.**
   `download_pdf` resolves a run through `query_history(role)`, which filters on
   `user_role`, and the run was stamped with the same value — so the two agree and the
   link grants no more than that person's own Pulse login already does. Before this
   was pinned, an account whose Pulse role is `user` got a `salesteam` credential
   printed in plain text into a chat transcript.
2. **`exp` is overridden to `PULSE_MCP_PDF_LINK_TTL_SECONDS`** (default 600).
   `_create_token` builds `{"role": ..., "exp": ..., **extra}`, so an `exp` in `extra`
   wins and the link does not inherit the thirty-day session default. A link leaked
   from a transcript is ten minutes of that person's access, not a month of it.

`pulse_get_report`, `pulse_compare` and `pulse_network_report` each mint one.

Worth knowing, though it is your existing behaviour and not something this PR
introduces: `_signing_key()` derives from the full `ACCESS_PASSWORD` set, so rotating
any shared password invalidates outstanding links early. Also existing behaviour:
`/api/network/{run_id}/pdf` and its two siblings authorize with `require_auth` alone
and do not scope on `user_role` at all, so any valid session token can fetch any
network PDF by id. The MCP does not widen that and does not rely on it.

---

## 9. Running the tests

```bash
python3.11 -m venv .venv-mcp
.venv-mcp/bin/pip install --upgrade pip
.venv-mcp/bin/pip install -e ".[dev]"
.venv-mcp/bin/pip install "mcp>=2.2,<3" "pyjwt[crypto]>=2.9"
.venv-mcp/bin/python -m pytest tests/test_mcp_auth.py tests/test_mcp_usage.py \
    tests/test_mcp_tools.py tests/test_mcp_mount.py -v
```

Python 3.11 because the `mcp` SDK needs 3.10 or newer while `pyproject.toml` says
`>=3.9`; the container is `python:3.11-slim`, so the image is fine and the floor is
left alone rather than raised on you.

No test opens Postgres, calls Anthropic, launches Chromium or reaches the public
internet. `perception.db.get_connection` is the single seam and it is monkeypatched
everywhere. `tests/test_mcp_auth.py` is the exception to "no network" only in the
loopback sense: it serves a JWKS from `127.0.0.1` on an ephemeral port so the
verifier's real fetch, cache and refetch-cooldown paths run rather than a mock.

`[tool.pytest.ini_options] testpaths = ["dashboard/tests"]` is unchanged, so a bare
`pytest` still runs only the Playwright dashboard suite. Naming the files explicitly
matches the convention in your own `tests/` docstrings.

---

## 10. Known limits

1. **A run blocks for its whole duration and reports no progress.** `_jobs` is a
   process-local dict on a service with `--max-instances=10`, so a `job_id` handed to
   a non-browser client is worthless the moment a follow-up lands elsewhere, and
   `--session-affinity` is a browser-cookie mechanism an MCP client does not carry.
   Cloud Run's `--timeout=3600` makes a blocking call fine. If the tool's own timeout
   (default 15 minutes) fires first, `asyncio.wait_for` cancels the wait and not the
   thread: the run keeps going, still writes its `analysis_runs` row, and the caller
   is told to pick it up with `pulse_history` then `pulse_get_report`. The honest fix
   is persisting ad-hoc jobs in Postgres the way the Events pipeline already does.
2. **`pulse_content_draft` saves drafts but does not regenerate Report 2**, which
   needs a `content_analysis_runs` row that only the web flow creates. The tool says
   so in its own output.
3. **Every run still bills the one shared `ANTHROPIC_API_KEY` and
   `GOOGLE_PLACES_API_KEY`.** The cap bounds volume per caller; attribution per caller
   is a separate conversation and is not in this PR — though `mcp_daily_usage` is the
   table that would carry it.
4. **PDF links carry a Pulse session token in the URL** — see section 8, which is the
   paragraph to read before Monday. They are short-lived (ten minutes) and bounded by
   the caller's own Pulse role, and the MCP endpoint itself never accepts a query
   token. Streaming PDF bytes through a tool result would be worse for both cost and
   context; a genuinely run-scoped download token would be better than either, and
   would be a change to your REST routes rather than to this module.
5. **A failed run tells the caller it failed and nothing more.** The exception text
   goes to the container log, because your job functions record raw exception strings
   and those routinely carry connection strings, absolute paths and upstream API
   bodies — and everything a tool returns lands in a chat transcript.

One more, on the score: it is not in `job["result"]` for hospital, practice or FQHC
runs, so `pulse_get_report` reads it from `ranked_providers` (`ai_visibility_score`
and `tier_scores` on the rank-1 row) joined to `analysis_runs`, and cross-checks it
against `entity_scores`. If those two ever disagree the tool prints both rather than
picking one.
