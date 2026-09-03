"""Webinar registration (free / certificate) helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.database.models import (
    RegistrationKind,
    RegistrationStatus,
    User,
    WebinarRegistration,
)
from bot.database.session import get_session
from bot.utils.users import get_or_create_user


STATUS_LABELS = {
    RegistrationStatus.PENDING_PAYMENT.value: "در انتظار پرداخت",
    RegistrationStatus.PENDING_REVIEW.value: "در انتظار بررسی رسید",
    RegistrationStatus.APPROVED.value: "تایید شده",
    RegistrationStatus.REJECTED.value: "نامعتبر / رد شده",
}

KIND_LABELS = {
    RegistrationKind.FREE.value: "بدون مدرک (رایگان)",
    RegistrationKind.CERTIFICATE.value: "با مدرک",
}


async def get_registration(user_id: int, webinar_id: int) -> WebinarRegistration | None:
    async with get_session() as session:
        result = await session.execute(
            select(WebinarRegistration).where(
                WebinarRegistration.user_id == user_id,
                WebinarRegistration.webinar_id == webinar_id,
            )
        )
        return result.scalar_one_or_none()


async def get_registration_by_id(registration_id: int) -> WebinarRegistration | None:
    async with get_session() as session:
        result = await session.execute(
            select(WebinarRegistration)
            .options(
                selectinload(WebinarRegistration.user),
                selectinload(WebinarRegistration.webinar),
            )
            .where(WebinarRegistration.id == registration_id)
        )
        return result.scalar_one_or_none()


async def upsert_registration(
    tg_user,
    *,
    webinar_id: int,
    kind: str,
    status: str,
    receipt_file_id: str | None = None,
    receipt_file_type: str | None = None,
) -> WebinarRegistration:
    async with get_session() as session:
        db_user = await get_or_create_user(session, tg_user)
        result = await session.execute(
            select(WebinarRegistration).where(
                WebinarRegistration.user_id == db_user.id,
                WebinarRegistration.webinar_id == webinar_id,
            )
        )
        reg = result.scalar_one_or_none()
        if reg is None:
            reg = WebinarRegistration(
                user_id=db_user.id,
                webinar_id=webinar_id,
                kind=kind,
                status=status,
                receipt_file_id=receipt_file_id,
                receipt_file_type=receipt_file_type,
            )
            session.add(reg)
        else:
            reg.kind = kind
            reg.status = status
            if receipt_file_id is not None:
                reg.receipt_file_id = receipt_file_id
            if receipt_file_type is not None:
                reg.receipt_file_type = receipt_file_type
            if status == RegistrationStatus.APPROVED.value:
                reg.reviewed_at = datetime.now(timezone.utc)
            if status == RegistrationStatus.PENDING_PAYMENT.value:
                reg.receipt_file_id = None
                reg.receipt_file_type = None
                reg.admin_note = None
                reg.reviewed_at = None
                reg.reviewed_by_admin_id = None
        await session.flush()
        await session.refresh(reg)
        return reg


async def set_registration_status(
    registration_id: int,
    *,
    status: str,
    admin_telegram_id: int | None = None,
    admin_note: str | None = None,
) -> WebinarRegistration | None:
    async with get_session() as session:
        reg = await session.get(WebinarRegistration, registration_id)
        if reg is None:
            return None
        reg.status = status
        reg.reviewed_by_admin_id = admin_telegram_id
        reg.reviewed_at = datetime.now(timezone.utc)
        if admin_note is not None:
            reg.admin_note = admin_note
        await session.flush()
        await session.refresh(reg)
        return reg


async def list_registrations_for_webinar(
    webinar_id: int,
    *,
    status: str | None = None,
) -> list[WebinarRegistration]:
    async with get_session() as session:
        stmt = (
            select(WebinarRegistration)
            .options(selectinload(WebinarRegistration.user))
            .where(WebinarRegistration.webinar_id == webinar_id)
            .order_by(WebinarRegistration.id.desc())
        )
        if status:
            stmt = stmt.where(WebinarRegistration.status == status)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def list_approved_telegram_ids(webinar_id: int) -> list[int]:
    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .join(WebinarRegistration, WebinarRegistration.user_id == User.id)
            .where(
                WebinarRegistration.webinar_id == webinar_id,
                WebinarRegistration.status == RegistrationStatus.APPROVED.value,
            )
        )
        return [row[0] for row in result.all()]


def registration_summary(reg: WebinarRegistration) -> str:
    user = reg.user
    name = (user.full_name if user else None) or "—"
    username = f"@{user.username}" if user and user.username else "—"
    tg_id = user.telegram_id if user else "—"
    return (
        f"#{reg.id} | {name} ({username})\n"
        f"ID: {tg_id}\n"
        f"نوع: {KIND_LABELS.get(reg.kind, reg.kind)}\n"
        f"وضعیت: {STATUS_LABELS.get(reg.status, reg.status)}"
    )
