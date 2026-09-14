"""Groq Responses API client for support replies."""

from __future__ import annotations

import asyncio

import httpx

from bot.config import get_settings


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


async def generate_support_reply(user_text: str) -> str:
    settings = get_settings()
    if not settings.groq_api_key:
        raise AIConfigurationError("GROQ_API_KEY تنظیم نشده است.")

    payload = {
        "model": settings.groq_model,
        "input": (
            "تو دستیار پشتیبانی فارسی هستی. کوتاه، دقیق و محترمانه پاسخ بده. "
            "اگر اطلاعات کافی نداری، کاربر را به پشتیبانی انسانی ارجاع بده.\n\n"
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