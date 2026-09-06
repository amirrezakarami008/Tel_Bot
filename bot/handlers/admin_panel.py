"""Admin panel: feature toggles and dynamic webinar management."""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import get_settings
from bot.database.models import RegistrationStatus
from bot.utils.buttons import BTN_CANCEL, BTN_MANAGE, BTN_SKIP
from bot.utils.channels import (
    channel_label,
    create_required_channel,
    delete_required_channel,
    get_required_channel,
    normalize_invite_link,
    parse_channel_input,
)
from bot.utils.features import FEATURE_LABELS, toggle_feature
from bot.utils.keyboards import (
    admin_panel_keyboard,
    broadcast_audience_keyboard,
    broadcast_segment_keyboard,
    broadcast_webinar_pick_keyboard,
    channel_manage_keyboard,
    confirm_keyboard,
    gift_file_manage_keyboard,
    gift_files_keyboard,
    main_menu_keyboard,
    registration_list_keyboard,
    webinar_manage_keyboard,
    wizard_keyboard,
)
from bot.utils.gifts import (
    delete_gift_file,
    get_gift_file_by_index,
    list_gift_files,
    save_telegram_document,
)
from bot.utils.payment import get_payment_card, set_payment_card
from bot.utils.registrations import (
    get_registration_by_id,
    list_registration_telegram_ids,
    list_registrations_for_webinar,
    registration_summary,
)
from bot.utils.backup import BackupError, send_database_backup
from bot.utils.users import list_all_telegram_ids
from bot.utils.webinars import (
    DETAILS_MAX,
    build_webinar_message,
    create_webinar,
    delete_webinar,
    get_webinar,
    normalize_link,
    normalize_optional,
    normalize_title,
    update_webinar,
)
from bot.handlers.webinar import _registration_admin_keyboard

logger = logging.getLogger(__name__)

(
    ASK_TITLE,
    ASK_TIME,
    ASK_DETAILS,
    ASK_LINK,
    ASK_GROUP_LINK,
    EDIT_VALUE,
    ASK_CHANNEL,
    ASK_CHANNEL_INVITE,
    ASK_HAS_CERT,
    ASK_PRICE,
    ASK_PAYMENT_CARD,
    ASK_PAYMENT_HOLDER,
    ASK_GIFT_FILE,
    BROADCAST_PICK,
    BROADCAST_TEXT,
    BROADCAST_CONFIRM,
) = range(16)

PANEL_TEXT = (
    "⚙️ مدیریت منو\n\n"
    "• دکمه‌ها و کانال‌های اجباری\n"
    "• وبینار + مدرک/پرداخت\n"
    "• فایل‌های هدیه (آپلود/حذف)\n"
    "• کارت بانکی برای واریز مدرک\n"
    "• پیام همگانی با انتخاب مخاطب\n"
    "• بک‌آپ دیتابیس (دستی و هر شب ۱۲ شب)"
)

ADMIN_CALLBACK_PATTERN = (
    r"^admin:(panel|payment|gifts|backup|toggle:|channel:(view|delete|delete_yes):|"
    r"webinar:(view|toggle|cert|regs|pending|reg|send|send_yes|delete|delete_yes):)"
)

BROADCAST_CALLBACK_PATTERN = r"^admin:broadcast(?::.*)?$"

EDIT_PROMPTS = {
    "title": "نام جدید وبینار را بفرستید:",
    "time": "ساعت برگزاری را بفرستید (مثلاً 21:00).\nبرای خالی ماندن «رد شدن» را بزنید.",
    "details": "جزئیات وبینار را بفرستید.\nبرای خالی ماندن «رد شدن» را بزنید.",
    "link": "لینک ورود را بفرستید (با https://).\nبرای خالی ماندن «رد شدن» را بزنید.",
    "group": "لینک گروه وبینار را بفرستید (با https://).\nبرای خالی ماندن «رد شدن» را بزنید.",
    "price": "مبلغ مدرک را بفرستید (مثلاً ۱۵۰٬۰۰۰ تومان).\nبرای خالی ماندن «رد شدن» را بزنید.",
}

BTN_CERT_YES = "بله، مدرک دارد"
BTN_CERT_NO = "خیر، بدون مدرک"

BROADCAST_SEGMENTS = {
    "approved": "ثبت‌نامی‌های تاییدشده",
    "all": "همه ثبت‌نام‌ها (هر وضعیت)",
    "pending": "در انتظار بررسی رسید",
}

BROADCAST_TEXT_MAX = 4000


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and get_settings().is_admin(user.id)


def _is_skip(text: str) -> bool:
    return text.strip() in {BTN_SKIP, "—", "-", "/skip"}


def _is_cancel(text: str) -> bool:
    return text.strip() in {BTN_CANCEL, "/cancel"}


def _webinar_view_text(webinar) -> str:
    cert = "دارد" if webinar.has_certificate else "ندارد"
    price = webinar.certificate_price or "—"
    return (
        f"📺 {webinar.title}\n\n"
        f"نام: {webinar.title}\n"
        f"ساعت: {webinar.time_text or '—'}\n"
        f"لینک ورود: {webinar.link or 'ثبت نشده'}\n"
        f"لینک گروه: {webinar.group_link or 'ثبت نشده'}\n"
        f"جزئیات: {webinar.details or '—'}\n"
        f"مدرک: {cert}\n"
        f"مبلغ مدرک: {price}\n"
        f"دکمه منو: {'نمایش داده می‌شود' if webinar.is_visible else 'مخفی است'}"
    )


