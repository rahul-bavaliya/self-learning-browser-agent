# Weekly log

## Week of 2026-11-02 — Phase 0

**Done**
- Repo scaffold per SOP layout; docker compose (Postgres 16 + pgvector, Redis 7).
- Typed settings (secrets as SecretStr), isolated-profile browser launcher, domain allowlist
  enforced at the network layer, plus launch flags that stop browser background traffic.
- pre-commit: ruff, mypy (strict), detect-secrets, .env guard.
- Codespaces devcontainer. Fixed build failure: base image's Yarn apt key no longer verifies
  (NO_PUBKEY 62D54FD4003F6525), which broke the docker-in-docker feature.

**Results (Codespace, 2026-11-03)**
| Check | Result |
|---|---|
| `SLBA_REQUIRE_ALL_BROWSERS=1 pytest -v` | 16 passed, 0 skipped (Chrome + Edge) |
| `docker compose ps` | postgres + redis healthy |
| `pre-commit run --all-files` | all passed |
| pgvector extension (`\dx`) | not yet confirmed |
| SOP Phase 0 exit criteria | not yet checked: SOP not in project |

**Open**
- `pytest` resolves to the image's pipx copy (`/usr/local/py-utils/venvs/pytest`); use
  `python -m pytest` so tests always run on the project interpreter.
- Trace recording (`SLBA_TRACE_DIR`) — in backlog.

**Next**
- Phase 1 defined (docs/phase-1.md, draft v0.1). Week 1: D1 ledger schema + Alembic, D2 ledger repository + retraction.
- Add SOP-SLBA-001 to the project; check Phase 0 exit criteria; plan Phase 1 from it.

## Week of 2026-11-09 — Phase 1, week 1 (D1, D2)

**Done**
- D1: ORM models (`provenance/models.py`) and Alembic migration `0001` for ledger_items,
  ledger_edges, runs, steps, evidence. Upgrade → downgrade → upgrade clean; `alembic check` clean.
- D2: `provenance/ledger.py` — start_run, record_step, record_evidence (SHA-256), finish_run
  (trust update for the run and everything it produced), retract (recursive CTE, flags
  descendants, deletes nothing), orphans (ledger-coverage gate), run_items.
- Trust rules in `provenance/trust.py`: 0.5 start, 0.9 verified success, 0.1 failure.

**Results (cloud workspace, local Postgres 16)**
| Check | Result |
|---|---|
| Unit + integration tests | 32 passed |
| Ledger coverage (`orphans()` empty after a full run) | pass |
| Retract run → all steps and evidence flagged, other runs untouched | pass |

**Confirmed in the Codespace (2026-11-03)**
- `alembic upgrade head` on the compose Postgres: 0001 applied.
- `SLBA_REQUIRE_DB=1 python -m pytest -m integration`: 13 passed.
- Full suite `python -m pytest`: 36 passed (unit, integration, Chrome + Edge smoke).

**Next:** week 2 — D3 LLM interface, D4 observation builder, D5 action schema.

## Phase 1, week 2 (D3, D4, D5) — built 2026-11-03

**Done**
- D3 `agent/llm/`: `LLMClient` Protocol; `AnthropicClient` (forced `act` tool, usage and cost
  per call, key held as SecretStr); `FakeLLM` (scripted, deterministic); `make_llm()` factory.
  One stateless call per step keeps prompts bounded and providers swappable.
- D4 `agent/observe.py` + `safety/untrusted.py`: accessibility snapshot in Playwright "ai" mode
  with element refs, capped by whole lines; URL, title and snapshot wrapped in
  `<untrusted_page_content>`, with any wrapper tag inside page text neutralised.
- D5 `agent/actions.py`: 9 typed actions (click, fill, press, select, scroll, goto, wait,
  answer, finish); strict parsing with model-readable errors; key allowlist; executor returns
  ok / error / denied and never raises on page failures; goto checks the allowlist first.
- `agent/prompts.py`: system prompt with the safety rules; goal outside, page inside the wrapper.

