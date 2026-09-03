"""Async SQLAlchemy engine and session helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import get_settings
from bot.database.models import Base, RequiredChannel, Webinar, WebinarLinkClaim

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
                await conn.run_sync(_migrate_schema)
            await _backfill_webinar_data()
            await _seed_channels_from_env()
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


def _migrate_schema(connection) -> None:
    """Add webinar_id to existing claim rows and new webinar columns."""
    inspector = inspect(connection)
    tables = inspector.get_table_names()

    if "webinars" in tables:
        webinar_cols = {col["name"] for col in inspector.get_columns("webinars")}
        alters = {
            "has_certificate": "ALTER TABLE webinars ADD COLUMN has_certificate BOOLEAN NOT NULL DEFAULT false",
            "certificate_price": "ALTER TABLE webinars ADD COLUMN certificate_price VARCHAR(120)",
            "link_send_at": "ALTER TABLE webinars ADD COLUMN link_send_at TIMESTAMPTZ",
            "link_auto_sent": "ALTER TABLE webinars ADD COLUMN link_auto_sent BOOLEAN NOT NULL DEFAULT false",
        }
        for col, sql in alters.items():
            if col not in webinar_cols:
                connection.execute(text(sql))

    if "webinar_link_claims" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("webinar_link_claims")}
    if "webinar_id" not in columns:
        connection.execute(text("ALTER TABLE webinar_link_claims ADD COLUMN webinar_id INTEGER"))
        inspector = inspect(connection)

    fks = inspector.get_foreign_keys("webinar_link_claims")
    has_webinar_fk = any("webinar_id" in (fk.get("constrained_columns") or []) for fk in fks)
    if not has_webinar_fk and "webinars" in inspector.get_table_names():
        connection.execute(
            text(
                "ALTER TABLE webinar_link_claims "
                "ADD CONSTRAINT fk_webinar_claims_webinar "
                "FOREIGN KEY (webinar_id) REFERENCES webinars(id) ON DELETE CASCADE"
            )
        )

    uq_names = {uq["name"] for uq in inspector.get_unique_constraints("webinar_link_claims")}
    for uq in inspector.get_unique_constraints("webinar_link_claims"):
        cols = list(uq.get("column_names") or [])
        if uq["name"] == "uq_webinar_claim_user" or cols == ["user_id"]:
            connection.execute(
                text(f'ALTER TABLE webinar_link_claims DROP CONSTRAINT "{uq["name"]}"')
            )
            inspector = inspect(connection)
            uq_names = {item["name"] for item in inspector.get_unique_constraints("webinar_link_claims")}
            break

    idx_names = {idx["name"] for idx in inspector.get_indexes("webinar_link_claims")}
    if (
        "uq_webinar_claim_user_webinar" not in idx_names
        and "uq_webinar_claim_user_webinar" not in uq_names
    ):
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_webinar_claim_user_webinar "
                "ON webinar_link_claims (user_id, webinar_id)"
            )
        )


async def _backfill_webinar_data() -> None:
    """Attach legacy claims to a webinar and seed from WEBINAR_LINK if needed."""
    from sqlalchemy import func, select, update

    async with get_session() as session:
        webinar_count = await session.scalar(select(func.count()).select_from(Webinar)) or 0
        null_claims = (
            await session.scalar(
                select(func.count())
                .select_from(WebinarLinkClaim)
                .where(WebinarLinkClaim.webinar_id.is_(None))
            )
            or 0
        )

        webinar: Webinar | None = None
        if webinar_count:
            webinar = (
                await session.execute(select(Webinar).order_by(Webinar.id).limit(1))
            ).scalar_one_or_none()
        elif get_settings().webinar_link or null_claims:
            webinar = Webinar(
                title="وبینار",
                link=get_settings().webinar_link,
                time_text="21:00",
                details="لطفا با نام و نام خانوادگی به عنوان شنونده وارد شوید.",
                is_visible=True,
            )
            session.add(webinar)
            await session.flush()

        if webinar is not None and null_claims:
            await session.execute(
                update(WebinarLinkClaim)
                .where(WebinarLinkClaim.webinar_id.is_(None))
                .values(webinar_id=webinar.id)
            )


async def _seed_channels_from_env() -> None:
    """If DB has no required channels, import once from REQUIRED_CHANNELS."""
    from sqlalchemy import func, select

    async with get_session() as session:
        count = await session.scalar(select(func.count()).select_from(RequiredChannel)) or 0
        if count:
            return
        for entry in get_settings().env_channel_entries():
            session.add(
                RequiredChannel(
                    chat_id=entry["chat_id"],
                    username=entry.get("username"),
                    invite_link=entry.get("invite_link"),
                    title=None,
                )
            )
            logger.info("Seeded required channel from env: %s", entry["chat_id"])
