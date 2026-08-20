"""Feature 1: gated webinar link delivery."""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.config import get_settings
from bot.database.models import WebinarLinkClaim
from bot.database.session import get_session
from bot.handlers.membership import handle_membership_check, prompt_force_join
from bot.utils.users import get_or_create_user

logger = logging.getLogger(__name__)

VERIFY_CALLBACK = "check_membership:webinar"

GATE_TEXT = (
    "برای دریافت لینک وبینار، ابتدا در کانال‌های زیر عضو شوید "
    "و سپس روی دکمه «عضو شدم، بررسی کن» بزنید."
)


async def send_webinar_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context  # unused; kept for consistent handler signature
    await prompt_force_join(
        update,
        check_callback=VERIFY_CALLBACK,
        intro_text=GATE_TEXT,
    )


async def _deliver_webinar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    user = update.effective_user
    if query is None or query.message is None or user is None:
        return

    settings = get_settings()

    async with get_session() as session:
        db_user = await get_or_create_user(session, user)
        existing = await session.execute(
            select(WebinarLinkClaim).where(WebinarLinkClaim.user_id == db_user.id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(WebinarLinkClaim(user_id=db_user.id))

    await query.message.reply_text(
        f"عضویت شما تأیید شد.\n\nلینک وبینار:\n{settings.webinar_link}"
    )
    logger.info("Webinar link claimed by telegram_id=%s", user.id)


async def verify_webinar_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_membership_check(
        update,
        context,
        check_callback=VERIFY_CALLBACK,
        on_success=_deliver_webinar,
    )


def register(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(verify_webinar_membership, pattern=rf"^{VERIFY_CALLBACK}$")
    )
