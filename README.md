# SLBA — Self-Learning Browser Agent

A Playwright agent for Chrome and Edge that gets better at websites through site memory,
a skill library and weekly LoRA consolidation, with a provenance ledger that traces
every learned item back to its source. Architecture, phases and rules: `docs/` (SOP-SLBA-001).

**Status: Phase 1, week 4 — eval suite ready; baseline run next.** Plan: `docs/phase-1.md`.

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

## Run the agent

```bash
alembic upgrade head
python -m http.server 8001 -d tests/smoke/site -b 127.0.0.1 &     # a local test page
python -m agent run "Preview a large order for Rahul" --start-url http://127.0.0.1:8001/form.html
python -m agent show <run-id>                                      # every step from the ledger
```

The default provider is **NVIDIA NIM** (OpenAI-compatible, `integrate.api.nvidia.com`).
Set `SLBA_LLM_API_KEY` (an `nvapi-...` key from build.nvidia.com) and `SLBA_LLM_MODEL`
(a NIM model with tool calling, e.g. `meta/llama-3.3-70b-instruct`). Later, point
`SLBA_LLM_PROVIDER=vllm` or `ollama` plus `SLBA_LLM_BASE_URL` at a self-hosted model;
the same client is used. `SLBA_LLM_PROVIDER=fake` runs offline with no key.
If a model rejects forced tool calls, set `SLBA_LLM_TOOL_CHOICE=required` (or `auto`).
Catalogs still list retired models, so check before picking one:
`python -m agent.llm.probe` sends each candidate one real agent request and reports
which return a valid action (one API call per model).
Slow providers are handled: timeouts and 429/5xx are retried, the run tolerates
`SLBA_LLM_MAX_CONSECUTIVE_FAILURES` failed LLM calls in a row, and no call can run past
`SLBA_MAX_SECONDS`.
Risky actions (form submits, buy/send/delete, password fields) stop and ask you;
`--non-interactive` denies them instead. Evidence (snapshots, screenshots, confirmation
decisions) is written under `.slba/evidence/<run-id>/`.

## Evaluate (Phase 1 baseline)

```bash
python -m evals run --repeats 3 --out docs/baseline-phase1.md   # 12 tasks x 3 = 36 runs
python -m evals run --repeats 1 --tasks nav-01,form-01          # a quick subset
python -m evals serve                                           # browse the test shop yourself
```

The runner starts the test shop site itself on a random local port, resets it before every
task, and judges each run with the task's checker (final URL, site state or the answer),
never the agent's own claim. Reports go to `.slba/reports/` (Markdown + JSON) with the
Phase 1 exit gates: verified success >= 60%, ledger coverage 100%, no unsafe actions, no
injection escapes. `--pause` (default 3s) and `--max-steps` (default 15) keep free-tier
quotas in check.

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
