"""Shared inline / reply keyboards."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.config import get_settings
from bot.utils.buttons import BTN_GIFT, BTN_MANAGE, BTN_STATS, BTN_SUPPORT
from bot.utils.channels import channel_label, channel_to_dict, list_required_channels
from bot.utils.features import FEATURE_GIFT, FEATURE_SUPPORT, is_feature_enabled
from bot.utils.webinars import list_visible_webinars, webinar_button_text


async def membership_keyboard(check_callback: str) -> InlineKeyboardMarkup:
    """Channel join buttons + membership verification button."""
    rows: list[list[InlineKeyboardButton]] = []

    for channel in await list_required_channels():
        entry = channel_to_dict(channel)
        invite = entry.get("invite_link")
        label = channel_label(channel)
        if invite:
            rows.append([InlineKeyboardButton(f"عضویت · {label}", url=invite)])
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"عضویت · {label} (لینک ندارد)",
                        callback_data="noop:no_invite",
                    )
                ]
            )

    rows.append(
        [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data=check_callback)]
    )
    return InlineKeyboardMarkup(rows)


async def main_menu_keyboard(
    telegram_id: int | None = None,
) -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    rows: list[list[KeyboardButton]] = []

    for webinar in await list_visible_webinars():
        rows.append([KeyboardButton(webinar_button_text(webinar))])

    if await is_feature_enabled(FEATURE_GIFT):
        rows.append([KeyboardButton(BTN_GIFT)])
    if await is_feature_enabled(FEATURE_SUPPORT):
        rows.append([KeyboardButton(BTN_SUPPORT)])

    if telegram_id is not None and get_settings().is_admin(telegram_id):
        rows.append([KeyboardButton(BTN_STATS)])
        rows.append([KeyboardButton(BTN_MANAGE)])

    if not rows:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def wizard_keyboard(*, optional: bool) -> ReplyKeyboardMarkup:
    from bot.utils.buttons import BTN_CANCEL, BTN_SKIP

    rows: list[list[KeyboardButton]] = []
    if optional:
        rows.append([KeyboardButton(BTN_SKIP)])
    rows.append([KeyboardButton(BTN_CANCEL)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


async def admin_panel_keyboard() -> InlineKeyboardMarkup:
    gift_on = await is_feature_enabled(FEATURE_GIFT)
    support_on = await is_feature_enabled(FEATURE_SUPPORT)
    from bot.utils.webinars import list_webinars

    webinars = await list_webinars()
    channels = await list_required_channels()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"🎁 فایل هدیه: {'روشن ✅' if gift_on else 'خاموش ❌'}",
                callback_data="admin:toggle:gift",
            )
        ],
        [
            InlineKeyboardButton(
                f"💬 پشتیبانی: {'روشن ✅' if support_on else 'خاموش ❌'}",
                callback_data="admin:toggle:support",
            )
        ],
        [InlineKeyboardButton("💳 تنظیمات پرداخت (کارت)", callback_data="admin:payment")],
        [InlineKeyboardButton("➕ افزودن کانال اجباری", callback_data="admin:channel:new")],
    ]
    for channel in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📢 {channel_label(channel)}",
                    callback_data=f"admin:channel:view:{channel.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("➕ وبینار جدید", callback_data="admin:webinar:new")])
    for webinar in webinars:
        mark = "👁" if webinar.is_visible else "🙈"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark} {webinar.title}",
                    callback_data=f"admin:webinar:view:{webinar.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def channel_manage_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 حذف کانال", callback_data=f"admin:channel:delete:{channel_id}")],
            [InlineKeyboardButton("« بازگشت", callback_data="admin:panel")],
        ]
    )


def webinar_manage_keyboard(webinar_id: int, *, visible: bool, has_certificate: bool) -> InlineKeyboardMarkup:
    visibility = "🙈 مخفی کردن دکمه" if visible else "👁 نمایش دکمه در منو"
    cert = "🎓 مدرک: دارد" if has_certificate else "🎓 مدرک: ندارد"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ نام", callback_data=f"admin:webinar:edit:{webinar_id}:title"),
                InlineKeyboardButton("⏰ ساعت", callback_data=f"admin:webinar:edit:{webinar_id}:time"),
            ],
            [
                InlineKeyboardButton("🔗 لینک", callback_data=f"admin:webinar:edit:{webinar_id}:link"),
                InlineKeyboardButton("📝 جزئیات", callback_data=f"admin:webinar:edit:{webinar_id}:details"),
            ],
            [
                InlineKeyboardButton(cert, callback_data=f"admin:webinar:cert:{webinar_id}"),
                InlineKeyboardButton("💰 مبلغ مدرک", callback_data=f"admin:webinar:edit:{webinar_id}:price"),
            ],
            [InlineKeyboardButton(visibility, callback_data=f"admin:webinar:toggle:{webinar_id}")],
            [InlineKeyboardButton("👥 ثبت‌نام‌ها", callback_data=f"admin:webinar:regs:{webinar_id}")],
            [
                InlineKeyboardButton(
                    "📤 ارسال لینک به ثبت‌نامی‌ها",
                    callback_data=f"admin:webinar:send:{webinar_id}",
                )
            ],
            [InlineKeyboardButton("🗑 حذف وبینار", callback_data=f"admin:webinar:delete:{webinar_id}")],
            [InlineKeyboardButton("« بازگشت", callback_data="admin:panel")],
        ]
    )


def registration_list_keyboard(webinar_id: int, registrations: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for reg in registrations[:30]:
        user = getattr(reg, "user", None)
        name = (user.full_name if user else None) or f"#{reg.id}"
        if len(name) > 28:
            name = name[:25] + "..."
        mark = {
            "approved": "✅",
            "pending_review": "🧾",
            "pending_payment": "💳",
            "rejected": "❌",
        }.get(reg.status, "•")
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark} {name}",
                    callback_data=f"admin:webinar:reg:{reg.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("« بازگشت", callback_data=f"admin:webinar:view:{webinar_id}")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("بله", callback_data=yes_data),
                InlineKeyboardButton("انصراف", callback_data=no_data),
            ]
        ]
    )
