""" /start and main menu handlers. """

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.database.session import get_session
from bot.utils.keyboards import main_menu_keyboard
from bot.utils.users import get_or_create_user

logger = logging.getLogger(__name__)

BTN_WEBINAR = "🔗 دریافت لینک وبینار"
BTN_GIFT = "🎁 دریافت فایل‌های هدیه"
BTN_SUPPORT = "💬 پشتیبانی"

WELCOME_TEXT = (
    "سلام! به ربات خوش آمدید.\n\n"
    "از منوی زیر یکی از گزینه‌ها را انتخاب کنید."
)

SUPPORT_HINT = (
    "برای ارتباط با پشتیبانی، پیام خود را (متن، عکس یا فایل) همین‌جا ارسال کنید. "
    "پاسخ ادمین نیز در همین چت به شما می‌رسد."
)

_MENU_PATTERN = (
    f"^({re.escape(BTN_WEBINAR)}|{re.escape(BTN_GIFT)}|{re.escape(BTN_SUPPORT)})$"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    async with get_session() as session:
        await get_or_create_user(session, update.effective_user)

    args = context.args or []
    payload = args[0].lower() if args else ""

    if payload == "webinar":
        from bot.handlers.webinar import send_webinar_gate

        await send_webinar_gate(update, context)
        return

    if payload in {"gift", "gifts", "gift_files"}:
        from bot.handlers.gift_files import send_gift_gate

        await send_gift_gate(update, context)
        return

    await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())


async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return

    text = update.message.text.strip()

    if text == BTN_WEBINAR:
        from bot.handlers.webinar import send_webinar_gate

        await send_webinar_gate(update, context)
        return

    if text == BTN_GIFT:
        from bot.handlers.gift_files import send_gift_gate

        await send_gift_gate(update, context)
        return

    if text == BTN_SUPPORT:
        await update.message.reply_text(SUPPORT_HINT)
        return


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(_MENU_PATTERN),
            menu_text_handler,
        )
    )
