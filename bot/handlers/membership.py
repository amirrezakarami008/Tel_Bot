"""Shared force-join membership checks used by webinar and gift features."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from telegram import Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot.config import get_settings
from bot.utils.keyboards import membership_keyboard

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = {"member", "administrator", "creator", "owner"}

NOT_MEMBER_TEXT = (
    "هنوز عضو همه‌ی کانال‌ها نیستی. "
    "لطفاً ابتدا در کانال‌های زیر عضو شو و دوباره بررسی کن."
)

OnSuccess = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


@dataclass(frozen=True)
class MembershipResult:
    ok: bool
    missing: list[str]
    error: bool = False
    error_detail: str | None = None


def _humanize_membership_error(chat_label: str, exc: TelegramError) -> str:
    """Turn Telegram API errors into actionable Persian messages."""
    raw = str(exc).lower()
    if "member list is inaccessible" in raw:
        return (
            f"ربات نمی‌تواند لیست اعضای کانال {chat_label} را ببیند.\n\n"
            "راه‌حل:\n"
            "۱) ربات را داخل آن کانال ادمین کنید\n"
            "۲) دسترسی دیدن/مدیریت اعضا را برای ربات روشن بگذارید\n"
            "۳) دوباره «عضو شدم، بررسی کن» را بزنید"
        )
    if isinstance(exc, Forbidden) or "bot is not a member" in raw or "not enough rights" in raw:
        return (
            f"ربات به کانال {chat_label} دسترسی ندارد.\n"
            "ربات را در آن کانال ادمین کنید، بعد دوباره بررسی کنید."
        )
    if isinstance(exc, BadRequest) and (
        "chat not found" in raw or "chat_id is empty" in raw
    ):
        return (
            f"کانال {chat_label} پیدا نشد.\n"
            "یوزرنیم را در REQUIRED_CHANNELS درست وارد کنید "
            "یا برای کانال خصوصی از فرمت -100...:@username استفاده کنید."
        )
    if "user not found" in raw:
        return "کاربر در تلگرام پیدا نشد. یک‌بار /start را دوباره بزنید."
    return (
        f"خطا در بررسی عضویت برای {chat_label}.\n"
        f"جزئیات فنی: {exc}"
    )


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
                logger.info(
                    "User %s not member of %s (status=%s)",
                    user_id,
                    label,
                    status_value,
                )
        except TelegramError as exc:
            logger.error(
                "get_chat_member failed for chat=%s user=%s: %s",
                chat_id,
                user_id,
                exc,
            )
            return MembershipResult(
                ok=False,
                missing=[],
                error=True,
                error_detail=_humanize_membership_error(label, exc),
            )

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
        text = result.error_detail or "خطا در بررسی عضویت، بعداً تلاش کنید."
        await query.message.reply_text(text)  # type: ignore[union-attr]
        return

    if not result.ok:
        missing = "، ".join(result.missing) if result.missing else "کانال‌های الزامی"
        await query.message.reply_text(  # type: ignore[union-attr]
            f"{NOT_MEMBER_TEXT}\n\nکانال‌های باقی‌مانده: {missing}",
            reply_markup=membership_keyboard(check_callback),
        )
        return

    await on_success(update, context)
