"""Required force-join channels stored in the database."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from bot.database.models import RequiredChannel
from bot.database.session import get_session

TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{4,}|joinchat/[A-Za-z0-9_-]+|\+[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def channel_label(channel: RequiredChannel) -> str:
    if channel.username:
        return f"@{channel.username}"
    if channel.title:
        return channel.title
    return channel.chat_id


def channel_to_dict(channel: RequiredChannel) -> dict[str, str]:
    entry: dict[str, str] = {"chat_id": channel.chat_id}
    if channel.username:
        entry["username"] = channel.username
    invite = channel.invite_link
    if not invite and channel.username:
        invite = f"https://t.me/{channel.username}"
    if invite:
        entry["invite_link"] = invite
    if channel.title:
        entry["title"] = channel.title
    return entry


def parse_channel_input(text: str) -> dict[str, str | None]:
    """
    Parse admin input into chat_id / username / invite_link.

    Supported:
    - @username / username
    - https://t.me/username
    - -1001234567890:@username
    - -1001234567890
    - https://t.me/+xxxx / joinchat/...  (invite only; chat_id still required later)
    """
    raw = text.strip()
    if not raw:
        raise ValueError("متن خالی است.")

    invite_link: str | None = None
    username: str | None = None
    chat_id: str | None = None

    tme = TME_RE.fullmatch(raw) or TME_RE.search(raw)
    if tme:
        path = tme.group(1)
        if path.startswith("+") or path.lower().startswith("joinchat/"):
            invite_link = raw if raw.startswith("http") else f"https://t.me/{path}"
            raise ValueError(
                "این یک لینک دعوت است.\n"
                "لطفاً شناسه عددی کانال را بفرستید (مثلاً -1001234567890) "
                "یا از فرمت -1001234567890:@username استفاده کنید.\n"
                "لینک دعوت را در مرحله بعد می‌توانید بفرستید."
            )
        username = path.lstrip("@")
        chat_id = f"@{username}"
        invite_link = f"https://t.me/{username}"
        return {"chat_id": chat_id, "username": username, "invite_link": invite_link, "title": None}

    if ":" in raw and not raw.startswith("@"):
        id_part, user_part = raw.split(":", 1)
        if not id_part.strip().lstrip("-").isdigit():
            raise ValueError("فرمت صحیح نیست. مثال: -1001234567890:@channel")
        chat_id = id_part.strip()
        username = user_part.strip().lstrip("@") or None
        invite_link = f"https://t.me/{username}" if username else None
        return {"chat_id": chat_id, "username": username, "invite_link": invite_link, "title": None}

    if raw.lstrip("-").isdigit():
        return {"chat_id": raw, "username": None, "invite_link": None, "title": None}

    if raw.startswith("@"):
        username = raw.lstrip("@")
        return {
            "chat_id": f"@{username}",
            "username": username,
            "invite_link": f"https://t.me/{username}",
            "title": None,
        }

    if re.fullmatch(r"[A-Za-z0-9_]{4,}", raw):
        username = raw
        return {
            "chat_id": f"@{username}",
            "username": username,
            "invite_link": f"https://t.me/{username}",
            "title": None,
        }

    raise ValueError(
        "فرمت شناخته نشد.\n"
        "مثال‌ها:\n"
        "• @mychannel\n"
        "• https://t.me/mychannel\n"
        "• -1001234567890:@mychannel\n"
        "• -1001234567890"
    )


def normalize_invite_link(text: str) -> str:
    value = text.strip()
    if not value:
        raise ValueError("لینک خالی است.")
    if value.startswith("@"):
        return f"https://t.me/{value.lstrip('@')}"
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        if "t.me" not in parsed.netloc and "telegram.me" not in parsed.netloc:
            raise ValueError("لینک باید مربوط به تلگرام باشد (t.me).")
        return value
    if value.startswith("t.me/") or value.startswith("telegram.me/"):
        return f"https://{value}"
    raise ValueError("لینک عضویت معتبر بفرستید (مثلاً https://t.me/mychannel).")


async def list_required_channels() -> list[RequiredChannel]:
    async with get_session() as session:
        result = await session.execute(select(RequiredChannel).order_by(RequiredChannel.id))
        return list(result.scalars().all())


async def get_required_channel(channel_id: int) -> RequiredChannel | None:
    async with get_session() as session:
        return await session.get(RequiredChannel, channel_id)


async def create_required_channel(
    *,
    chat_id: str,
    username: str | None = None,
    title: str | None = None,
    invite_link: str | None = None,
) -> RequiredChannel:
    async with get_session() as session:
        channel = RequiredChannel(
            chat_id=chat_id,
            username=username,
            title=title,
            invite_link=invite_link,
        )
        session.add(channel)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ValueError("این کانال از قبل در لیست اجباری وجود دارد.") from exc
        await session.refresh(channel)
        return channel


async def delete_required_channel(channel_id: int) -> bool:
    async with get_session() as session:
        channel = await session.get(RequiredChannel, channel_id)
        if channel is None:
            return False
        await session.delete(channel)
        return True


async def count_required_channels() -> int:
    async with get_session() as session:
        return await session.scalar(select(func.count()).select_from(RequiredChannel)) or 0
