"""Async database engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent.config import Settings, get_settings


def make_engine(settings: Settings | None = None, *, url: str | None = None) -> AsyncEngine:
    """Create an async engine (psycopg 3). ``url`` overrides settings, e.g. in tests."""
    cfg = settings or get_settings()
    return create_async_engine(url or cfg.database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory; objects stay usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
