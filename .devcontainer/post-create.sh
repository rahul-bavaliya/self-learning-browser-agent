#!/usr/bin/env bash
# Runs once when the Codespace is created.
set -euo pipefail

pip install --upgrade pip
pip install -e ".[dev]"

# Branded browsers + their system libraries (needs sudo; available in Codespaces).
playwright install --with-deps chrome msedge

# Compose needs these; Codespaces secrets arrive as env vars, else use throwaway local values.
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}/" .env
  sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${REDIS_PASSWORD:-$(openssl rand -hex 16)}/" .env
fi

pre-commit install
echo "Ready: run 'SLBA_REQUIRE_ALL_BROWSERS=1 pytest -v'"
