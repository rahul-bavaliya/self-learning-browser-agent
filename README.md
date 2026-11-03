# SLBA — Self-Learning Browser Agent

A Playwright agent for Chrome and Edge that gets better at websites through site memory,
a skill library and weekly LoRA consolidation, with a provenance ledger that traces
every learned item back to its source. Architecture, phases and rules: `docs/` (SOP-SLBA-001).

**Status: Phase 1, week 2 — LLM interface, observation, actions.** Plan: `docs/phase-1.md`.

## Layout

```
agent/          agent loop, browser launcher, settings       (Phase 0: config.py, browser.py)
memory/         per-domain site memory
skills/         parameterized skill library
provenance/     ledger: origin, evidence, trust, dependency edges
safety/         allowlist, untrusted-content wrapping, confirm gates   (Phase 0: allowlist.py)
consolidation/  LoRA fine-tuning on verified trajectories
evals/          BrowserGym / WebArena harness + SOP metrics
api/            FastAPI service
dashboard/      UI (later)
infra/          docker, postgres init
tests/          unit/ and smoke/
docs/           SOP, weekly log, backlog
```

## Setup (Windows, macOS or Linux)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
playwright install chrome msedge     # branded browsers, not bundled Chromium
cp .env.example .env                 # then set POSTGRES_PASSWORD and REDIS_PASSWORD
docker compose up -d
docker compose ps                    # both services should be "healthy"
pre-commit install
```

Already have Chrome and Edge installed system-wide? Playwright uses those; skip the
install step (on Linux it may ask for sudo to replace them — say no if you'd rather not).

## Database

```bash
alembic upgrade head          # create / update the schema (Postgres from docker compose)
alembic downgrade base        # drop it again
alembic check                 # models and migrations agree
```

## Checks

```bash
python -m pytest tests/unit           # fast, no browser or DB
python -m pytest -m integration      # ledger against a throwaway Postgres DB
SLBA_REQUIRE_ALL_BROWSERS=1 python -m pytest -m smoke -v   # Chrome + Edge, must not skip
pre-commit run --all-files
```

Phase 0 exit criteria: `docker compose ps` healthy, `CREATE EXTENSION vector` present
(`docker exec slba-postgres psql -U slba -c "\dx"`), 4/4 smoke tests pass with no skips,
pre-commit clean.

## Safety rules enforced from day one

- Each browser session uses a fresh temporary profile, deleted on exit. No personal profile.
- Every network request is checked against `SLBA_DOMAIN_ALLOWLIST` (default: localhost only),
  twice: a page-level route guard, and a browser-wide dead proxy that only allowlisted hosts
  bypass, which also blocks Chrome/Edge background services (sign-in, updates).
- Page content reaches the model only inside `<untrusted_page_content>`, which the page
  cannot close or reopen.
- Secrets come from environment variables as `SecretStr`; `.env` is git-ignored and
  blocked by a pre-commit hook; `detect-secrets` scans every commit.
