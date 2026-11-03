# Backlog

Ideas parked outside the current phase. Move items into a phase only via the SOP.

- CI workflow (GitHub Actions): ruff + mypy + unit tests; smoke on a Windows runner with Chrome + Edge.
- Alembic baseline migration + provenance ledger tables (Phase 1).
- Integration test that connects to Postgres/Redis from docker compose (`-m integration`).
- `SLBA_TRACE_DIR`: optional Playwright trace recording in `browser_session()`.
- Lock dependencies (uv or pip-tools) once the Phase 1 dependency set settles.
- MiniWoB++ via BrowserGym as the first external benchmark (Phase 2).
