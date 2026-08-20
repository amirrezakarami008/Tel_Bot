"""Admin-only commands: /stats and /broadcast."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.config import get_settings
from bot.database.models import GiftFileClaim, SupportDirection, SupportMessage, User, WebinarLinkClaim
from bot.database.session import get_session

logger = logging.getLogger(__name__)


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and get_settings().is_admin(user.id)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if message is None:
        return
    if not _is_admin(update):
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return

    async with get_session() as session:
        users_count = await session.scalar(select(func.count()).select_from(User)) or 0
        webinar_count = (
            await session.scalar(select(func.count()).select_from(WebinarLinkClaim)) or 0
        )
        gift_count = await session.scalar(select(func.count()).select_from(GiftFileClaim)) or 0

        # Open support: users whose latest support message is still user_to_admin
        last_ids = (
            select(
                SupportMessage.user_id.label("user_id"),
                func.max(SupportMessage.id).label("last_id"),
            )
            .group_by(SupportMessage.user_id)
            .subquery()
        )
        open_count = await session.scalar(
            select(func.count())
            .select_from(SupportMessage)
            .join(last_ids, SupportMessage.id == last_ids.c.last_id)
            .where(SupportMessage.direction == SupportDirection.USER_TO_ADMIN.value)
        ) or 0

    await message.reply_text(
        "📊 آمار ربات\n\n"
        f"کل کاربران: {users_count}\n"
        f"دریافت‌کنندگان لینک وبینار: {webinar_count}\n"
        f"دریافت‌کنندگان فایل هدیه: {gift_count}\n"
        f"مکالمات پشتیبانی باز: {open_count}"
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not _is_admin(update):
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return

    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await message.reply_text("استفاده: /broadcast <متن پیام>")
        return

    async with get_session() as session:
        result = await session.execute(select(User.telegram_id))
        telegram_ids = [row[0] for row in result.all()]

    sent = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await context.bot.send_message(chat_id=telegram_id, text=text)
            sent += 1
        except TelegramError as exc:
            failed += 1
            logger.warning("Broadcast failed for %s: %s", telegram_id, exc)

    await message.reply_text(f"برودکست تمام شد.\nموفق: {sent}\nناموفق: {failed}")


def register(application: Application) -> None:
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
