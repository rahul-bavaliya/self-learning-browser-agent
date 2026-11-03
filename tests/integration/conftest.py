"""Integration fixtures: a throwaway database migrated with Alembic.

Needs the compose Postgres (``docker compose up -d``). If it can't be reached the
tests skip, unless SLBA_REQUIRE_DB=1 (set in CI so a missing DB fails loudly).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config import Settings
from provenance.db import make_engine, make_sessionmaker

REPO = Path(__file__).resolve().parents[2]
TABLES = "steps, evidence, runs, ledger_edges, ledger_items"


def _conninfo(s: Settings, db: str) -> str:
    return psycopg.conninfo.make_conninfo(
        host=s.postgres_host,
        port=s.postgres_port,
        user=s.postgres_user,
        password=s.postgres_password.get_secret_value() or None,
        dbname=db,
        connect_timeout=3,
    )


def _sa_url(s: Settings, db: str) -> str:
    pw = s.postgres_password.get_secret_value()
    auth = f"{s.postgres_user}:{pw}" if pw else s.postgres_user
    return f"postgresql+psycopg://{auth}@{s.postgres_host}:{s.postgres_port}/{db}"


@pytest.fixture(scope="session")
def migrated_db_url() -> Iterator[str]:
    """Create ``slba_test_<random>``, run ``alembic upgrade head``, drop it afterwards."""
    s = Settings()
    admin_db = s.postgres_db
    name = f"slba_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(_conninfo(s, admin_db), autocommit=True)
    except psycopg.OperationalError as exc:
        if os.environ.get("SLBA_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run docker compose up -d")
    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = _sa_url(s, name)
    try:
        cfg = Config(str(REPO / "alembic.ini"))
        cfg.attributes["url"] = url
        cfg.attributes["skip_logging"] = True
        command.upgrade(cfg, "head")
        yield url
    finally:
        with psycopg.connect(_conninfo(s, admin_db), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
async def session(migrated_db_url: str) -> AsyncIterator[AsyncSession]:
    """A session on the migrated test DB; tables are emptied after each test."""
    engine = make_engine(url=migrated_db_url)
    maker = make_sessionmaker(engine)
    async with maker() as sess:
        yield sess
        await sess.rollback()
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    await engine.dispose()