def _channel_view_text(channel) -> str:
    username = f"@{channel.username}" if channel.username else "—"
    return (
        "📢 کانال اجباری\n\n"
        f"عنوان: {channel.title or '—'}\n"
        f"نمایش: {channel_label(channel)}\n"
        f"شناسه: {channel.chat_id}\n"
        f"یوزرنیم: {username}\n"
        f"لینک عضویت: {channel.invite_link or 'ثبت نشده'}\n\n"
        "توجه: ربات باید در این کانال ادمین باشد."
    )


async def _show_panel_message(message, *, user_id: int) -> None:
    await message.reply_text(
        PANEL_TEXT,
        reply_markup=await main_menu_keyboard(user_id),
    )
    await message.reply_text(
        "گزینه‌ها:",
        reply_markup=await admin_panel_keyboard(),
    )


async def open_manage_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not _is_admin(update):
        await message.reply_text("شما دسترسی ادمین ندارید.")
        return
    await _show_panel_message(message, user_id=user.id)


async def _edit_panel(query) -> None:
    await query.edit_message_text(
        PANEL_TEXT,
        reply_markup=await admin_panel_keyboard(),
    )


async def _edit_webinar_view(query, webinar) -> None:
    await query.edit_message_text(
        _webinar_view_text(webinar),
        reply_markup=webinar_manage_keyboard(
            webinar.id,
            visible=webinar.is_visible,
            has_certificate=webinar.has_certificate,
        ),
    )


async def _edit_channel_view(query, channel) -> None:
    await query.edit_message_text(
        _channel_view_text(channel),
        reply_markup=channel_manage_keyboard(channel.id),
    )


async def _show_gifts_panel(query) -> None:
    files = list_gift_files()
    await query.edit_message_text(
        "🎁 مدیریت فایل‌های هدیه\n\n"
        f"تعداد فایل‌ها: {len(files)}\n"
        "می‌توانید فایل جدید آپلود کنید یا فایل موجود را حذف کنید.",
        reply_markup=gift_files_keyboard(),
    )


