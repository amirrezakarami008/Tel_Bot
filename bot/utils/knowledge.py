"""Admin-managed text knowledge file for AI answers."""

from __future__ import annotations

from pathlib import Path

from bot.config import get_settings


MAX_KNOWLEDGE_BYTES = 1_000_000


def knowledge_file_path() -> Path:
    path = Path(get_settings().ai_knowledge_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_knowledge_text() -> str:
    path = knowledge_file_path()
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")[:MAX_KNOWLEDGE_BYTES]


async def save_knowledge_document(bot, *, file_id: str) -> Path:
    path = knowledge_file_path()
    telegram_file = await bot.get_file(file_id)
    await telegram_file.download_to_drive(custom_path=str(path))
    if path.stat().st_size > MAX_KNOWLEDGE_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError("حجم فایل دانش نباید بیشتر از ۱ مگابایت باشد.")
    return path