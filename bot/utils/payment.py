"""Global bank card settings for certificate payments."""

from __future__ import annotations

from bot.database.models import BotSetting
from bot.database.session import get_session

CARD_NUMBER_KEY = "payment_card_number"
CARD_HOLDER_KEY = "payment_card_holder"


async def get_payment_card() -> tuple[str | None, str | None]:
    async with get_session() as session:
        number_row = await session.get(BotSetting, CARD_NUMBER_KEY)
        holder_row = await session.get(BotSetting, CARD_HOLDER_KEY)
        number = number_row.value if number_row else None
        holder = holder_row.value if holder_row else None
        return number, holder


async def set_payment_card(*, number: str, holder: str | None) -> None:
    async with get_session() as session:
        for key, value in (
            (CARD_NUMBER_KEY, number.strip()),
            (CARD_HOLDER_KEY, (holder or "").strip() or None),
        ):
            row = await session.get(BotSetting, key)
            if row is None:
                session.add(BotSetting(key=key, value=value))
            else:
                row.value = value


def format_payment_instructions(*, price: str | None, card_number: str, card_holder: str | None) -> str:
    lines = [
        "برای دریافت مدرک، مبلغ را به کارت زیر واریز کنید.",
        "سپس فقط عکس یا فایل فیش واریز را همین‌جا بفرستید.",
        "",
    ]
    if price:
        lines.append(f"مبلغ: {price}")
    lines.append(f"شماره کارت: {card_number}")
    if card_holder:
        lines.append(f"به نام: {card_holder}")
    lines.extend(
        [
            "",
            "بعد از دریافت فیش، اطلاعات لازم برای صدور مدرک از شما پرسیده می‌شود.",
        ]
    )
    return "\n".join(lines)
