"""Enable/disable user-facing feature buttons."""

from __future__ import annotations

from bot.database.models import BotSetting
from bot.database.session import get_session

FEATURE_GIFT = "gift"
FEATURE_SUPPORT = "support"
FEATURE_KEYS = (FEATURE_GIFT, FEATURE_SUPPORT)

FEATURE_LABELS = {
    FEATURE_GIFT: "فایل هدیه",
    FEATURE_SUPPORT: "پشتیبانی",
}


def _setting_key(feature: str) -> str:
    return f"feature:{feature}"


async def is_feature_enabled(feature: str) -> bool:
    async with get_session() as session:
        row = await session.get(BotSetting, _setting_key(feature))
        if row is None or row.value is None:
            return True
        return row.value == "1"


async def set_feature_enabled(feature: str, enabled: bool) -> None:
    key = _setting_key(feature)
    value = "1" if enabled else "0"
    async with get_session() as session:
        row = await session.get(BotSetting, key)
        if row is None:
            session.add(BotSetting(key=key, value=value))
        else:
            row.value = value


async def toggle_feature(feature: str) -> bool:
    enabled = not await is_feature_enabled(feature)
    await set_feature_enabled(feature, enabled)
    return enabled