async def _handle_gifts_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    del context
    query = update.callback_query
    if query is None:
        return

    if data == "admin:gifts":
        await _show_gifts_panel(query)
        return

    if data == "admin:gifts:upload":
        # Handled by ConversationHandler entry; ignore if reached here.
        return

    if data.startswith("admin:gifts:file:"):
        index = int(data.rsplit(":", 1)[-1])
        path = get_gift_file_by_index(index)
        if path is None:
            await _show_gifts_panel(query)
            return
        size_kb = max(1, path.stat().st_size // 1024)
        await query.edit_message_text(
            f"📄 {path.name}\nحجم تقریبی: {size_kb} KB",
            reply_markup=gift_file_manage_keyboard(index),
        )
        return

    if data.startswith("admin:gifts:delete:") and not data.startswith("admin:gifts:delete_yes:"):
        index = int(data.rsplit(":", 1)[-1])
        path = get_gift_file_by_index(index)
        if path is None:
            await _show_gifts_panel(query)
            return
        await query.edit_message_text(
            f"فایل «{path.name}» حذف شود؟",
            reply_markup=confirm_keyboard(
                f"admin:gifts:delete_yes:{index}",
                f"admin:gifts:file:{index}",
            ),
        )
        return

    if data.startswith("admin:gifts:delete_yes:"):
        index = int(data.rsplit(":", 1)[-1])
        path = get_gift_file_by_index(index)
        if path is None:
            await _show_gifts_panel(query)
            return
        name = path.name
        if delete_gift_file(path):
            if query.message:
                await query.message.reply_text(f"فایل «{name}» حذف شد.")
        else:
            if query.message:
                await query.message.reply_text("حذف فایل ناموفق بود.")
        await _show_gifts_panel(query)


async def _handle_manual_backup(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = query.message
    if message is None:
        return
    status = await message.reply_text("⏳ در حال تهیه بک‌آپ کامل دیتابیس…")
    try:
        dump = await send_database_backup(context.bot, reason="بک‌آپ دستی از پنل ادمین")
        await status.edit_text(
            "✅ بک‌آپ آماده شد و فقط برای ادمین(ها) ارسال شد.\n"
            f"📄 {dump.filename}"
        )
    except BackupError as exc:
        logger.error("Manual database backup failed: %s", exc)
        await status.edit_text(f"❌ تهیه بک‌آپ ناموفق بود.\n{exc}")
    except Exception:
        logger.exception("Manual database backup failed")
        await status.edit_text("❌ تهیه بک‌آپ ناموفق بود. جزئیات در لاگ سرور است.")


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return

    data = query.data
    if data == "admin:backup":
        await query.answer("در حال تهیه بک‌آپ…")
        await _handle_manual_backup(query, context)
        return

    await query.answer()

    if data == "admin:panel":
        await _edit_panel(query)
        return

    if data == "admin:payment":
        number, holder = await get_payment_card()
        await query.edit_message_text(
            "💳 تنظیمات پرداخت مدرک\n\n"
            f"شماره کارت: {number or 'ثبت نشده'}\n"
            f"به نام: {holder or '—'}\n\n"
            "برای تغییر، دکمه زیر را بزنید.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✏️ تغییر کارت", callback_data="admin:payment:edit")],
                    [InlineKeyboardButton("« بازگشت", callback_data="admin:panel")],
                ]
            ),
        )
        return

    if data == "admin:gifts" or data.startswith("admin:gifts:"):
        await _handle_gifts_callback(update, context, data)
        return

    if data.startswith("admin:toggle:"):
        feature = data.split(":", 2)[2]
        enabled = await toggle_feature(feature)
        label = FEATURE_LABELS.get(feature, feature)
        state = (
            "روشن شد و در منوی کاربران نمایش داده می‌شود"
            if enabled
            else "خاموش شد و از منوی کاربران برداشته شد"
        )
        await _edit_panel(query)
        if query.message:
            await query.message.reply_text(
                f"{label} {state}.",
                reply_markup=await main_menu_keyboard(update.effective_user.id),
            )
        return

    if data.startswith("admin:channel:view:"):
        channel_id = int(data.rsplit(":", 1)[-1])
        channel = await get_required_channel(channel_id)
        if channel is None:
            await _edit_panel(query)
            return
        await _edit_channel_view(query, channel)
        return

    if data.startswith("admin:channel:delete:") and not data.startswith("admin:channel:delete_yes:"):
        channel_id = int(data.rsplit(":", 1)[-1])
        channel = await get_required_channel(channel_id)
        if channel is None:
            await _edit_panel(query)
            return
        await query.edit_message_text(
            f"کانال «{channel_label(channel)}» از لیست عضویت اجباری حذف شود؟",
            reply_markup=confirm_keyboard(
                f"admin:channel:delete_yes:{channel_id}",
                f"admin:channel:view:{channel_id}",
            ),
        )
        return

    if data.startswith("admin:channel:delete_yes:"):
        channel_id = int(data.rsplit(":", 1)[-1])
        await delete_required_channel(channel_id)
        await _edit_panel(query)
        if query.message:
            await query.message.reply_text("کانال از لیست عضویت اجباری حذف شد.")
        return

    if data.startswith("admin:webinar:view:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.answer("وبینار پیدا نشد.", show_alert=True)
            await _edit_panel(query)
            return
        await _edit_webinar_view(query, webinar)
        return

    if data.startswith("admin:webinar:toggle:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.answer("وبینار پیدا نشد.", show_alert=True)
            return
        webinar = await update_webinar(webinar_id, is_visible=not webinar.is_visible)
        if webinar.is_visible:
            note = "دکمه این وبینار به منوی کاربران اضافه شد."
        else:
            note = "دکمه این وبینار از منوی کاربران برداشته شد."
        await _edit_webinar_view(query, webinar)
        if query.message:
            await query.message.reply_text(
                note,
                reply_markup=await main_menu_keyboard(update.effective_user.id),
            )
        return

    if data.startswith("admin:webinar:cert:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await _edit_panel(query)
            return
        webinar = await update_webinar(webinar_id, has_certificate=not webinar.has_certificate)
        await _edit_webinar_view(query, webinar)
        if query.message:
            state = "فعال شد" if webinar.has_certificate else "غیرفعال شد"
            await query.message.reply_text(f"گزینه مدرک {state}.")
        return

    if data.startswith("admin:webinar:regs:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await _edit_panel(query)
            return
        regs = await list_registrations_for_webinar(webinar_id)
        if not regs:
            await query.edit_message_text(
                f"هنوز ثبت‌نامی برای «{webinar.title}» نیست.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("« بازگشت", callback_data=f"admin:webinar:view:{webinar_id}")]]
                ),
            )
            return
        await query.edit_message_text(
            f"👥 ثبت‌نام‌های «{webinar.title}» ({len(regs)} نفر)\nروی هر مورد بزنید تا وضعیت را تغییر دهید.",
            reply_markup=registration_list_keyboard(webinar_id, regs),
        )
        return

    if data.startswith("admin:webinar:pending:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await _edit_panel(query)
            return
        regs = await list_registrations_for_webinar(
            webinar_id,
            status=RegistrationStatus.PENDING_REVIEW.value,
        )
        if not regs:
            await query.edit_message_text(
                f"هیچ ثبت‌نام در انتظار بررسی برای «{webinar.title}» نیست.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("« بازگشت", callback_data=f"admin:webinar:view:{webinar_id}")]]
                ),
            )
            return
        await query.edit_message_text(
            f"🧾 در انتظار بررسی «{webinar.title}» ({len(regs)} نفر)\n"
            "روی هر مورد بزنید تا گفتگو کنید یا تایید/رد کنید.",
            reply_markup=registration_list_keyboard(
                webinar_id,
                regs,
                back_callback=f"admin:webinar:view:{webinar_id}",
            ),
        )
        return

    if data.startswith("admin:webinar:reg:"):
        registration_id = int(data.rsplit(":", 1)[-1])
        reg = await get_registration_by_id(registration_id)
        if reg is None:
            await query.answer("ثبت‌نام پیدا نشد.", show_alert=True)
            return
        text = (
            f"📋 جزئیات ثبت‌نام\n\n{registration_summary(reg)}\n"
            f"وبینار: {reg.webinar.title if reg.webinar else '—'}"
        )
        await query.edit_message_text(
            text,
            reply_markup=_registration_admin_keyboard(reg.id, reg.status, kind=reg.kind),
        )
        if query.message and reg.webinar:
            back = (
                f"admin:webinar:pending:{reg.webinar_id}"
                if reg.status == RegistrationStatus.PENDING_REVIEW.value
                else f"admin:webinar:regs:{reg.webinar_id}"
            )
            back_label = (
                "« لیست در انتظار بررسی"
                if reg.status == RegistrationStatus.PENDING_REVIEW.value
                else "« لیست ثبت‌نام‌ها"
            )
            await query.message.reply_text(
                "بازگشت به لیست:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(back_label, callback_data=back)]]
                ),
            )
        return

    if data.startswith("admin:webinar:send:") and not data.startswith("admin:webinar:send_yes:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.answer("وبینار پیدا نشد.", show_alert=True)
            return
        if not webinar.link:
            await query.answer("ابتدا لینک وبینار را ثبت کنید.", show_alert=True)
            return
        await query.edit_message_text(
            "لینک فقط برای کسانی که پیش‌ثبت‌نام‌شان تایید شده ارسال می‌شود:\n\n"
            f"{build_webinar_message(webinar)}\n\n"
            "ادامه می‌دهید؟",
            reply_markup=confirm_keyboard(
                f"admin:webinar:send_yes:{webinar_id}",
                f"admin:webinar:view:{webinar_id}",
            ),
        )
        return

    if data.startswith("admin:webinar:send_yes:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.answer("وبینار پیدا نشد.", show_alert=True)
            return
        from bot.handlers.webinar import broadcast_webinar_to_registrants

        if query.message:
            await query.message.reply_text("در حال ارسال لینک به ثبت‌نامی‌های تاییدشده…")
        sent, failed = await broadcast_webinar_to_registrants(context.bot, webinar.id)
        webinar = await get_webinar(webinar.id) or webinar
        await query.edit_message_text(
            f"ارسال تمام شد.\nموفق: {sent}\nناموفق: {failed}\n\n{_webinar_view_text(webinar)}",
            reply_markup=webinar_manage_keyboard(
                webinar.id,
                visible=webinar.is_visible,
                has_certificate=webinar.has_certificate,
            ),
        )
        return

    if data.startswith("admin:webinar:delete:") and not data.startswith("admin:webinar:delete_yes:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.answer("وبینار پیدا نشد.", show_alert=True)
            return
        await query.edit_message_text(
            f"وبینار «{webinar.title}» حذف شود؟\nدکمه‌اش هم از منوی کاربران برداشته می‌شود.",
            reply_markup=confirm_keyboard(
                f"admin:webinar:delete_yes:{webinar_id}",
                f"admin:webinar:view:{webinar_id}",
            ),
        )
        return

    if data.startswith("admin:webinar:delete_yes:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        await delete_webinar(webinar_id)
        await _edit_panel(query)
        if query.message:
            await query.message.reply_text(
                "وبینار حذف شد و دکمه از منو برداشته شد.",
                reply_markup=await main_menu_keyboard(update.effective_user.id),
            )


async def start_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    context.user_data.clear()
    context.user_data["draft"] = {}
    await query.message.reply_text(  # type: ignore[union-attr]
        "نام وبینار را بفرستید.\nهمین نام روی دکمه منو نمایش داده می‌شود.",
        reply_markup=wizard_keyboard(optional=False),
    )
    return ASK_TITLE


async def start_channel_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    context.user_data.clear()
    context.user_data["channel_draft"] = {}
    await query.message.reply_text(  # type: ignore[union-attr]
        "کانال اجباری را بفرستید.\n\n"
        "مثال‌ها:\n"
        "• @mychannel\n"
        "• https://t.me/mychannel\n"
        "• -1001234567890:@mychannel\n"
        "• -1001234567890\n\n"
        "قبل از افزودن، ربات را در آن کانال ادمین کنید.",
        reply_markup=wizard_keyboard(optional=False),
    )
    return ASK_CHANNEL


async def _resolve_channel_with_bot(bot, parsed: dict[str, str | None]) -> dict[str, str | None]:
    chat_id = parsed.get("chat_id")
    if not chat_id:
        raise ValueError("شناسه کانال مشخص نیست.")
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramError as exc:
        raise ValueError(
            "ربات این کانال را پیدا نکرد یا به آن دسترسی ندارد.\n"
            "ربات را ادمین کانال کنید و دوباره تلاش کنید.\n"
            f"جزئیات: {exc}"
        ) from exc

    username = getattr(chat, "username", None) or parsed.get("username")
    title = getattr(chat, "title", None) or parsed.get("title")
    store_chat_id = f"@{username}" if username else str(chat.id)
    invite = parsed.get("invite_link")
    if not invite and username:
        invite = f"https://t.me/{username}"
    return {
        "chat_id": store_chat_id,
        "username": username,
        "title": title,
        "invite_link": invite,
    }


async def _save_channel_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    draft = context.user_data.get("channel_draft") or {}
    if message is None or user is None:
        return ConversationHandler.END
    try:
        channel = await create_required_channel(
            chat_id=str(draft["chat_id"]),
            username=draft.get("username"),
            title=draft.get("title"),
            invite_link=draft.get("invite_link"),
        )
    except (KeyError, ValueError) as exc:
        await message.reply_text(str(exc) if str(exc) else "ثبت کانال ناموفق بود.")
        return ConversationHandler.END

    context.user_data.clear()
    await message.reply_text(
        f"کانال «{channel_label(channel)}» به لیست عضویت اجباری اضافه شد.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    await message.reply_text(
        _channel_view_text(channel),
        reply_markup=channel_manage_keyboard(channel.id),
    )
    return ConversationHandler.END


async def receive_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_CHANNEL
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        parsed = parse_channel_input(message.text)
        resolved = await _resolve_channel_with_bot(context.bot, parsed)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=False))
        return ASK_CHANNEL

    context.user_data["channel_draft"] = resolved
    if resolved.get("invite_link"):
        return await _save_channel_draft(update, context)

    await message.reply_text(
        "لینک عضویت این کانال را بفرستید (مثلاً لینک دعوت خصوصی).\n"
        "بدون لینک، دکمه عضویت برای کاربر ساخته نمی‌شود.",
        reply_markup=wizard_keyboard(optional=False),
    )
    return ASK_CHANNEL_INVITE


async def receive_channel_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_CHANNEL_INVITE
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        invite = normalize_invite_link(message.text)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=False))
        return ASK_CHANNEL_INVITE
    draft = context.user_data.setdefault("channel_draft", {})
    draft["invite_link"] = invite
    return await _save_channel_draft(update, context)


async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    # admin:webinar:edit:<id>:<field>
    parts = query.data.split(":")
    field = parts[-1]
    webinar_id = int(parts[-2])
    if field not in EDIT_PROMPTS:
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["edit_id"] = webinar_id
    context.user_data["edit_field"] = field
    optional = field != "title"
    await query.message.reply_text(  # type: ignore[union-attr]
        EDIT_PROMPTS[field],
        reply_markup=wizard_keyboard(optional=optional),
    )
    return EDIT_VALUE


async def _maybe_leave_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    message = update.effective_message
    if message is None or message.text is None:
        return None
    text = message.text.strip()
    if _is_cancel(text):
        return await _cancel_conversation(update, context)
    if text == BTN_MANAGE:
        context.user_data.clear()
        await open_manage_panel(update, context)
        return ConversationHandler.END
    return None


async def _cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user = update.effective_user
    message = update.effective_message
    if message is not None and user is not None:
        await message.reply_text(
            "انصراف داده شد.",
            reply_markup=await main_menu_keyboard(user.id),
        )
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _cancel_conversation(update, context)


async def cancel_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    from bot.handlers.start import start_command

    await start_command(update, context)
    return ConversationHandler.END


async def fallback_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await panel_callback(update, context)
    return ConversationHandler.END


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_TITLE
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        title = normalize_title(message.text)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=False))
        return ASK_TITLE
    context.user_data.setdefault("draft", {})["title"] = title
    await message.reply_text(
        "ساعت برگزاری را بفرستید (مثلاً 21:00).\nاگر لازم نیست «رد شدن» را بزنید.",
        reply_markup=wizard_keyboard(optional=True),
    )
    return ASK_TIME


