"""Admin-managed text knowledge file for AI answers."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from bot.config import get_settings


logger = logging.getLogger(__name__)

MAX_KNOWLEDGE_BYTES = 1_000_000


def knowledge_file_path() -> Path:
    path = Path(get_settings().ai_knowledge_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    _maybe_migrate_legacy_knowledge(path)
    return path


def _maybe_migrate_legacy_knowledge(target: Path) -> None:
    """Move old gift_files/ai_knowledge.txt into the dedicated AI path once."""
    if target.is_file():
        return
    legacy = Path("./gift_files/ai_knowledge.txt")
    if not legacy.is_file():
        return
    try:
        shutil.move(str(legacy), str(target))
        logger.info("Migrated AI knowledge file from %s to %s", legacy, target)
    except OSError:
        logger.exception("Failed to migrate AI knowledge file from %s", legacy)


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
