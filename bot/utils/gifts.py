"""Gift files on disk: list, save, delete."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from bot.config import get_settings

logger = logging.getLogger(__name__)

MAX_FILENAME_LEN = 120


def gift_files_directory() -> Path:
    directory = Path(get_settings().gift_files_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_gift_files() -> list[Path]:
    directory = gift_files_directory()
    if not directory.is_dir():
        logger.warning("Gift files directory missing: %s", directory)
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.name.startswith(".")
    )


def sanitize_filename(name: str) -> str:
    raw = Path(name or "gift.bin").name.strip() or "gift.bin"
    cleaned = re.sub(r"[^\w.\- ()\u0600-\u06FF]+", "_", raw, flags=re.UNICODE)
    cleaned = cleaned.strip(" ._") or "gift.bin"
    if len(cleaned) > MAX_FILENAME_LEN:
        stem = Path(cleaned).stem[: MAX_FILENAME_LEN - 20]
        suffix = Path(cleaned).suffix[:15]
        cleaned = f"{stem}{suffix}"
    return cleaned


def unique_gift_path(filename: str) -> Path:
    directory = gift_files_directory()
    safe = sanitize_filename(filename)
    target = directory / safe
    if not target.exists():
        return target
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("نام فایل تکراری است؛ لطفاً نام دیگری بفرستید.")


def get_gift_file_by_index(index: int) -> Path | None:
    files = list_gift_files()
    if index < 0 or index >= len(files):
        return None
    return files[index]


def delete_gift_file(path: Path) -> bool:
    directory = gift_files_directory().resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if directory not in resolved.parents and resolved.parent != directory:
        logger.error("Refusing to delete path outside gift dir: %s", resolved)
        return False
    if not resolved.is_file():
        return False
    resolved.unlink()
    return True


async def save_telegram_document(bot, *, file_id: str, filename: str) -> Path:
    target = unique_gift_path(filename)
    tg_file = await bot.get_file(file_id)
    await tg_file.download_to_drive(custom_path=str(target))
    return target
