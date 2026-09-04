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
    registrant_name: str | None = None,
    name_fa: str | None = None,
    name_en: str | None = None,
    national_id: str | None = None,
    phone: str | None = None,
    info_text: str | None = None,
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
                registrant_name=registrant_name,
                name_fa=name_fa,
                name_en=name_en,
                national_id=national_id,
                phone=phone,
                info_text=info_text,
                receipt_file_id=receipt_file_id,
                receipt_file_type=receipt_file_type,
            )
            session.add(reg)
        else:
            reg.kind = kind
            reg.status = status
            if registrant_name is not None:
                reg.registrant_name = registrant_name
            if name_fa is not None:
                reg.name_fa = name_fa
            if name_en is not None:
                reg.name_en = name_en
            if national_id is not None:
                reg.national_id = national_id
            if phone is not None:
                reg.phone = phone
            if info_text is not None:
                reg.info_text = info_text
            if receipt_file_id is not None:
                reg.receipt_file_id = receipt_file_id
            if receipt_file_type is not None:
                reg.receipt_file_type = receipt_file_type
            if status == RegistrationStatus.APPROVED.value:
                reg.reviewed_at = datetime.now(timezone.utc)
            if status == RegistrationStatus.PENDING_PAYMENT.value:
                reg.receipt_file_id = None
                reg.receipt_file_type = None
                reg.name_fa = None
                reg.name_en = None
                reg.national_id = None
                reg.phone = None
                reg.info_text = None
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
    name = (
        reg.name_fa
        or reg.registrant_name
        or (user.full_name if user else None)
        or "—"
    )
    username = f"@{user.username}" if user and user.username else "—"
    tg_id = user.telegram_id if user else "—"
    lines = [
        f"#{reg.id} | {name} ({username})",
        f"ID: {tg_id}",
        f"نوع: {KIND_LABELS.get(reg.kind, reg.kind)}",
        f"وضعیت: {STATUS_LABELS.get(reg.status, reg.status)}",
    ]
    if reg.name_fa:
        lines.append(f"نام فارسی: {reg.name_fa}")
    if reg.name_en:
        lines.append(f"نام انگلیسی: {reg.name_en}")
    if reg.national_id:
        lines.append(f"کد ملی: {reg.national_id}")
    if reg.phone:
        lines.append(f"شماره تماس: {reg.phone}")
    if reg.info_text:
        lines.append(f"اطلاعات همراه رسید: {reg.info_text}")
    return "\n".join(lines)