async def receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_TIME
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        time_text = None if _is_skip(message.text) else normalize_optional(message.text, max_len=64)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=True))
        return ASK_TIME
    context.user_data.setdefault("draft", {})["time_text"] = time_text
    await message.reply_text(
        "جزئیات را بفرستید (مثلاً نحوه ورود).\nاگر لازم نیست «رد شدن» را بزنید.",
        reply_markup=wizard_keyboard(optional=True),
    )
    return ASK_DETAILS


async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_DETAILS
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        details = None if _is_skip(message.text) else normalize_optional(message.text, max_len=DETAILS_MAX)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=True))
        return ASK_DETAILS
    context.user_data.setdefault("draft", {})["details"] = details
    await message.reply_text(
        "لینک ورود را بفرستید (با https://).\nاگر هنوز آماده نیست «رد شدن» را بزنید؛ بعداً می‌توانید اضافه کنید.",
        reply_markup=wizard_keyboard(optional=True),
    )
    return ASK_LINK


async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return ASK_LINK
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        link = None if _is_skip(message.text) else normalize_link(message.text)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=True))
        return ASK_LINK

    context.user_data.setdefault("draft", {})["link"] = link
    await message.reply_text(
        "لینک گروه وبینار را بفرستید (با https://).\n"
        "این لینک در آخرین مرحله ثبت‌نام (با مدرک یا بدون مدرک) به کاربر داده می‌شود.\n"
        "اگر هنوز آماده نیست «رد شدن» را بزنید.",
        reply_markup=wizard_keyboard(optional=True),
    )
    return ASK_GROUP_LINK


