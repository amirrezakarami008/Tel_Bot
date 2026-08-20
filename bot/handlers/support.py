"""Feature 2: bidirectional support message relay."""

from __future__ import annotations

import logging
import re
import time

from telegram import Message, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import get_settings
from bot.database.models import SupportDirection, SupportMessage
from bot.database.session import get_session
from bot.handlers.start import BTN_GIFT, BTN_SUPPORT, BTN_WEBINAR
from bot.utils.users import get_or_create_user

logger = logging.getLogger(__name__)

MENU_BUTTONS = {BTN_WEBINAR, BTN_GIFT, BTN_SUPPORT}

TICKET_RE = re.compile(r"#TICKET_(\d+)_(\d+)")
RATE_LIMIT_SECONDS = 10.0

# telegram_id -> last support message monotonic timestamp
_last_support_at: dict[int, float] = {}

# (admin_telegram_id, admin_chat_message_id) -> user telegram_id
_reply_targets: dict[tuple[int, int], int] = {}


def build_ticket_tag(user_id: int, message_id: int) -> str:
    return f"#TICKET_{user_id}_{message_id}"


def parse_ticket_tag(text: str | None) -> int | None:
    if not text:
        return None
    match = TICKET_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def _is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    last = _last_support_at.get(user_id)
    if last is not None and (now - last) < RATE_LIMIT_SECONDS:
        return True
    _last_support_at[user_id] = now
    return False


def _message_preview(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.photo:
        return "[photo]"
    if message.document:
        name = message.document.file_name or "document"
        return f"[document: {name}]"
    if message.voice:
        return "[voice]"
    if message.video:
        return "[video]"
    if message.sticker:
        return "[sticker]"
    return "[media]"


def _remember_reply_target(admin_id: int, message_id: int, user_telegram_id: int) -> None:
    _reply_targets[(admin_id, message_id)] = user_telegram_id


def _resolve_target_user(admin_id: int, replied: Message) -> int | None:
    mapped = _reply_targets.get((admin_id, replied.message_id))
    if mapped is not None:
        return mapped
    return parse_ticket_tag(replied.text) or parse_ticket_tag(replied.caption)


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_message: Message,
    user_telegram_id: int,
    ticket: str,
    header: str,
) -> None:
    settings = get_settings()
    for admin_id in settings.admin_ids:
        try:
            notice = await context.bot.send_message(
                chat_id=admin_id,
                text=f"{header}\n{ticket}",
            )
            _remember_reply_target(admin_id, notice.message_id, user_telegram_id)

            forwarded = await user_message.forward(chat_id=admin_id)
            _remember_reply_target(admin_id, forwarded.message_id, user_telegram_id)
        except TelegramError as exc:
            logger.error("Failed to notify admin %s: %s", admin_id, exc)


async def handle_user_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    if message.chat.type != ChatType.PRIVATE:
        return

    settings = get_settings()
    if settings.is_admin(user.id):
        return

    if message.text and message.text.strip() in MENU_BUTTONS:
        return

    if _is_rate_limited(user.id):
        await message.reply_text(
            f"لطفاً کمی صبر کنید (حداکثر یک پیام هر {int(RATE_LIMIT_SECONDS)} ثانیه)."
        )
        return

    ticket = build_ticket_tag(user.id, message.message_id)
    username = f"@{user.username}" if user.username else "—"
    full_name = " ".join(
        part for part in [user.first_name or "", user.last_name or ""] if part
    ).strip() or "—"

    header = (
        "📩 پیام پشتیبانی جدید\n"
        f"کاربر: {full_name}\n"
        f"Username: {username}\n"
        f"ID: {user.id}\n"
        "برای پاسخ، روی این پیام یا پیام فوروارد‌شده ریپلای کنید."
    )

    async with get_session() as session:
        db_user = await get_or_create_user(session, user)
        session.add(
            SupportMessage(
                user_id=db_user.id,
                direction=SupportDirection.USER_TO_ADMIN.value,
                text=_message_preview(message),
                admin_telegram_id=None,
            )
        )

    await _notify_admins(
        context,
        user_message=message,
        user_telegram_id=user.id,
        ticket=ticket,
        header=header,
    )
    await message.reply_text(
        "پیام شما برای پشتیبانی ارسال شد، پاسخ همین‌جا به شما داده خواهد شد."
    )


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    admin = update.effective_user
    if message is None or admin is None or message.reply_to_message is None:
        return

    settings = get_settings()
    if not settings.is_admin(admin.id):
        return

    target_user_id = _resolve_target_user(admin.id, message.reply_to_message)
    if target_user_id is None:
        await message.reply_text(
            "برای پاسخ پشتیبانی، روی پیام اعلان بات (حاوی #TICKET_...) یا پیام فوروارد‌شده ریپلای کنید."
        )
        return

    reply_body = message.text or message.caption
    has_media = bool(message.photo or message.document or message.voice or message.video)
    if not reply_body and not has_media:
        await message.reply_text("پاسخ خالی ارسال نشد.")
        return

    try:
        if message.text:
            await context.bot.send_message(chat_id=target_user_id, text=message.text)
        elif message.photo:
            await context.bot.send_photo(
                chat_id=target_user_id,
                photo=message.photo[-1].file_id,
                caption=message.caption,
            )
        elif message.document:
            await context.bot.send_document(
                chat_id=target_user_id,
                document=message.document.file_id,
                caption=message.caption,
            )
        elif message.voice:
            await context.bot.send_voice(
                chat_id=target_user_id,
                voice=message.voice.file_id,
                caption=message.caption,
            )
        elif message.video:
            await context.bot.send_video(
                chat_id=target_user_id,
                video=message.video.file_id,
                caption=message.caption,
            )
        else:
            await message.reply_text("این نوع پیام برای رله پشتیبانی پشتیبانی نمی‌شود.")
            return
    except TelegramError as exc:
        logger.error("Failed to deliver admin reply to user %s: %s", target_user_id, exc)
        await message.reply_text("ارسال پاسخ به کاربر ناموفق بود.")
        return

    async with get_session() as session:
        from sqlalchemy import select

        from bot.database.models import User

        result = await session.execute(select(User).where(User.telegram_id == target_user_id))
        db_user = result.scalar_one_or_none()
        if db_user is None:
            db_user = User(telegram_id=target_user_id, username=None, full_name=None)
            session.add(db_user)
            await session.flush()

        session.add(
            SupportMessage(
                user_id=db_user.id,
                direction=SupportDirection.ADMIN_TO_USER.value,
                text=reply_body or _message_preview(message),
                admin_telegram_id=admin.id,
            )
        )

    await message.reply_text("پاسخ برای کاربر ارسال شد.")


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if query:
        await query.answer("برای این کانال لینک عمومی در .env تنظیم نشده است.", show_alert=True)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^noop:"))

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.REPLY & ~filters.COMMAND,
            handle_admin_reply,
        ),
        group=1,
    )

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & ~filters.COMMAND
            & (
                filters.TEXT
                | filters.PHOTO
                | filters.Document.ALL
                | filters.VOICE
                | filters.VIDEO
            ),
            handle_user_support,
        ),
        group=2,
    )
