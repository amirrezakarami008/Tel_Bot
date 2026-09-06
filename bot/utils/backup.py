"""PostgreSQL backup: dump in memory and send to Telegram admins only."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, time
from io import BytesIO
from zoneinfo import ZoneInfo

from sqlalchemy.engine.url import make_url
from telegram import InputFile
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from bot.config import get_settings

logger = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")
BACKUP_JOB_NAME = "nightly_db_backup"
DUMP_TIMEOUT_SECONDS = 180
# Telegram Bot API document limit is 50 MB; stay a little under it.
TELEGRAM_MAX_DOCUMENT_BYTES = 49 * 1024 * 1024

_backup_lock = asyncio.Lock()


class BackupError(Exception):
    """Raised when dump or delivery fails."""


@dataclass(frozen=True)
class BackupDump:
    content: bytes
    filename: str
    created_at: datetime
    database: str


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} بایت"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} کیلوبایت"
    return f"{size / (1024 * 1024):.1f} مگابایت"


def _pg_env() -> tuple[dict[str, str], str]:
    url = make_url(get_settings().database_url)
    if not url.host or not url.database or not url.username:
        raise BackupError("DATABASE_URL ناقص است؛ host/user/database لازم است.")

    env = os.environ.copy()
    env["PGHOST"] = str(url.host)
    env["PGPORT"] = str(url.port or 5432)
    env["PGUSER"] = str(url.username)
    env["PGPASSWORD"] = str(url.password or "")
    env["PGDATABASE"] = str(url.database)
    env["PGCLIENTENCODING"] = "UTF8"
    return env, str(url.database)


def _resolve_pg_dump() -> str:
    found = shutil.which("pg_dump")
    if found:
        return found
    raise BackupError(
        "ابزار pg_dump روی سرور پیدا نشد. "
        "در Docker باید کلاینت Postgres داخل ایمیج بات نصب باشد."
    )


async def create_database_dump() -> BackupDump:
    """Run pg_dump and keep the result only in RAM (no file on disk)."""
    env, database = _pg_env()
    pg_dump = _resolve_pg_dump()
    created_at = datetime.now(TEHRAN)
    filename = f"bot-db-{created_at.strftime('%Y-%m-%d_%H%M')}.dump"

    proc = await asyncio.create_subprocess_exec(
        pg_dump,
        "--format=custom",
        "--compress=9",
        "--no-owner",
        "--no-acl",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=DUMP_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise BackupError("زمان تهیه بک‌آپ تمام شد.") from exc

    err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        logger.error("pg_dump failed (code=%s): %s", proc.returncode, err_text)
        detail = err_text.splitlines()[-1] if err_text else f"exit {proc.returncode}"
        raise BackupError(f"pg_dump ناموفق بود: {detail}")

    if not stdout:
        raise BackupError("خروجی بک‌آپ خالی بود.")

    logger.info(
        "Database dump ready db=%s size=%s bytes",
        database,
        len(stdout),
    )
    return BackupDump(
        content=stdout,
        filename=filename,
        created_at=created_at,
        database=database,
    )


def _caption(dump: BackupDump, *, reason: str) -> str:
    stamp = dump.created_at.strftime("%Y-%m-%d %H:%M")
    return (
        "💾 بک‌آپ دیتابیس\n"
        f"📌 {reason}\n"
        f"🗓 {stamp} (تهران)\n"
        f"🗄 {html.escape(dump.database)}\n"
        f"📦 {_format_size(len(dump.content))}\n\n"
        "این فایل فقط برای ادمین است و روی سرور ذخیره نمی‌شود.\n\n"
        "بازیابی:\n"
        f"<code>pg_restore --clean --if-exists -d {html.escape(dump.database)} {html.escape(dump.filename)}</code>"
    )


async def send_database_backup(bot, *, reason: str) -> BackupDump:
    """Dump the database and send the file to every admin. Nothing is kept on disk."""
    async with _backup_lock:
        dump = await create_database_dump()
        if len(dump.content) > TELEGRAM_MAX_DOCUMENT_BYTES:
            raise BackupError(
                "حجم بک‌آپ از حد ارسال تلگرام (حدود ۵۰ مگابایت) بیشتر است: "
                f"{_format_size(len(dump.content))}"
            )

        caption = _caption(dump, reason=reason)
        recipients = get_settings().admin_ids
        failures: list[str] = []

        for admin_id in recipients:
            buffer = BytesIO(dump.content)
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=InputFile(buffer, filename=dump.filename),
                    caption=caption,
                    parse_mode="HTML",
                )
            except TelegramError as exc:
                logger.error("Failed to send DB backup to admin %s: %s", admin_id, exc)
                failures.append(f"{admin_id}: {exc}")

        if len(failures) == len(recipients):
            raise BackupError("ارسال بک‌آپ به ادمین‌ها ناموفق بود.")
        if failures:
            logger.warning("Backup delivered with partial failures: %s", failures)

        return dump


async def notify_admins_backup_error(bot, *, reason: str, error: str) -> None:
    text = (
        "❌ بک‌آپ دیتابیس گرفته نشد\n"
        f"📌 {reason}\n"
        f"⚠️ {error}"
    )
    for admin_id in get_settings().admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except TelegramError as exc:
            logger.error("Failed to notify admin %s about backup error: %s", admin_id, exc)


async def nightly_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    reason = "بک‌آپ خودکار نیمه‌شب"
    try:
        await send_database_backup(context.bot, reason=reason)
        logger.info("Nightly database backup sent to admins")
    except Exception as exc:  # noqa: BLE001 — notify admins, do not crash the job queue
        logger.exception("Nightly database backup failed")
        await notify_admins_backup_error(
            context.bot,
            reason=reason,
            error=str(exc) or exc.__class__.__name__,
        )


def schedule_nightly_backup(application: Application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        logger.error(
            "JobQueue در دسترس نیست؛ بک‌آپ نیمه‌شب فعال نشد. "
            "پکیج python-telegram-bot[job-queue] را نصب کنید."
        )
        return

    for job in job_queue.get_jobs_by_name(BACKUP_JOB_NAME):
        job.schedule_removal()

    job_queue.run_daily(
        nightly_backup_job,
        time=time(hour=0, minute=0, tzinfo=TEHRAN),
        name=BACKUP_JOB_NAME,
    )
    logger.info("Nightly DB backup scheduled for 00:00 Asia/Tehran")