async def receive_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return ASK_GROUP_LINK
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        group_link = None if _is_skip(message.text) else normalize_link(message.text)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=True))
        return ASK_GROUP_LINK

    context.user_data.setdefault("draft", {})["group_link"] = group_link
    await message.reply_text(
        "آیا این وبینار گزینه «با مدرک» دارد؟",
        reply_markup=ReplyKeyboardMarkup(
            [[BTN_CERT_YES], [BTN_CERT_NO], [BTN_CANCEL]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return ASK_HAS_CERT


async def receive_has_cert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return ASK_HAS_CERT
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    text = message.text.strip()
    if text == BTN_CERT_YES:
        context.user_data.setdefault("draft", {})["has_certificate"] = True
        await message.reply_text(
            "مبلغ مدرک را بفرستید (مثلاً ۱۵۰٬۰۰۰ تومان).",
            reply_markup=wizard_keyboard(optional=True),
        )
        return ASK_PRICE
    if text == BTN_CERT_NO:
        context.user_data.setdefault("draft", {})["has_certificate"] = False
        context.user_data.setdefault("draft", {})["certificate_price"] = None
        return await _finish_webinar_create(update, context)
    await message.reply_text(
        "یکی از گزینه‌های کیبورد را انتخاب کنید.",
        reply_markup=ReplyKeyboardMarkup(
            [[BTN_CERT_YES], [BTN_CERT_NO], [BTN_CANCEL]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return ASK_HAS_CERT


async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_PRICE
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    try:
        price = None if _is_skip(message.text) else normalize_optional(message.text, max_len=120)
    except ValueError as exc:
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=True))
        return ASK_PRICE
    context.user_data.setdefault("draft", {})["certificate_price"] = price
    return await _finish_webinar_create(update, context)


async def _finish_webinar_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return ConversationHandler.END
    draft = context.user_data.get("draft") or {}
    try:
        webinar = await create_webinar(
            title=draft["title"],
            time_text=draft.get("time_text"),
            details=draft.get("details"),
            link=draft.get("link"),
            is_visible=True,
            has_certificate=bool(draft.get("has_certificate")),
            certificate_price=draft.get("certificate_price"),
            group_link=draft.get("group_link"),
        )
    except KeyError:
        await message.reply_text("ثبت وبینار ناموفق بود. دوباره از مدیریت منو شروع کنید.")
        return ConversationHandler.END
    except ValueError as exc:
        await message.reply_text(
            f"{exc}\nنام دیگری بفرستید:",
            reply_markup=wizard_keyboard(optional=False),
        )
        return ASK_TITLE

    context.user_data.clear()
    await message.reply_text(
        "وبینار ثبت شد و دکمه‌اش به منوی کاربران اضافه شد.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    await message.reply_text(
        _webinar_view_text(webinar),
        reply_markup=webinar_manage_keyboard(
            webinar.id,
            visible=webinar.is_visible,
            has_certificate=webinar.has_certificate,
        ),
    )
    return ConversationHandler.END


async def start_payment_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(  # type: ignore[union-attr]
        "شماره کارت را بفرستید (فقط عدد، با یا بدون خط تیره).",
        reply_markup=wizard_keyboard(optional=False),
    )
    return ASK_PAYMENT_CARD


async def start_gift_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(  # type: ignore[union-attr]
        "فایل هدیه را به‌صورت Document (مثلاً PDF) همین‌جا بفرستید.\n"
        "برای انصراف «انصراف» را بزنید.",
        reply_markup=wizard_keyboard(optional=False),
    )
    return ASK_GIFT_FILE


async def receive_gift_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return ASK_GIFT_FILE

    if message.text:
        left = await _maybe_leave_wizard(update, context)
        if left is not None:
            return left
        await message.reply_text(
            "لطفاً خود فایل را ارسال کنید (نه متن).",
            reply_markup=wizard_keyboard(optional=False),
        )
        return ASK_GIFT_FILE

    document = message.document
    if document is None:
        await message.reply_text(
            "فقط فایل Document پشتیبانی می‌شود (مثلاً PDF).",
            reply_markup=wizard_keyboard(optional=False),
        )
        return ASK_GIFT_FILE

    filename = document.file_name or f"gift_{document.file_unique_id}.bin"
    try:
        saved = await save_telegram_document(
            context.bot,
            file_id=document.file_id,
            filename=filename,
        )
    except Exception as exc:
        logger.exception("Gift upload failed")
        await message.reply_text(
            f"آپلود ناموفق بود.\n{exc}",
            reply_markup=wizard_keyboard(optional=False),
        )
        return ASK_GIFT_FILE

    context.user_data.clear()
    await message.reply_text(
        f"فایل «{saved.name}» به فایل‌های هدیه اضافه شد.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    await message.reply_text(
        "🎁 مدیریت فایل‌های هدیه\n\n"
        f"تعداد فایل‌ها: {len(list_gift_files())}",
        reply_markup=gift_files_keyboard(),
    )
    return ConversationHandler.END


async def receive_payment_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ASK_PAYMENT_CARD
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    number = re.sub(r"\s+", "", message.text.strip())
    if len(re.sub(r"\D", "", number)) < 12:
        await message.reply_text(
            "شماره کارت معتبر به نظر نمی‌رسد. دوباره بفرستید.",
            reply_markup=wizard_keyboard(optional=False),
        )
        return ASK_PAYMENT_CARD
    context.user_data["payment_card_number"] = number
    await message.reply_text(
        "نام صاحب کارت را بفرستید (یا «رد شدن»).",
        reply_markup=wizard_keyboard(optional=True),
    )
    return ASK_PAYMENT_HOLDER


async def receive_payment_holder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return ASK_PAYMENT_HOLDER
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left
    holder = None if _is_skip(message.text) else message.text.strip()
    number = context.user_data.get("payment_card_number")
    if not number:
        await message.reply_text("شماره کارت پیدا نشد. دوباره از تنظیمات پرداخت شروع کنید.")
        return ConversationHandler.END
    await set_payment_card(number=str(number), holder=holder)
    context.user_data.clear()
    await message.reply_text(
        "تنظیمات پرداخت ذخیره شد.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    await _show_panel_message(message, user_id=user.id)
    return ConversationHandler.END


async def receive_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return EDIT_VALUE
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left

    webinar_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")
    if not isinstance(webinar_id, int) or field not in EDIT_PROMPTS:
        await message.reply_text("ویرایش نامعتبر است. دوباره از مدیریت منو شروع کنید.")
        return ConversationHandler.END

    try:
        if field == "title":
            value: object = normalize_title(message.text)
        elif field == "time":
            value = None if _is_skip(message.text) else normalize_optional(message.text, max_len=64)
            field = "time_text"
        elif field == "details":
            value = None if _is_skip(message.text) else normalize_optional(message.text, max_len=DETAILS_MAX)
        elif field == "price":
            value = None if _is_skip(message.text) else normalize_optional(message.text, max_len=120)
            field = "certificate_price"
        elif field == "group":
            value = None if _is_skip(message.text) else normalize_link(message.text)
            field = "group_link"
        else:
            value = None if _is_skip(message.text) else normalize_link(message.text)
            field = "link"
        webinar = await update_webinar(webinar_id, **{field: value})
    except ValueError as exc:
        optional = context.user_data.get("edit_field") != "title"
        await message.reply_text(str(exc), reply_markup=wizard_keyboard(optional=optional))
        return EDIT_VALUE

    context.user_data.clear()
    await message.reply_text(
        "ذخیره شد.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    await message.reply_text(
        _webinar_view_text(webinar),
        reply_markup=webinar_manage_keyboard(
            webinar.id,
            visible=webinar.is_visible,
            has_certificate=webinar.has_certificate,
        ),
    )
    return ConversationHandler.END



# --- Broadcast (پیام همگانی) ---


def _broadcast_audience_label(draft: dict) -> str:
    kind = draft.get("audience")
    if kind == "all":
        return "همه اعضای ربات"
    webinar_title = draft.get("webinar_title") or "وبینار"
    segment = draft.get("segment", "approved")
    seg_label = BROADCAST_SEGMENTS.get(segment, segment)
    return f"{seg_label} · «{webinar_title}»"


async def _resolve_broadcast_ids(draft: dict) -> list[int]:
    kind = draft.get("audience")
    if kind == "all":
        return await list_all_telegram_ids()
    webinar_id = draft.get("webinar_id")
    if not isinstance(webinar_id, int):
        return []
    segment = draft.get("segment", "approved")
    if segment == "all":
        return await list_registration_telegram_ids(webinar_id)
    if segment == "pending":
        return await list_registration_telegram_ids(
            webinar_id,
            status=RegistrationStatus.PENDING_REVIEW.value,
        )
    return await list_registration_telegram_ids(
        webinar_id,
        status=RegistrationStatus.APPROVED.value,
    )


async def broadcast_text_to_users(bot, telegram_ids: list[int], text: str) -> tuple[int, int]:
    sent = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await bot.send_message(chat_id=telegram_id, text=text)
            sent += 1
        except TelegramError as exc:
            failed += 1
            logger.warning("Broadcast failed for %s: %s", telegram_id, exc)
        await asyncio.sleep(0.05)
    return sent, failed


async def _prompt_broadcast_text(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get("broadcast") or {}
    label = _broadcast_audience_label(draft)
    await query.message.reply_text(  # type: ignore[union-attr]
        f"مخاطب: {label}\n\n"
        "متن پیام همگانی را بفرستید.\n"
        "برای انصراف «انصراف» را بزنید.",
        reply_markup=wizard_keyboard(optional=False),
    )
    return BROADCAST_TEXT


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    context.user_data.clear()
    context.user_data["broadcast"] = {}
    await query.edit_message_text(
        "📢 پیام همگانی\n\nمخاطبان هدف را انتخاب کنید:",
        reply_markup=broadcast_audience_keyboard(),
    )
    return BROADCAST_PICK


async def broadcast_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END

    data = query.data
    await query.answer()

    if data == "admin:panel":
        context.user_data.clear()
        await panel_callback(update, context)
        return ConversationHandler.END

    if data == "admin:broadcast":
        context.user_data["broadcast"] = {}
        await query.edit_message_text(
            "📢 پیام همگانی\n\nمخاطبان هدف را انتخاب کنید:",
            reply_markup=broadcast_audience_keyboard(),
        )
        return BROADCAST_PICK

    if data == "admin:broadcast:aud:all":
        context.user_data["broadcast"] = {"audience": "all"}
        return await _prompt_broadcast_text(query, context)

    if data == "admin:broadcast:aud:webinar":
        webinars_kb = await broadcast_webinar_pick_keyboard()
        if len(webinars_kb.inline_keyboard) <= 1:
            await query.edit_message_text(
                "هنوز وبیناری ثبت نشده است.",
                reply_markup=broadcast_audience_keyboard(),
            )
            return BROADCAST_PICK
        await query.edit_message_text(
            "وبینار مورد نظر را انتخاب کنید:",
            reply_markup=webinars_kb,
        )
        return BROADCAST_PICK

    if data.startswith("admin:broadcast:wb:"):
        webinar_id = int(data.rsplit(":", 1)[-1])
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.edit_message_text(
                "وبینار پیدا نشد.",
                reply_markup=broadcast_audience_keyboard(),
            )
            return BROADCAST_PICK
        context.user_data["broadcast"] = {
            "audience": "webinar",
            "webinar_id": webinar.id,
            "webinar_title": webinar.title,
        }
        await query.edit_message_text(
            f"مخاطبان «{webinar.title}»:\nکدام گروه؟",
            reply_markup=broadcast_segment_keyboard(webinar.id),
        )
        return BROADCAST_PICK

    if data.startswith("admin:broadcast:seg:"):
        parts = data.split(":")
        if len(parts) < 5:
            return BROADCAST_PICK
        webinar_id = int(parts[3])
        segment = parts[4]
        if segment not in BROADCAST_SEGMENTS:
            return BROADCAST_PICK
        webinar = await get_webinar(webinar_id)
        if webinar is None:
            await query.edit_message_text(
                "وبینار پیدا نشد.",
                reply_markup=broadcast_audience_keyboard(),
            )
            return BROADCAST_PICK
        context.user_data["broadcast"] = {
            "audience": "webinar",
            "webinar_id": webinar.id,
            "webinar_title": webinar.title,
            "segment": segment,
        }
        return await _prompt_broadcast_text(query, context)

    return BROADCAST_PICK


async def receive_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return BROADCAST_TEXT
    left = await _maybe_leave_wizard(update, context)
    if left is not None:
        return left

    text = message.text.strip()
    if not text:
        await message.reply_text(
            "متن پیام نمی‌تواند خالی باشد.",
            reply_markup=wizard_keyboard(optional=False),
        )
        return BROADCAST_TEXT
    if len(text) > BROADCAST_TEXT_MAX:
        await message.reply_text(
            f"متن پیام حداکثر {BROADCAST_TEXT_MAX} کاراکتر باشد.",
            reply_markup=wizard_keyboard(optional=False),
        )
        return BROADCAST_TEXT

    draft = context.user_data.setdefault("broadcast", {})
    draft["text"] = text
    recipients = await _resolve_broadcast_ids(draft)
    draft["recipient_count"] = len(recipients)
    label = _broadcast_audience_label(draft)
    preview = text if len(text) <= 500 else text[:500] + "…"

    await message.reply_text(
        f"پیش‌نمایش پیام همگانی\n\n"
        f"مخاطب: {label}\n"
        f"تعداد گیرندگان: {len(recipients)} نفر\n\n"
        f"———\n{preview}\n———\n\n"
        "ارسال شود؟",
        reply_markup=confirm_keyboard("admin:broadcast:yes", "admin:broadcast:no"),
    )
    return BROADCAST_CONFIRM


async def broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return ConversationHandler.END
    if not _is_admin(update):
        await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    data = query.data
    user = update.effective_user

    if data in {"admin:broadcast:no", "admin:panel", "admin:broadcast"}:
        context.user_data.clear()
        if data == "admin:panel":
            await panel_callback(update, context)
        else:
            await query.edit_message_text("ارسال پیام همگانی لغو شد.")
            if query.message:
                await query.message.reply_text(
                    PANEL_TEXT,
                    reply_markup=await admin_panel_keyboard(),
                )
        return ConversationHandler.END

    if data != "admin:broadcast:yes":
        return BROADCAST_CONFIRM

    draft = context.user_data.get("broadcast") or {}
    text = draft.get("text")
    if not text:
        await query.edit_message_text("متن پیام پیدا نشد. دوباره از پیام همگانی شروع کنید.")
        context.user_data.clear()
        return ConversationHandler.END

    recipients = await _resolve_broadcast_ids(draft)
    label = _broadcast_audience_label(draft)
    await query.edit_message_text(
        f"در حال ارسال به {len(recipients)} نفر ({label})…"
    )
    sent, failed = await broadcast_text_to_users(context.bot, recipients, text)
    context.user_data.clear()
    await query.message.reply_text(  # type: ignore[union-attr]
        f"پیام همگانی تمام شد.\n"
        f"مخاطب: {label}\n"
        f"موفق: {sent}\n"
        f"ناموفق: {failed}",
        reply_markup=await main_menu_keyboard(user.id),
    )
    if query.message:
        await query.message.reply_text(
            PANEL_TEXT,
            reply_markup=await admin_panel_keyboard(),
        )
    return ConversationHandler.END



def build_conversation() -> ConversationHandler:
    cancel_filters = filters.Regex(f"^{re.escape(BTN_CANCEL)}$") | filters.Regex(
        f"^{re.escape(BTN_MANAGE)}$"
    )
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_create, pattern=r"^admin:webinar:new$"),
            CallbackQueryHandler(start_channel_create, pattern=r"^admin:channel:new$"),
            CallbackQueryHandler(start_payment_edit, pattern=r"^admin:payment:edit$"),
            CallbackQueryHandler(start_gift_upload, pattern=r"^admin:gifts:upload$"),
            CallbackQueryHandler(start_broadcast, pattern=r"^admin:broadcast$"),
            CallbackQueryHandler(
                start_edit,
                pattern=r"^admin:webinar:edit:\d+:(title|time|details|link|group|price)$",
            ),
        ],
        states={
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time)],
            ASK_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_details)],
            ASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link)],
            ASK_GROUP_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_link)
            ],
            ASK_HAS_CERT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_has_cert)],
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit)],
            ASK_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel)],
            ASK_CHANNEL_INVITE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_invite)
            ],
            ASK_PAYMENT_CARD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_card)
            ],
            ASK_PAYMENT_HOLDER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_holder)
            ],
            ASK_GIFT_FILE: [
                MessageHandler(filters.Document.ALL & ~filters.COMMAND, receive_gift_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gift_file),
            ],
            BROADCAST_PICK: [
                CallbackQueryHandler(broadcast_pick_callback, pattern=BROADCAST_CALLBACK_PATTERN),
                CallbackQueryHandler(broadcast_pick_callback, pattern=r"^admin:panel$"),
            ],
            BROADCAST_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_broadcast_text),
            ],
            BROADCAST_CONFIRM: [
                CallbackQueryHandler(
                    broadcast_confirm_callback,
                    pattern=r"^admin:broadcast:(yes|no)$",
                ),
                CallbackQueryHandler(broadcast_confirm_callback, pattern=r"^admin:panel$"),
                CallbackQueryHandler(broadcast_confirm_callback, pattern=r"^admin:broadcast$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CommandHandler("start", cancel_and_start),
            MessageHandler(cancel_filters, cancel_command),
            CallbackQueryHandler(fallback_admin_callback, pattern=ADMIN_CALLBACK_PATTERN),
        ],
        allow_reentry=True,
    )


def register(application: Application) -> None:
    application.add_handler(build_conversation())
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{re.escape(BTN_MANAGE)}$"),
            open_manage_panel,
        )
    )
    application.add_handler(
        CallbackQueryHandler(panel_callback, pattern=ADMIN_CALLBACK_PATTERN)
    )
