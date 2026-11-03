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
