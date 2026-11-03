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
