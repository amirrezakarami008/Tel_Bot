"""Shared inline / reply keyboards."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.config import get_settings


def membership_keyboard(check_callback: str) -> InlineKeyboardMarkup:
    """Channel join buttons + membership verification button."""
    settings = get_settings()
    rows: list[list[InlineKeyboardButton]] = []

    for channel in settings.channels:
        invite = channel.get("invite_link")
        if invite:
            rows.append([InlineKeyboardButton("عضویت", url=invite)])
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        "عضویت (لینک عمومی ندارد)",
                        callback_data="noop:no_invite",
                    )
                ]
            )

    rows.append(
        [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data=check_callback)]
    )
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    from bot.handlers.start import BTN_GIFT, BTN_SUPPORT, BTN_WEBINAR

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_WEBINAR)],
            [KeyboardButton(BTN_GIFT)],
            [KeyboardButton(BTN_SUPPORT)],
        ],
        resize_keyboard=True,
    )
