"""Webinar CRUD and user-facing message builder."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database.models import Webinar
from bot.database.session import get_session

BUTTON_PREFIX = "🔗 "
TITLE_MAX = 60
DETAILS_MAX = 1500
LINK_NOTE = (
    "توجه: نیاز به نصب نرم افزار یا برنامه خاصی نیست  "
    "با کلیک روی لینک بصورت تحت وب وارد فضای کلاس می‌شوید."
)


def webinar_button_text(webinar: Webinar) -> str:
    title = webinar.title.strip()
    if len(title) > 40:
        title = title[:37] + "..."
    return f"{BUTTON_PREFIX}{title}"


def build_webinar_message(webinar: Webinar) -> str:
    lines = [f"💻 {webinar.title}"]
    if webinar.time_text:
        lines.extend(["", f"⏰ ساعت {webinar.time_text}"])
    if webinar.details:
        lines.extend(["", webinar.details])
    if webinar.has_certificate:
        price = webinar.certificate_price or "—"
        lines.extend(["", f"🎓 گزینه مدرک فعال است (مبلغ: {price})"])
    if webinar.link:
        lines.extend(["", f"لینک ورود:\n{webinar.link}", "", LINK_NOTE])
    else:
        if webinar.time_text:
            hint = (
                f"لینک در ساعت {webinar.time_text} به صورت خودکار "
                "براتون ارسال می‌شه در همین‌جا."
            )
        else:
            hint = "لینک به‌زودی به صورت خودکار براتون ارسال می‌شه در همین‌جا."
        lines.extend(["", hint])
    return "\n".join(lines)


def normalize_title(text: str) -> str:
    title = " ".join(text.strip().split())
    if not title:
        raise ValueError("نام وبینار نمی‌تواند خالی باشد.")
    if len(title) > TITLE_MAX:
        raise ValueError(f"نام وبینار حداکثر {TITLE_MAX} کاراکتر باشد.")
    return title


def normalize_optional(text: str, *, max_len: int) -> str | None:
    value = text.strip()
    if not value:
        return None
    if len(value) > max_len:
        raise ValueError(f"این متن حداکثر {max_len} کاراکتر باشد.")
    return value


def normalize_link(text: str) -> str | None:
    value = text.strip()
    if not value:
        return None
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("لینک باید با http:// یا https:// شروع شود.")
    return value


async def list_webinars() -> list[Webinar]:
    async with get_session() as session:
        result = await session.execute(select(Webinar).order_by(Webinar.id))
        return list(result.scalars().all())


async def list_visible_webinars() -> list[Webinar]:
    async with get_session() as session:
        result = await session.execute(
            select(Webinar).where(Webinar.is_visible.is_(True)).order_by(Webinar.id)
        )
        return list(result.scalars().all())


async def get_webinar(webinar_id: int) -> Webinar | None:
    async with get_session() as session:
        return await session.get(Webinar, webinar_id)


async def find_webinar_by_button(text: str) -> Webinar | None:
    for webinar in await list_webinars():
        if webinar_button_text(webinar) == text:
            return webinar
    return None


async def create_webinar(
    *,
    title: str,
    time_text: str | None,
    details: str | None,
    link: str | None,
    is_visible: bool = True,
    has_certificate: bool = False,
    certificate_price: str | None = None,
    group_link: str | None = None,
    link_send_at=None,
) -> Webinar:
    async with get_session() as session:
        webinar = Webinar(
            title=title,
            time_text=time_text,
            details=details,
            link=link,
            is_visible=is_visible,
            has_certificate=has_certificate,
            certificate_price=certificate_price,
            group_link=group_link,
            link_send_at=link_send_at,
        )
        session.add(webinar)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ValueError("وبیناری با این نام از قبل وجود دارد.") from exc
        await session.refresh(webinar)
        return webinar


async def update_webinar(webinar_id: int, **fields: object) -> Webinar:
    async with get_session() as session:
        webinar = await session.get(Webinar, webinar_id)
        if webinar is None:
            raise ValueError("وبینار پیدا نشد.")
        for key, value in fields.items():
            setattr(webinar, key, value)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ValueError("وبیناری با این نام از قبل وجود دارد.") from exc
        await session.refresh(webinar)
        return webinar


async def delete_webinar(webinar_id: int) -> bool:
    from sqlalchemy import delete

    from bot.database.models import WebinarLinkClaim, WebinarRegistration

    async with get_session() as session:
        webinar = await session.get(Webinar, webinar_id)
        if webinar is None:
            return False
        await session.execute(
            delete(WebinarRegistration).where(WebinarRegistration.webinar_id == webinar_id)
        )
        await session.execute(
            delete(WebinarLinkClaim).where(WebinarLinkClaim.webinar_id == webinar_id)
        )
        await session.delete(webinar)
        return True
