"""Shared force-join membership checks used by webinar and gift features."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.config import get_settings
from bot.utils.keyboards import membership_keyboard

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = {"member", "administrator", "creator", "owner"}

MEMBERSHIP_ERROR_TEXT = "خطا در بررسی عضویت، بعداً تلاش کنید."
NOT_MEMBER_TEXT = "هنوز عضو همه‌ی کانال‌ها نیستی. لطفاً ابتدا در کانال‌های زیر عضو شو و دوباره بررسی کن."

OnSuccess = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


@dataclass(frozen=True)
class MembershipResult:
    ok: bool
    missing: list[str]
    error: bool = False


async def check_user_membership(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> MembershipResult:
    """
    Verify the user is a member of every channel in REQUIRED_CHANNELS.

    On Telegram API errors (e.g. bot is not a channel admin), returns error=True.
    """
    settings = get_settings()
    missing: list[str] = []

    for channel in settings.channels:
        chat_id = channel["chat_id"]
        label = f"@{channel['username']}" if channel.get("username") else str(chat_id)
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            status = member.status
            status_value = status.value if hasattr(status, "value") else str(status)
            if status_value not in ALLOWED_STATUSES:
                missing.append(label)
        except TelegramError as exc:
            logger.error(
                "get_chat_member failed for chat=%s user=%s: %s",
                chat_id,
                user_id,
                exc,
            )
            return MembershipResult(ok=False, missing=[], error=True)

    return MembershipResult(ok=not missing, missing=missing, error=False)


async def prompt_force_join(
    update: Update,
    *,
    check_callback: str,
    intro_text: str,
) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(intro_text, reply_markup=membership_keyboard(check_callback))


async def handle_membership_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    check_callback: str,
    on_success: OnSuccess,
) -> None:
    """Shared handler for the «عضو شدم، بررسی کن» callback."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    await query.answer()
    result = await check_user_membership(context, update.effective_user.id)

    if result.error:
        await query.message.reply_text(MEMBERSHIP_ERROR_TEXT)  # type: ignore[union-attr]
        return

    if not result.ok:
        await query.message.reply_text(  # type: ignore[union-attr]
            NOT_MEMBER_TEXT,
            reply_markup=membership_keyboard(check_callback),
        )
        return

    await on_success(update, context)
