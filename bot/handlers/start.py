""" /start and main menu handlers. """

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.database.session import get_session
from bot.utils.buttons import BTN_GIFT, BTN_SUPPORT
from bot.utils.features import FEATURE_GIFT, FEATURE_SUPPORT, is_feature_enabled
from bot.utils.keyboards import main_menu_keyboard
from bot.utils.users import get_or_create_user
from bot.utils.webinars import get_webinar, list_visible_webinars

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "سلام! به ربات خوش آمدید.\n\n"
    "از منوی زیر یکی از گزینه‌ها را انتخاب کنید."
)

SUPPORT_HINT = (
    "برای ارتباط با پشتیبانی، پیام خود را (متن، عکس یا فایل) همین‌جا ارسال کنید. "
    "پاسخ ادمین نیز در همین چت به شما می‌رسد."
)

_MENU_PATTERN = f"^({re.escape(BTN_GIFT)}|{re.escape(BTN_SUPPORT)})$"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    async with get_session() as session:
        await get_or_create_user(session, update.effective_user)

    args = context.args or []
    payload = args[0].lower() if args else ""

    if payload == "gift" or payload in {"gifts", "gift_files"}:
        from bot.handlers.gift_files import send_gift_gate

        await send_gift_gate(update, context)
        return

    if payload == "webinar" or payload.startswith("webinar_"):
        await _start_webinar_payload(update, context, payload)
        return

    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=await main_menu_keyboard(update.effective_user.id),
    )


async def _start_webinar_payload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    payload: str,
) -> None:
    from bot.handlers.webinar import send_webinar_gate

    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    if payload.startswith("webinar_") and payload != "webinar_":
        try:
            webinar_id = int(payload.split("_", 1)[1])
        except ValueError:
            webinar_id = None
        webinar = await get_webinar(webinar_id) if webinar_id is not None else None
        if webinar is None or not webinar.is_visible:
            await message.reply_text(
                "این وبینار در دسترس نیست.",
                reply_markup=await main_menu_keyboard(user.id),
            )
            return
        await send_webinar_gate(update, context, webinar.id)
        return

    visible = await list_visible_webinars()
    if len(visible) == 1:
        await send_webinar_gate(update, context, visible[0].id)
        return
    if not visible:
        await message.reply_text(
            "در حال حاضر وبینار فعالی وجود ندارد.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return
    await message.reply_text(
        "از منوی زیر وبینار مورد نظر را انتخاب کنید.",
        reply_markup=await main_menu_keyboard(user.id),
    )


async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None or update.effective_user is None:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == BTN_GIFT:
        if not await is_feature_enabled(FEATURE_GIFT):
            await update.message.reply_text(
                "دریافت فایل هدیه فعلاً فعال نیست.",
                reply_markup=await main_menu_keyboard(user_id),
            )
            return
        from bot.handlers.gift_files import send_gift_gate

        await send_gift_gate(update, context)
        return

    if text == BTN_SUPPORT:
        if not await is_feature_enabled(FEATURE_SUPPORT):
            await update.message.reply_text(
                "پشتیبانی فعلاً فعال نیست.",
                reply_markup=await main_menu_keyboard(user_id),
            )
            return
        await update.message.reply_text(
            SUPPORT_HINT,
            reply_markup=await main_menu_keyboard(user_id),
        )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(_MENU_PATTERN),
            menu_text_handler,
        )
    )
