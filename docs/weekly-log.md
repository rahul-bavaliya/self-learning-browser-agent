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

**To confirm in the Codespace**
- `alembic upgrade head` against the compose Postgres, then `SLBA_REQUIRE_DB=1 python -m pytest -m integration`.

**Next:** week 2 — D3 LLM interface, D4 observation builder, D5 action schema.
