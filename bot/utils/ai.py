"""Groq Responses API client for support replies."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import func, select

from bot.config import get_settings
from bot.database.models import (
    GiftFileClaim,
    SupportMessage,
    User,
    Webinar,
    WebinarLinkClaim,
    WebinarRegistration,
)
from bot.database.session import get_session
from bot.utils.knowledge import read_knowledge_text


class AIConfigurationError(RuntimeError):
    pass


class AIRequestError(RuntimeError):
    pass


async def _request_groq(
    client: httpx.AsyncClient,
    *,
    payload: dict,
    headers: dict[str, str],
) -> dict:
    url = "https://api.groq.com/openai/v1/responses"
    last_status: int | None = None
    for attempt in range(3):
        try:
            response = await client.post(url, json=payload, headers=headers)
            last_status = response.status_code
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if attempt < 2 and (
                isinstance(exc, httpx.TimeoutException)
                or last_status in {429, 500, 502, 503, 504}
            ):
                await asyncio.sleep(2**attempt)
                continue
            raise AIRequestError(
                f"دریافت پاسخ از Groq ناموفق بود (HTTP {last_status or 'network'})."
            ) from exc
    raise AIRequestError("دریافت پاسخ از Groq ناموفق بود.")


async def _build_user_context(telegram_id: int) -> str | None:
    async with get_session() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None

        registrations = (
            await session.execute(
                select(Webinar.title, WebinarRegistration.status)
                .join(Webinar, Webinar.id == WebinarRegistration.webinar_id)
                .where(WebinarRegistration.user_id == user.id)
                .order_by(WebinarRegistration.created_at)
            )
        ).all()
        webinar_claims = await session.scalar(
            select(func.count())
            .select_from(WebinarLinkClaim)
            .where(WebinarLinkClaim.user_id == user.id)
        ) or 0
        gift_claims = await session.scalar(
            select(func.count())
            .select_from(GiftFileClaim)
            .where(GiftFileClaim.user_id == user.id)
        ) or 0
        support_messages = await session.scalar(
            select(func.count())
            .select_from(SupportMessage)
            .where(SupportMessage.user_id == user.id)
        ) or 0

    webinar_lines = "\n".join(
        f"- {title}: {status}" for title, status in registrations
    ) or "- موردی ثبت نشده"
    return (
        f"شناسه تأییدشده کاربر: {telegram_id}\n"
        f"نام ثبت‌شده: {user.full_name or 'ثبت نشده'}\n"
        f"تاریخ ورود به ربات: {user.first_seen_at.isoformat()}\n"
        f"تعداد دریافت لینک وبینار: {webinar_claims}\n"
        f"تعداد دریافت فایل هدیه: {gift_claims}\n"
        f"تعداد پیام‌های پشتیبانی: {support_messages}\n"
        f"ثبت‌نام‌های وبینار:\n{webinar_lines}"
    )


async def generate_support_reply(
    user_text: str,
    *,
    telegram_id: int | None = None,
) -> str:
    settings = get_settings()
    if not settings.groq_api_key:
        raise AIConfigurationError("GROQ_API_KEY تنظیم نشده است.")

    user_context = ""
    if telegram_id is not None:
        user_context = await _build_user_context(telegram_id) or ""
        if not user_context:
            raise AIConfigurationError("اطلاعات این کاربر در دیتابیس پیدا نشد.")
    knowledge = read_knowledge_text()

    payload = {
        "model": settings.groq_model,
        "input": (
            "تو دستیار پشتیبانی فارسی هستی. کوتاه، دقیق و محترمانه پاسخ بده. "
            "فقط بر اساس اطلاعات زیر پاسخ بده؛ اطلاعات شخصی را فقط برای همان کاربر استفاده کن. "
            "اگر پاسخ در اطلاعات موجود نیست، صادقانه بگو اطلاعات کافی نداری.\n\n"
            f"اطلاعات دانش عمومی:\n{knowledge or 'فایل دانش هنوز بارگذاری نشده است.'}\n\n"
            f"اطلاعات خصوصی کاربر:\n{user_context or 'درخواست عمومی ادمین'}\n\n"
            f"پیام کاربر:\n{user_text}"
        ),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.groq_api_key}",
    }
    async with httpx.AsyncClient(timeout=45) as client:
        data = await _request_groq(
            client,
            payload=payload,
            headers=headers,
        )

    try:
        content = data["output_text"]
    except (KeyError, IndexError, TypeError) as exc:
        try:
            content = "".join(
                item["text"]
                for output in data["output"]
                for item in output.get("content", [])
                if item.get("type") == "output_text"
            )
        except (KeyError, TypeError) as nested_exc:
            raise AIRequestError("فرمت پاسخ Groq قابل شناسایی نیست.") from nested_exc
    if not isinstance(content, str) or not content.strip():
        raise AIRequestError("Groq پاسخ خالی برگرداند.")
    return content.strip()