**Safety finding, fixed**
- Chrome's own background services reached the network despite Phase 0's flags. A net log of
  one 8-second session on a blank page showed accounts.google.com, android.clients.google.com,
  clients2.google.com, update.googleapis.com and www.google.com. These bypass Playwright's page
  route. Fix: browser-wide dead proxy; only allowlisted hosts bypass it. After the fix all 57
  attempts failed with ERR_PROXY_CONNECTION_FAILED and none left the machine.

**Results (cloud workspace, Chrome only)**
| Check | Result |
|---|---|
| Full suite | 84 passed, 6 skipped (Edge not installable here) |
| Injection page text stays inside the wrapper (real browser) | pass |
| Off-allowlist goto denied before any request | pass |

**Confirmed in the Codespace (2026-11-03):** 90 passed with Chrome and Edge.

**Next:** week 3 — D6 safety gate, D7 agent loop + `python -m agent run`.

## Phase 1, week 3 (D6, D7) — built 2026-11-03

**Done**
- D6 `safety/gate.py`: reads the target element from the live page; gates form submits
  (from the DOM, not the label), Enter inside a form, password fields, and controls labelled
  buy/pay/send/delete/publish/sign up and similar. Confirmers: `CliConfirmer` (asks y/N),
  `DenyAll` (non-interactive), `LocalhostAutoConfirm` (eval tasks, localhost only). Every gate
  decision is stored as `confirmation` evidence.
- `safety/captcha.py` stops the run as `blocked`; `safety/redact.py` masks secret-looking
  keys and every SecretStr value in logs.
- D7 `agent/loop.py`: observe → decide → gate → act → record, committed per step; budgets
  for steps, time and cost; checker alone decides `verified`. Migration 0002 adds
  `runs.end_reason` and the `confirmation` evidence kind (downgrade refuses to drop
  confirmation evidence).
- CLI: `python -m agent run "<goal>" --start-url URL [--non-interactive]`, `python -m agent show RUN_ID`.

**Safety findings, fixed**
- Typed passwords appeared in the step log line and the confirmation evidence. Fixed: the
  step summary masks text for password fields everywhere.
- Playwright's ai-mode accessibility snapshot includes password field *values* in plain text,
  so the next observation would have saved the password as evidence and sent it to the LLM.
  Fixed: password values are masked inside `observe()`. A test asserts the password appears
  nowhere: ledger, evidence files, logs or any later request to the model.

**Results (cloud workspace, Chrome, local Postgres)**
| Check | Result |
|---|---|
| Full suite | 111 passed, 6 skipped (Edge) |
| End-to-end loop tests (FakeLLM, real Chrome + Postgres) | 12 passed |
| Happy path verified, ledger coverage 100%, orphans none | pass |
| Form submit denied without confirmation, form not submitted | pass |
| Injected goto to a blocked host denied, never requested | pass |
| CAPTCHA page blocked before any LLM call | pass |

**Not yet measured:** a live run with a real model (needs `SLBA_LLM_API_KEY`).

**Next:** week 4 — D8 local task site + 12 tasks with checkers, D9 eval runner + baseline.

## LLM provider switch — 2026-11-03

- First live run with the Anthropic API failed with HTTP 400 "credit balance is too low"
  (account billing, not code). Decision: remove the Anthropic client and use NVIDIA NIM.
- New `agent/llm/openai_compat.py`: one OpenAI-compatible chat-completions client (plain httpx)
  with a single `act` function tool and named `tool_choice`; falls back to a JSON action in
  `content` when a server's tool parser misses the call; retries 429/5xx. Default provider
  `nim`; the same client serves `vllm` and `ollama` later via `SLBA_LLM_BASE_URL`, matching
  the plan to move to self-hosted Qwen/Llama.
