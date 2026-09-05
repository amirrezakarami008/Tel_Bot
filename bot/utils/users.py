"""User persistence helpers."""

from __future__ import annotations

from telegram import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.session import get_session


def build_full_name(tg_user: TelegramUser) -> str | None:
    parts = [tg_user.first_name or "", tg_user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or None


async def get_or_create_user(session: AsyncSession, tg_user: TelegramUser) -> User:
    """Fetch existing user by telegram_id or create a new row."""
    result = await session.execute(
        select(User).where(User.telegram_id == tg_user.id)
    )
    user = result.scalar_one_or_none()
    full_name = build_full_name(tg_user)

    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=full_name,
        )
        session.add(user)
        await session.flush()
        return user

    # Keep profile fields fresh
    user.username = tg_user.username
    user.full_name = full_name
    await session.flush()
    return user


async def list_all_telegram_ids() -> list[int]:
    async with get_session() as session:
        result = await session.execute(select(User.telegram_id))
        return [row[0] for row in result.all()]
