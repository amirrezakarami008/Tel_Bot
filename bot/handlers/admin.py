"""Admin-only commands: /stats, /broadcast, /send_webinar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.config import get_settings
from bot.database.models import GiftFileClaim, SupportDirection, SupportMessage, User, WebinarLinkClaim
from bot.database.session import get_session
from bot.handlers.webinar import broadcast_webinar_to_all_users, mark_webinar_link_announced

logger = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and get_settings().is_admin(user.id)


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "0%"
    return f"{(part * 100 / whole):.0f}%"


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if message is None:
        return
    if not _is_admin(update):
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return

    now = datetime.now(TEHRAN)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_7d = now - timedelta(days=7)

    async with get_session() as session:
        users_count = await session.scalar(select(func.count()).select_from(User)) or 0
        users_today = (
            await session.scalar(
                select(func.count()).select_from(User).where(User.first_seen_at >= start_today)
            )
            or 0
        )
        users_7d = (
            await session.scalar(
                select(func.count()).select_from(User).where(User.first_seen_at >= start_7d)
            )
            or 0
        )

        webinar_count = (
            await session.scalar(select(func.count()).select_from(WebinarLinkClaim)) or 0
        )
        gift_count = await session.scalar(select(func.count()).select_from(GiftFileClaim)) or 0

        both_count = (
            await session.scalar(
                select(func.count())
                .select_from(WebinarLinkClaim)
                .join(GiftFileClaim, GiftFileClaim.user_id == WebinarLinkClaim.user_id)
            )
            or 0
        )
        only_webinar = webinar_count - both_count
        only_gift = gift_count - both_count
        inactive_count = users_count - webinar_count - gift_count + both_count

        webinar_today = (
            await session.scalar(
                select(func.count())
                .select_from(WebinarLinkClaim)
                .where(WebinarLinkClaim.claimed_at >= start_today)
            )
            or 0
        )
        gift_today = (
            await session.scalar(
                select(func.count())
                .select_from(GiftFileClaim)
                .where(GiftFileClaim.claimed_at >= start_today)
            )
            or 0
        )

        last_ids = (
            select(
                SupportMessage.user_id.label("user_id"),
                func.max(SupportMessage.id).label("last_id"),
            )
            .group_by(SupportMessage.user_id)
            .subquery()
        )
        open_count = (
            await session.scalar(
                select(func.count())
                .select_from(SupportMessage)
                .join(last_ids, SupportMessage.id == last_ids.c.last_id)
                .where(SupportMessage.direction == SupportDirection.USER_TO_ADMIN.value)
            )
            or 0
        )
        support_users = (
            await session.scalar(
                select(func.count(func.distinct(SupportMessage.user_id))).where(
                    SupportMessage.direction == SupportDirection.USER_TO_ADMIN.value
                )
            )
            or 0
        )
        support_today = (
            await session.scalar(
                select(func.count())
                .select_from(SupportMessage)
                .where(
                    and_(
                        SupportMessage.direction == SupportDirection.USER_TO_ADMIN.value,
                        SupportMessage.created_at >= start_today,
                    )
                )
            )
            or 0
        )

    await message.reply_text(
        "📊 آمار ربات\n\n"
        "👥 کاربران\n"
        f"کل: {users_count}\n"
        f"امروز: {users_today}\n"
        f"۷ روز اخیر: {users_7d}\n"
        f"بدون اکشن (نه وبینار نه هدیه): {inactive_count}\n\n"
        "🎯 تبدیل فیچرها\n"
        f"وبینار: {webinar_count} ({_pct(webinar_count, users_count)})"
        f" — امروز {webinar_today}\n"
        f"هدیه: {gift_count} ({_pct(gift_count, users_count)})"
        f" — امروز {gift_today}\n"
        f"هر دو: {both_count}\n"
        f"فقط وبینار: {only_webinar}\n"
        f"فقط هدیه: {only_gift}\n\n"
        "💬 پشتیبانی\n"
        f"مکالمات باز: {open_count}\n"
        f"کاربران پیام‌دهنده: {support_users}\n"
        f"پیام‌های امروز: {support_today}"
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


async def send_webinar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the full webinar message (with WEBINAR_LINK from env) to all users."""
    message = update.effective_message
    if message is None:
        return
    if not _is_admin(update):
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return

    settings = get_settings()
    if not settings.webinar_link:
        await message.reply_text(
            "WEBINAR_LINK در .env خالی است.\n"
            "لینک را در .env بگذارید، ربات را ری‌استارت کنید، دوباره /send_webinar بزنید."
        )
        return

    await message.reply_text("در حال ارسال لینک وبینار به همه کاربران…")
    sent, failed = await broadcast_webinar_to_all_users(context.bot)
    await mark_webinar_link_announced(settings.webinar_link)
    await message.reply_text(f"ارسال لینک وبینار تمام شد.\nموفق: {sent}\nناموفق: {failed}")


def register(application: Application) -> None:
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("send_webinar", send_webinar_command))