- Removed `agent/llm/anthropic_client.py` and the `anthropic` dependency.
- Run summary now says why an LLM call failed (`end_reason` carries the provider error).
- Not yet measured: first live NIM run.
- First NIM attempts: `.env` base URL included `/chat/completions` (client now accepts both);
  `meta/llama-3.3-70b-instruct` returned HTTP 410 (retired 2026-08-26, still listed in /models).
  Added `python -m agent.llm.probe` to test candidate models with the real request shape.

## Timeout handling — 2026-11-03

Trigger: `openai/gpt-oss-20b` on NIM never answered; one probe waited 183s (3 x 60s).
- Client: typed errors. `LLMTimeoutError` (every attempt timed out) and `LLMTransientError`
  (429 / 5xx / network after retries) vs permanent `LLMError` (400/401/404/410, not retried).
  Connect timeout 10s, read timeout `SLBA_LLM_TIMEOUT_S`; honours numeric `Retry-After`
  (capped at 30s).
- Loop: a transient failure is recorded as an error step and the step is retried; after
  `SLBA_LLM_MAX_CONSECUTIVE_FAILURES` (3) in a row the run aborts with "LLM unavailable: ...".
  Permanent errors abort immediately. Provider failures are kept out of the model's history.
- Each LLM call is bounded by the run's remaining time (`SLBA_MAX_SECONDS`); snapshot and
  screenshot have 10s timeouts, and a page that won't produce a snapshot aborts the run.
- Probe: 20s per model, no retries, prints which model it is waiting on.

## First live run — 2026-11-03

Model `nvidia/nemotron-3.5-bolt-e4b` on NVIDIA NIM (free tier), Chrome, Codespace.
Goal "Preview a large order for Rahul" on `tests/smoke/site/form.html`.

| Metric | Result |
|---|---|
| Outcome | success (claimed); manually confirmed: final snapshot shows `Rahul/Large` |
| Steps | 7 (ideal 4): 1 premature Preview click, 2 steps with no tool call |
| Wall time | 26 s |
| Tokens | 7,553 in / 325 out (~1.1k per step) |
| Ledger | 22 items (run + 7 steps + 14 evidence), trust 0.5 (unverified) |

Findings and fixes:
- Steps 4–5: model replied in plain text instead of calling `act` once it thought it was
  done. Fixed the prompt ("never reply in plain text; call finish") and the step error now
  records what the model said, so misses are diagnosable.
- Step 1 clicked Preview before choosing the size: a planning slip, the kind of lesson site
  memory (Phase 2) should capture for this form.

Getting here: Anthropic credit balance too low → switched to NIM; base URL with
`/chat/completions` doubled; retired model (HTTP 410); wrong key (Anthropic key sent to NIM);
gpt-oss-20b timeouts; 4 Nemotron models not enabled for the account (404); Postgres password
mismatch after Codespace restart; test server not running after restart.

## Second live run and loop feedback — 2026-11-03

- Re-run after the prompt fix: 0 errors (no more plain-text replies), but 15 steps: the model
  clicked Preview 11 times before choosing Large. It only ever saw "click -> ok".
- Fix: after each action the next snapshot is compared with the one before it; history lines
  now say "no visible change on the page" and, from 3 repeats, "same action N times in a row:
  try something different". `SLBA_MAX_REPEATS` (5) identical no-op actions abort the run as
  "stuck", saving free-tier quota. Real progress is unaffected (test).

**Third live run (after loop feedback), 2026-11-03:** same task and model.

| Run | Steps | Errors | Tokens in/out | Time |
|---|---|---|---|---|
| 1st live | 7 | 2 | 7,553 / 325 | 26 s |
| 2nd (before loop feedback) | 15 | 0 | 17,361 / 620 | 34 s |
| 3rd (after loop feedback) | 5 | 0 | 5,534 / 217 | 12 s |

Ideal is 4 steps; the extra one is the premature Preview click, now recovered from
immediately. Still unverified (no checker) until week 4.

**Next:** Phase 1 week 4: D8 local task site + 12 tasks with checkers, D9 eval runner +
baseline (target >= 60% verified success over 3 repeats).
