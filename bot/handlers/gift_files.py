"""Feature 3: gated gift PDF delivery."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.config import get_settings
from bot.database.models import GiftFileClaim
from bot.database.session import get_session
from bot.handlers.membership import handle_membership_check, require_membership_or_prompt
from bot.utils.features import FEATURE_GIFT, is_feature_enabled
from bot.utils.keyboards import main_menu_keyboard
from bot.utils.users import get_or_create_user

logger = logging.getLogger(__name__)

VERIFY_CALLBACK = "check_membership:gift"

GATE_TEXT = (
    "برای دریافت فایل‌های هدیه، ابتدا در کانال‌های زیر عضو شوید "
    "و سپس روی دکمه «عضو شدم، بررسی کن» بزنید."
)


def list_gift_files() -> list[Path]:
    directory = Path(get_settings().gift_files_dir)
    if not directory.exists() or not directory.is_dir():
        logger.warning("Gift files directory missing: %s", directory)
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.name.startswith(".")
    )


async def _deliver_gifts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return

    files = list_gift_files()
    if not files:
        await message.reply_text(
            "در حال حاضر فایل هدیه‌ای موجود نیست. بعداً تلاش کنید.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return

    for file_path in files:
        try:
            with file_path.open("rb") as document:
                await context.bot.send_document(
                    chat_id=chat.id,
                    document=document,
                    filename=file_path.name,
                    caption=f"🎁 {file_path.name}",
                )
        except Exception:
            logger.exception("Failed to send gift file %s to user %s", file_path, user.id)
            await message.reply_text(
                f"ارسال فایل «{file_path.name}» با خطا مواجه شد. بعداً تلاش کنید."
            )
            return

    async with get_session() as session:
        db_user = await get_or_create_user(session, user)
        existing = await session.execute(
            select(GiftFileClaim).where(GiftFileClaim.user_id == db_user.id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(GiftFileClaim(user_id=db_user.id))

    await message.reply_text(
        "فایل‌های هدیه با موفقیت ارسال شد.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    logger.info("Gift files claimed by telegram_id=%s count=%s", user.id, len(files))


async def send_gift_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check membership first; only show join buttons if needed."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not await is_feature_enabled(FEATURE_GIFT):
        await message.reply_text(
            "دریافت فایل هدیه فعلاً فعال نیست.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return
    await require_membership_or_prompt(
        update,
        context,
        check_callback=VERIFY_CALLBACK,
        intro_text=GATE_TEXT,
        on_success=_deliver_gifts,
    )


async def verify_gift_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is not None and user is not None and not await is_feature_enabled(FEATURE_GIFT):
        await query.answer("این گزینه فعلاً فعال نیست.", show_alert=True)
        return
    await handle_membership_check(
        update,
        context,
        check_callback=VERIFY_CALLBACK,
        on_success=_deliver_gifts,
    )


def register(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(verify_gift_membership, pattern=rf"^{VERIFY_CALLBACK}$")
    )
