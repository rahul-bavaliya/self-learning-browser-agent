# Backlog

Ideas parked outside the current phase. Move items into a phase only via the SOP.

- CI workflow (GitHub Actions): ruff + mypy + unit tests; smoke on a Windows runner with Chrome + Edge.
- Alembic baseline migration + provenance ledger tables (Phase 1).
- Integration test that connects to Postgres/Redis from docker compose (`-m integration`).
- `SLBA_TRACE_DIR`: optional Playwright trace recording in `browser_session()`.
- Lock dependencies (uv or pip-tools) once the Phase 1 dependency set settles.
- MiniWoB++ via BrowserGym as the first external benchmark (Phase 2).
- Gate: GET search forms are gated like any submit (safe side, but adds prompts). Consider
  exempting method=GET forms once the eval shows how often it matters.
- Gate gap: a JS button that sends data without a form, under an innocent label, is not
  gated. Consider gating any click that triggers a non-GET request (needs request hooks).
- Scroll inside scrollable containers, not only the window.
- Allow a corporate/upstream proxy for allowlisted external hosts (they connect directly today).
