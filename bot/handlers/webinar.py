"""Gated webinar registration: free / certificate + receipt review."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import get_settings
from bot.database.models import RegistrationKind, RegistrationStatus, User, WebinarLinkClaim
from bot.database.session import get_session
from bot.handlers.membership import handle_membership_check, require_membership_or_prompt
from bot.utils.buttons import LEGACY_WEBINAR_BTN
from bot.utils.keyboards import main_menu_keyboard
from bot.utils.payment import format_payment_instructions, get_payment_card
from bot.utils.registrations import (
    KIND_LABELS,
    STATUS_LABELS,
    get_registration,
    get_registration_by_id,
    list_approved_telegram_ids,
    registration_summary,
    set_registration_status,
    upsert_registration,
)
from bot.utils.users import get_or_create_user
from bot.utils.webinars import (
    build_webinar_message,
    find_webinar_by_button,
    get_webinar,
    list_visible_webinars,
    update_webinar,
)

logger = logging.getLogger(__name__)

VERIFY_PREFIX = "check_membership:webinar:"
AWAITING_RECEIPT_KEY = "awaiting_receipt_webinar_id"
AWAITING_NAME_KEY = "awaiting_webinar_name_id"
AWAITING_CERT_INFO_KEY = "awaiting_cert_info_webinar_id"
CERT_INFO_STEP_KEY = "cert_info_step"
CERT_INFO_DRAFT_KEY = "cert_info_draft"

CERT_INFO_STEPS = ("name_fa", "name_en", "national_id", "phone")
CERT_INFO_PROMPTS = {
    "name_fa": "نام و نام‌خانوادگی به فارسی را وارد کنید:",
    "name_en": "نام و نام‌خانوادگی به انگلیسی را وارد کنید:",
    "national_id": "کد ملی را وارد کنید (۱۰ رقم):",
    "phone": "شماره تماس را وارد کنید (مثلاً 09121234567):",
}

GATE_TEXT = (
    "برای ثبت‌نام در وبینار، ابتدا در کانال‌های زیر عضو شوید "
    "و سپس روی دکمه «عضو شدم، بررسی کن» بزنید."
)


def _cert_interest_keyboard(webinar_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "بله، مایل به دریافت مدرک هستم",
                    callback_data=f"wb:reg:{webinar_id}:cert",
                )
            ],
            [
                InlineKeyboardButton(
                    "خیر",
                    callback_data=f"wb:reg:{webinar_id}:free",
                )
            ],
        ]
    )


def _receipt_review_keyboard(registration_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تایید پرداخت",
                    callback_data=f"wb:pay:ok:{registration_id}",
                ),
                InlineKeyboardButton(
                    "❌ رد رسید",
                    callback_data=f"wb:pay:no:{registration_id}",
                ),
            ]
        ]
    )


def _registration_admin_keyboard(registration_id: int, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status != RegistrationStatus.APPROVED.value:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ تایید",
                    callback_data=f"wb:pay:ok:{registration_id}",
                )
            ]
        )
    if status != RegistrationStatus.REJECTED.value:
        rows.append(
            [
                InlineKeyboardButton(
                    "❌ نامعتبر",
                    callback_data=f"wb:pay:no:{registration_id}",
                )
            ]
        )
    if status == RegistrationStatus.REJECTED.value:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔄 بازگشت به انتظار پرداخت",
                    callback_data=f"wb:pay:reset:{registration_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


async def _record_claim(tg_user, webinar_id: int) -> None:
    async with get_session() as session:
        db_user = await get_or_create_user(session, tg_user)
        existing = await session.execute(
            select(WebinarLinkClaim).where(
                WebinarLinkClaim.user_id == db_user.id,
                WebinarLinkClaim.webinar_id == webinar_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(WebinarLinkClaim(user_id=db_user.id, webinar_id=webinar_id))


async def _thanks_without_cert_message(webinar) -> str:
    return (
        f"سپاسگزاریم «{webinar.title}».\n"
        "قبل از شروع جلسه لینک ورود براتون ارسال می‌شه."
    )


async def _send_group_link_if_any(message, webinar) -> None:
    if not webinar.group_link:
        return
    await message.reply_text(
        f"لینک گروه وبینار «{webinar.title}»:\n{webinar.group_link}\n\n"
        "لطفاً وارد گروه شوید."
    )


async def _continue_after_name(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user,
    webinar,
) -> None:
    if webinar.has_certificate:
        await message.reply_text(
            "مایل به ثبت‌نام برای دریافت مدرک هستید؟",
            reply_markup=_cert_interest_keyboard(webinar.id),
        )
        return

    await upsert_registration(
        user,
        webinar_id=webinar.id,
        kind=RegistrationKind.FREE.value,
        status=RegistrationStatus.APPROVED.value,
        registrant_name=context.user_data.get("webinar_registrant_name"),
    )
    await message.reply_text(
        await _thanks_without_cert_message(webinar),
        reply_markup=await main_menu_keyboard(user.id),
    )
    await _send_group_link_if_any(message, webinar)


async def _start_registration_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    webinar_id: int,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    webinar = await get_webinar(webinar_id)
    if webinar is None or not webinar.is_visible:
        await message.reply_text(
            "این وبینار دیگر در دسترس نیست.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return

    async with get_session() as session:
        db_user = await get_or_create_user(session, user)
        user_pk = db_user.id

    existing = await get_registration(user_pk, webinar_id)
    if existing and existing.status == RegistrationStatus.APPROVED.value:
        await message.reply_text(
            f"شما قبلاً برای «{webinar.title}» ثبت‌نام شده‌اید.\n"
            + (
                "قبل از شروع جلسه لینک ورود براتون ارسال می‌شه."
            ),
            reply_markup=await main_menu_keyboard(user.id),
        )
        if webinar.group_link:
            await message.reply_text(f"لینک گروه:\n{webinar.group_link}")
        return
    if existing and existing.status == RegistrationStatus.PENDING_REVIEW.value:
        await message.reply_text(
            "رسید شما دریافت شده و در انتظار بررسی ادمین است.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return
    if existing and existing.status == RegistrationStatus.PENDING_PAYMENT.value:
        if existing.registrant_name:
            context.user_data["webinar_registrant_name"] = existing.registrant_name
        # Resume certificate info form if user already sent receipt.
        if (
            context.user_data.get(AWAITING_CERT_INFO_KEY) == webinar_id
            and context.user_data.get(CERT_INFO_DRAFT_KEY)
        ):
            step = context.user_data.get(CERT_INFO_STEP_KEY) or "name_fa"
            await message.reply_text(
                "لطفاً اطلاعات مدرک را کامل کنید.\n"
                f"{CERT_INFO_PROMPTS.get(step, CERT_INFO_PROMPTS['name_fa'])}"
            )
            return
        await _prompt_payment(message, context, webinar)
        return
    if existing and existing.status == RegistrationStatus.REJECTED.value:
        await message.reply_text(
            "ثبت‌نام قبلی شما نامعتبر اعلام شده است.\n"
            "برای پیگیری با پشتیبانی در ارتباط باشید؛ "
            "یا اگر ادمین وضعیت را باز کند می‌توانید دوباره اقدام کنید.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return

    context.user_data.pop(AWAITING_RECEIPT_KEY, None)
    context.user_data[AWAITING_NAME_KEY] = webinar_id
    await message.reply_text(
        f"ثبت‌نام وبینار «{webinar.title}»\n\n"
        "لطفاً نام و نام‌خانوادگی خودتان را وارد کنید:"
    )


async def handle_webinar_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    webinar_id = context.user_data.get(AWAITING_NAME_KEY)
    if not webinar_id:
        return

    message = update.message
    user = update.effective_user
    if message is None or user is None or not message.text:
        return

    from bot.utils.buttons import BTN_GIFT, BTN_MANAGE, BTN_STATS, BTN_SUPPORT

    text = message.text.strip()
    if text in {BTN_GIFT, BTN_SUPPORT, BTN_STATS, BTN_MANAGE} or text.startswith("🔗 "):
        return

    if len(text) < 2:
        await message.reply_text("نام معتبر وارد کنید.")
        raise ApplicationHandlerStop

    webinar = await get_webinar(int(webinar_id))
    if webinar is None or not webinar.is_visible:
        context.user_data.pop(AWAITING_NAME_KEY, None)
        await message.reply_text(
            "این وبینار دیگر در دسترس نیست.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        raise ApplicationHandlerStop

    context.user_data.pop(AWAITING_NAME_KEY, None)
    context.user_data["webinar_registrant_name"] = text[:255]
    await _continue_after_name(message, context, user=user, webinar=webinar)
    raise ApplicationHandlerStop


async def _prompt_payment(message, context: ContextTypes.DEFAULT_TYPE, webinar) -> None:
    card_number, card_holder = await get_payment_card()
    if not card_number:
        await message.reply_text(
            "ثبت‌نام با مدرک فعلاً ممکن نیست؛ تنظیمات پرداخت کامل نیست. با پشتیبانی تماس بگیرید.",
            reply_markup=await main_menu_keyboard(message.chat_id),
        )
        return
    context.user_data.pop(AWAITING_CERT_INFO_KEY, None)
    context.user_data.pop(CERT_INFO_STEP_KEY, None)
    context.user_data.pop(CERT_INFO_DRAFT_KEY, None)
    context.user_data[AWAITING_RECEIPT_KEY] = webinar.id
    text = format_payment_instructions(
        price=webinar.certificate_price,
        card_number=card_number,
        card_holder=card_holder,
    )
    await message.reply_text(text, reply_markup=await main_menu_keyboard(message.chat_id))


async def send_webinar_gate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    webinar_id: int,
) -> None:
    webinar = await get_webinar(webinar_id)
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if webinar is None or not webinar.is_visible:
        await message.reply_text(
            "این وبینار فعلاً فعال نیست.",
            reply_markup=await main_menu_keyboard(user.id),
        )
        return

    async def on_success(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _start_registration_flow(upd, ctx, webinar_id)

    await require_membership_or_prompt(
        update,
        context,
        check_callback=f"{VERIFY_PREFIX}{webinar_id}",
        intro_text=GATE_TEXT,
        on_success=on_success,
    )


async def webinar_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None or update.effective_user is None:
        return

    text = update.message.text.strip()
    webinar = await find_webinar_by_button(text)
    if webinar is None and text == LEGACY_WEBINAR_BTN:
        visible = await list_visible_webinars()
        if len(visible) == 1:
            webinar = visible[0]
        elif not visible:
            await update.message.reply_text(
                "در حال حاضر وبینار فعالی وجود ندارد.",
                reply_markup=await main_menu_keyboard(update.effective_user.id),
            )
            return
        else:
            await update.message.reply_text(
                "چند وبینار فعال است. از منوی زیر یکی را انتخاب کنید.",
                reply_markup=await main_menu_keyboard(update.effective_user.id),
            )
            return

    if webinar is None:
        await update.message.reply_text(
            "این گزینه دیگر فعال نیست.",
            reply_markup=await main_menu_keyboard(update.effective_user.id),
        )
        return

    await send_webinar_gate(update, context, webinar.id)


async def verify_webinar_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    webinar_id = int(query.data.rsplit(":", 1)[-1])

    async def on_success(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _start_registration_flow(upd, ctx, webinar_id)

    await handle_membership_check(
        update,
        context,
        check_callback=f"{VERIFY_PREFIX}{webinar_id}",
        on_success=on_success,
    )


async def registration_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or query.data is None or user is None or query.message is None:
        return

    # wb:reg:<webinar_id>:free|cert
    parts = query.data.split(":")
    webinar_id = int(parts[2])
    choice = parts[3]
    webinar = await get_webinar(webinar_id)
    if webinar is None or not webinar.is_visible:
        await query.answer("وبینار در دسترس نیست.", show_alert=True)
        return

    async with get_session() as session:
        db_user = await get_or_create_user(session, user)
        user_pk = db_user.id
    existing = await get_registration(user_pk, webinar_id)
    if existing and existing.status in {
        RegistrationStatus.APPROVED.value,
        RegistrationStatus.PENDING_REVIEW.value,
        RegistrationStatus.PENDING_PAYMENT.value,
        RegistrationStatus.REJECTED.value,
    }:
        await query.answer("قبلاً پاسخ داده‌اید.", show_alert=True)
        try:
            await query.message.delete()
        except TelegramError:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass
        return

    await query.answer()
    try:
        await query.message.delete()
    except TelegramError:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass

    registrant_name = context.user_data.get("webinar_registrant_name")
    if choice == "free":
        await upsert_registration(
            user,
            webinar_id=webinar_id,
            kind=RegistrationKind.FREE.value,
            status=RegistrationStatus.APPROVED.value,
            registrant_name=registrant_name,
        )
        context.user_data.pop(AWAITING_RECEIPT_KEY, None)
        await query.message.reply_text(
            await _thanks_without_cert_message(webinar),
            reply_markup=await main_menu_keyboard(user.id),
        )
        await _send_group_link_if_any(query.message, webinar)
        return

    if not webinar.has_certificate:
        await query.message.reply_text("این وبینار گزینه مدرک ندارد.")
        return

    await upsert_registration(
        user,
        webinar_id=webinar_id,
        kind=RegistrationKind.CERTIFICATE.value,
        status=RegistrationStatus.PENDING_PAYMENT.value,
        registrant_name=registrant_name,
    )
    await _prompt_payment(query.message, context, webinar)


async def handle_receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    webinar_id = context.user_data.get(AWAITING_RECEIPT_KEY)
    if not webinar_id:
        return

    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    file_id = None
    file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        await message.reply_text("لطفاً عکس یا فایل رسید را ارسال کنید.")
        raise ApplicationHandlerStop

    webinar = await get_webinar(int(webinar_id))
    if webinar is None:
        context.user_data.pop(AWAITING_RECEIPT_KEY, None)
        await message.reply_text("وبینار پیدا نشد.")
        raise ApplicationHandlerStop

    context.user_data.pop(AWAITING_RECEIPT_KEY, None)
    context.user_data[AWAITING_CERT_INFO_KEY] = webinar.id
    context.user_data[CERT_INFO_STEP_KEY] = "name_fa"
    context.user_data[CERT_INFO_DRAFT_KEY] = {
        "receipt_file_id": file_id,
        "receipt_file_type": file_type,
    }

    await message.reply_text(
        "فیش دریافت شد.\n\n"
        "حالا اطلاعات لازم برای صدور مدرک را وارد کنید.\n"
        f"{CERT_INFO_PROMPTS['name_fa']}"
    )
    raise ApplicationHandlerStop


def _normalize_phone(text: str) -> str | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if len(digits) == 11 and digits.startswith("09"):
        return digits
    return None


def _validate_cert_info_step(step: str, text: str) -> tuple[str | None, str | None]:
    value = " ".join(text.strip().split())
    if step == "name_fa":
        if len(value) < 3:
            return None, "نام فارسی معتبر وارد کنید."
        return value[:255], None
    if step == "name_en":
        if len(value) < 3:
            return None, "نام انگلیسی معتبر وارد کنید."
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ -'.")
        if any(ch not in allowed for ch in value):
            return None, "نام انگلیسی فقط با حروف لاتین وارد شود."
        return value[:255], None
    if step == "national_id":
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) != 10:
            return None, "کد ملی باید ۱۰ رقم باشد."
        return digits, None
    if step == "phone":
        phone = _normalize_phone(value)
        if phone is None:
            return None, "شماره تماس معتبر نیست. مثلاً 09121234567"
        return phone, None
    return None, "مرحله نامعتبر است."


async def _notify_admins_receipt(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reg,
    webinar,
) -> None:
    settings = get_settings()
    file_id = reg.receipt_file_id
    file_type = reg.receipt_file_type
    if not file_id:
        return
    caption = (
        "🧾 رسید پرداخت وبینار\n\n"
        f"{registration_summary(reg)}\n"
        f"وبینار: {webinar.title}\n"
        f"مبلغ: {webinar.certificate_price or '—'}"
    )
    # Telegram caption max ~1024; keep summary compact enough in practice
    if len(caption) > 1000:
        caption = caption[:997] + "..."
    for admin_id in settings.admin_ids:
        try:
            if file_type == "photo":
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption,
                    reply_markup=_receipt_review_keyboard(reg.id),
                )
            else:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=file_id,
                    caption=caption,
                    reply_markup=_receipt_review_keyboard(reg.id),
                )
        except TelegramError as exc:
            logger.error("Failed to notify admin %s about receipt: %s", admin_id, exc)


async def handle_cert_info_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    webinar_id = context.user_data.get(AWAITING_CERT_INFO_KEY)
    if not webinar_id:
        return

    message = update.message
    user = update.effective_user
    if message is None or user is None or not message.text:
        return

    from bot.utils.buttons import BTN_GIFT, BTN_MANAGE, BTN_STATS, BTN_SUPPORT

    text = message.text.strip()
    if text in {BTN_GIFT, BTN_SUPPORT, BTN_STATS, BTN_MANAGE} or text.startswith("🔗 "):
        return

    step = context.user_data.get(CERT_INFO_STEP_KEY) or "name_fa"
    draft = context.user_data.setdefault(CERT_INFO_DRAFT_KEY, {})
    value, error = _validate_cert_info_step(step, text)
    if error or value is None:
        await message.reply_text(error or "مقدار نامعتبر است.")
        raise ApplicationHandlerStop

    draft[step] = value
    try:
        idx = CERT_INFO_STEPS.index(step)
    except ValueError:
        idx = 0

    if idx + 1 < len(CERT_INFO_STEPS):
        next_step = CERT_INFO_STEPS[idx + 1]
        context.user_data[CERT_INFO_STEP_KEY] = next_step
        await message.reply_text(CERT_INFO_PROMPTS[next_step])
        raise ApplicationHandlerStop

    webinar = await get_webinar(int(webinar_id))
    if webinar is None:
        context.user_data.pop(AWAITING_CERT_INFO_KEY, None)
        context.user_data.pop(CERT_INFO_STEP_KEY, None)
        context.user_data.pop(CERT_INFO_DRAFT_KEY, None)
        await message.reply_text("وبینار پیدا نشد.")
        raise ApplicationHandlerStop

    reg = await upsert_registration(
        user,
        webinar_id=webinar.id,
        kind=RegistrationKind.CERTIFICATE.value,
        status=RegistrationStatus.PENDING_REVIEW.value,
        registrant_name=draft.get("name_fa") or context.user_data.get("webinar_registrant_name"),
        name_fa=draft.get("name_fa"),
        name_en=draft.get("name_en"),
        national_id=draft.get("national_id"),
        phone=draft.get("phone"),
        receipt_file_id=draft.get("receipt_file_id"),
        receipt_file_type=draft.get("receipt_file_type"),
    )
    context.user_data.pop(AWAITING_CERT_INFO_KEY, None)
    context.user_data.pop(CERT_INFO_STEP_KEY, None)
    context.user_data.pop(CERT_INFO_DRAFT_KEY, None)

    reg = await get_registration_by_id(reg.id) or reg
    await message.reply_text(
        "اطلاعات و فیش شما دریافت شد و برای بررسی ادمین ارسال شد.\n"
        "پس از تایید، ثبت‌نام با مدرک قطعی می‌شود.",
        reply_markup=await main_menu_keyboard(user.id),
    )
    await _send_group_link_if_any(message, webinar)
    await _notify_admins_receipt(context, reg=reg, webinar=webinar)
    raise ApplicationHandlerStop


async def payment_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin = update.effective_user
    if query is None or query.data is None or admin is None:
        return
    if not get_settings().is_admin(admin.id):
        await query.answer("فقط ادمین.", show_alert=True)
        return

    # wb:pay:ok|no|reset:<registration_id>
    parts = query.data.split(":")
    action = parts[2]
    registration_id = int(parts[3])
    reg = await get_registration_by_id(registration_id)
    if reg is None:
        await query.answer("ثبت‌نام پیدا نشد.", show_alert=True)
        return

    webinar = reg.webinar
    user = reg.user
    if user is None:
        await query.answer("کاربر پیدا نشد.", show_alert=True)
        return

    if action == "ok":
        await set_registration_status(
            registration_id,
            status=RegistrationStatus.APPROVED.value,
            admin_telegram_id=admin.id,
        )
        await query.answer("تایید شد")
        try:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"✅ پرداخت شما برای وبینار «{webinar.title if webinar else ''}» تایید شد.\n"
                    "ثبت‌نام با مدرک انجام شد.\n"
                    "قبل از شروع جلسه لینک ورود براتون ارسال می‌شه."
                ),
            )
        except TelegramError as exc:
            logger.warning("Notify approved user failed: %s", exc)
        note = f"وضعیت ثبت‌نام #{registration_id}: تایید شد."
    elif action == "no":
        await set_registration_status(
            registration_id,
            status=RegistrationStatus.REJECTED.value,
            admin_telegram_id=admin.id,
            admin_note="رسید نامعتبر",
        )
        await query.answer("رد شد")
        try:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"❌ رسید ارسالی برای وبینار «{webinar.title if webinar else ''}» تایید نشد "
                    "(واریز معتبر تشخیص داده نشد).\n\n"
                    "لطفاً از طریق پشتیبانی پیگیری کنید تا وضعیت بررسی شود."
                ),
                reply_markup=await main_menu_keyboard(user.telegram_id),
            )
        except TelegramError as exc:
            logger.warning("Notify rejected user failed: %s", exc)
        note = f"وضعیت ثبت‌نام #{registration_id}: رد / نامعتبر."
    else:
        await set_registration_status(
            registration_id,
            status=RegistrationStatus.PENDING_PAYMENT.value,
            admin_telegram_id=admin.id,
        )
        await query.answer("به انتظار پرداخت برگشت")
        try:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"وضعیت ثبت‌نام شما برای «{webinar.title if webinar else ''}» "
                    "به حالت انتظار پرداخت برگشت. می‌توانید دوباره رسید معتبر ارسال کنید."
                ),
            )
        except TelegramError as exc:
            logger.warning("Notify reset user failed: %s", exc)
        note = f"وضعیت ثبت‌نام #{registration_id}: بازگشت به انتظار پرداخت."

    try:
        if query.message:
            await query.message.reply_text(note)
    except TelegramError:
        pass


async def broadcast_webinar_to_registrants(bot, webinar_id: int) -> tuple[int, int]:
    webinar = await get_webinar(webinar_id)
    if webinar is None or not webinar.link:
        return 0, 0

    text = build_webinar_message(webinar)
    telegram_ids = await list_approved_telegram_ids(webinar_id)
    guest_markup = await main_menu_keyboard()
    admin_ids = set(get_settings().admin_ids)

    sent = 0
    failed = 0
    for telegram_id in telegram_ids:
        markup = await main_menu_keyboard(telegram_id) if telegram_id in admin_ids else guest_markup
        try:
            await bot.send_message(chat_id=telegram_id, text=text, reply_markup=markup)
            # also record claim
            async with get_session() as session:
                result = await session.execute(select(User).where(User.telegram_id == telegram_id))
                db_user = result.scalar_one_or_none()
                if db_user is not None:
                    existing = await session.execute(
                        select(WebinarLinkClaim).where(
                            WebinarLinkClaim.user_id == db_user.id,
                            WebinarLinkClaim.webinar_id == webinar_id,
                        )
                    )
                    if existing.scalar_one_or_none() is None:
                        session.add(
                            WebinarLinkClaim(user_id=db_user.id, webinar_id=webinar_id)
                        )
            sent += 1
        except TelegramError as exc:
            failed += 1
            logger.warning("Webinar link send failed for %s: %s", telegram_id, exc)
        await asyncio.sleep(0.05)

    await update_webinar(webinar_id, link_auto_sent=True)
    return sent, failed


# Backward-compatible alias used by admin panel
async def broadcast_webinar_to_all_users(bot, webinar_id: int) -> tuple[int, int]:
    return await broadcast_webinar_to_registrants(bot, webinar_id)


def register(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(verify_webinar_membership, pattern=rf"^{VERIFY_PREFIX}\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(registration_choice_callback, pattern=r"^wb:reg:\d+:(free|cert)$")
    )
    application.add_handler(
        CallbackQueryHandler(payment_review_callback, pattern=r"^wb:pay:(ok|no|reset):\d+$")
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(r"^🔗 "),
            webinar_button_handler,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & ~filters.COMMAND,
            handle_webinar_name_input,
        ),
        group=1,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & ~filters.COMMAND,
            handle_cert_info_input,
        ),
        group=1,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & ~filters.COMMAND
            & (filters.PHOTO | filters.Document.ALL),
            handle_receipt_upload,
        ),
        group=1,
    )
