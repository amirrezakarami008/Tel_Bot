"""Google Gemini client for support replies."""

from __future__ import annotations

import httpx

from bot.config import get_settings


class AIConfigurationError(RuntimeError):
    pass


class AIRequestError(RuntimeError):
    pass


async def generate_support_reply(user_text: str) -> str:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise AIConfigurationError("GEMINI_API_KEY تنظیم نشده است.")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "تو دستیار پشتیبانی فارسی هستی. کوتاه، دقیق و محترمانه پاسخ بده. "
                            "اگر اطلاعات کافی نداری، کاربر را به پشتیبانی انسانی ارجاع بده.\n\n"
                            f"پیام کاربر:\n{user_text}"
                        )
                    }
                ]
            }
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": settings.gemini_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AIRequestError("دریافت پاسخ از Gemini ناموفق بود.") from exc

    try:
        parts = data["candidates"][0]["content"]["parts"]
        content = "".join(part["text"] for part in parts if part.get("text"))
    except (KeyError, IndexError, TypeError) as exc:
        raise AIRequestError("فرمت پاسخ Gemini قابل شناسایی نیست.") from exc
    if not content.strip():
        raise AIRequestError("Gemini پاسخ خالی برگرداند.")
    return content.strip()