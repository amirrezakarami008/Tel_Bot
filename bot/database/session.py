"""Async SQLAlchemy engine and session helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import get_settings
from bot.database.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _engine


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session with automatic commit/rollback."""
    factory = async_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(*, max_retries: int = 30, delay_seconds: float = 2.0) -> None:
    """Create tables, retrying until PostgreSQL is ready."""
    engine = get_engine()
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schema ready")
            return
        except Exception as exc:  # noqa: BLE001 — retry until DB is up
            last_error = exc
            logger.warning(
                "DB connection attempt %s/%s failed: %s",
                attempt,
                max_retries,
                exc,
            )
            await asyncio.sleep(delay_seconds)
    raise RuntimeError(
        f"Could not connect to database after {max_retries} attempts"
    ) from last_error
