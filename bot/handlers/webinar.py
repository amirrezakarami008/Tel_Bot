"""Feature 1: gated webinar link delivery."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.config import get_settings
from bot.database.models import BotSetting, User, WebinarLinkClaim
from bot.database.session import get_session
from bot.handlers.membership import handle_membership_check, require_membership_or_prompt
from bot.utils.users import get_or_create_user
from bot.utils.webinar_text import build_webinar_message

logger = logging.getLogger(__name__)

VERIFY_CALLBACK = "check_membership:webinar"
ANNOUNCED_LINK_KEY = "announced_webinar_link"

GATE_TEXT = (
    "برای دریافت لینک وبینار، ابتدا در کانال‌های زیر عضو شوید "
    "و سپس روی دکمه «عضو شدم، بررسی کن» بزنید."
)


async def _record_claim(tg_user) -> None:
    async with get_session() as session:
        db_user = await get_or_create_user(session, tg_user)
        existing = await session.execute(
            select(WebinarLinkClaim).where(WebinarLinkClaim.user_id == db_user.id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(WebinarLinkClaim(user_id=db_user.id))


async def _deliver_webinar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    await _record_claim(user)
    await message.reply_text(build_webinar_message())
    logger.info("Webinar message delivered to telegram_id=%s", user.id)


async def send_webinar_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check membership first; only show join buttons if needed."""
    await require_membership_or_prompt(
        update,
        context,
        check_callback=VERIFY_CALLBACK,
        intro_text=GATE_TEXT,
        on_success=_deliver_webinar,
    )


async def verify_webinar_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_membership_check(
        update,
        context,
        check_callback=VERIFY_CALLBACK,
        on_success=_deliver_webinar,
    )


async def broadcast_webinar_to_all_users(bot) -> tuple[int, int]:
    """Send the linked webinar message to every registered user. Returns (sent, failed)."""
    settings = get_settings()
    if not settings.webinar_link:
        return 0, 0

    text = build_webinar_message(settings.webinar_link)

    async with get_session() as session:
        result = await session.execute(select(User.telegram_id))
        telegram_ids = [row[0] for row in result.all()]

    sent = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await bot.send_message(chat_id=telegram_id, text=text)
            sent += 1
        except TelegramError as exc:
            failed += 1
            logger.warning("Webinar broadcast failed for %s: %s", telegram_id, exc)
        await asyncio.sleep(0.05)

    return sent, failed


async def _get_announced_link() -> str | None:
    async with get_session() as session:
        row = await session.get(BotSetting, ANNOUNCED_LINK_KEY)
        return row.value if row else None


async def mark_webinar_link_announced(link: str) -> None:
    async with get_session() as session:
        row = await session.get(BotSetting, ANNOUNCED_LINK_KEY)
        if row is None:
            session.add(BotSetting(key=ANNOUNCED_LINK_KEY, value=link))
        else:
            row.value = link


async def announce_webinar_if_link_changed(application: Application) -> None:
    """
    On startup: if WEBINAR_LINK is set and differs from the last announced value,
    send the full message to all registered users.
    """
    settings = get_settings()
    link = settings.webinar_link
    if not link:
        logger.info("WEBINAR_LINK empty — skipping auto-announce")
        return

    previous = await _get_announced_link()
    if previous == link:
        logger.info("WEBINAR_LINK unchanged — skipping auto-announce")
        return

    logger.info("New WEBINAR_LINK detected — announcing to all users")
    sent, failed = await broadcast_webinar_to_all_users(application.bot)
    await mark_webinar_link_announced(link)
    logger.info("Webinar auto-announce done. sent=%s failed=%s", sent, failed)


def register(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(verify_webinar_membership, pattern=rf"^{VERIFY_CALLBACK}$")
    )
