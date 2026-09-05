"""Admin-only commands: /stats, /broadcast, /send_webinar."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.config import get_settings
from bot.database.models import GiftFileClaim, SupportDirection, SupportMessage, User, WebinarLinkClaim
from bot.database.session import get_session
from bot.utils.buttons import BTN_STATS
from bot.utils.keyboards import main_menu_keyboard
from bot.utils.users import list_all_telegram_ids

logger = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and get_settings().is_admin(user.id)


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "0٪"
    return f"{(part * 100 / whole):.0f}٪"


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

    user = update.effective_user
    await message.reply_text(
        "📊 گزارش ربات\n\n"
        "👥 کاربران\n"
        f"• کل کسانی که ربات را استارت کرده‌اند: {users_count} نفر\n"
        f"• تازه‌وارد امروز: {users_today} نفر\n"
        f"• تازه‌وارد ۷ روز اخیر: {users_7d} نفر\n"
        f"• هنوز نه لینک وبینار گرفته‌اند نه فایل هدیه: {inactive_count} نفر\n\n"
        "🔗 لینک وبینار\n"
        f"• تا حالا گرفته‌اند: {webinar_count} نفر از {users_count}"
        f" ({_pct(webinar_count, users_count)})\n"
        f"• امروز گرفته‌اند: {webinar_today} نفر\n\n"
        "🎁 فایل هدیه\n"
        f"• تا حالا گرفته‌اند: {gift_count} نفر از {users_count}"
        f" ({_pct(gift_count, users_count)})\n"
        f"• امروز گرفته‌اند: {gift_today} نفر\n\n"
        "📌 خلاصه دریافت‌ها\n"
        f"• هم وبینار هم هدیه: {both_count} نفر\n"
        f"• فقط وبینار: {only_webinar} نفر\n"
        f"• فقط هدیه: {only_gift} نفر\n\n"
        "💬 پشتیبانی\n"
        f"• منتظر پاسخ شما: {open_count} گفتگو\n"
        f"• تا حالا پیام پشتیبانی فرستاده‌اند: {support_users} نفر\n"
        f"• پیام‌های پشتیبانی امروز: {support_today}",
        reply_markup=await main_menu_keyboard(user.id if user else None),
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
        await message.reply_text(
            "استفاده سریع: /broadcast <متن پیام>\n"
            "یا از «⚙️ مدیریت منو» → «📢 پیام همگانی» مخاطب را انتخاب کنید."
        )
        return

    from bot.handlers.admin_panel import broadcast_text_to_users

    telegram_ids = await list_all_telegram_ids()
    sent, failed = await broadcast_text_to_users(context.bot, telegram_ids, text)
    await message.reply_text(f"برودکست تمام شد.\nموفق: {sent}\nناموفق: {failed}")


async def send_webinar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    if not _is_admin(update):
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return

    await message.reply_text(
        "برای ارسال وبینار، از دکمه «⚙️ مدیریت منو» وبینار را باز کنید "
        "و «ارسال به همه کاربران» را بزنید.",
        reply_markup=await main_menu_keyboard(user.id if user else None),
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{re.escape(BTN_STATS)}$"),
            stats_command,
        )
    )
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("send_webinar", send_webinar_command))
