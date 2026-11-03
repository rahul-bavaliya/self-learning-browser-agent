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

**To confirm in the Codespace:** `SLBA_REQUIRE_ALL_BROWSERS=1 python -m pytest -q` (Edge runs too).

**Next:** week 3 — D6 safety gate, D7 agent loop + `python -m agent run`.
