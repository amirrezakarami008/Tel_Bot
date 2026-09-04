"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SupportDirection(str, Enum):
    USER_TO_ADMIN = "user_to_admin"
    ADMIN_TO_USER = "admin_to_user"


class RegistrationKind(str, Enum):
    FREE = "free"
    CERTIFICATE = "certificate"


class RegistrationStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    webinar_claims: Mapped[list[WebinarLinkClaim]] = relationship(back_populates="user")
    gift_claims: Mapped[list[GiftFileClaim]] = relationship(back_populates="user")
    support_messages: Mapped[list[SupportMessage]] = relationship(back_populates="user")
    webinar_registrations: Mapped[list[WebinarRegistration]] = relationship(
        back_populates="user"
    )


class Webinar(Base):
    __tablename__ = "webinars"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    has_certificate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    certificate_price: Mapped[str | None] = mapped_column(String(120), nullable=True)
    group_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    link_auto_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    claims: Mapped[list[WebinarLinkClaim]] = relationship(
        back_populates="webinar",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    registrations: Mapped[list[WebinarRegistration]] = relationship(
        back_populates="webinar",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WebinarRegistration(Base):
    __tablename__ = "webinar_registrations"
    __table_args__ = (
        UniqueConstraint("user_id", "webinar_id", name="uq_webinar_registration_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    webinar_id: Mapped[int] = mapped_column(
        ForeignKey("webinars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    registrant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name_fa: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(255), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    info_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    receipt_file_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="webinar_registrations")
    webinar: Mapped[Webinar] = relationship(back_populates="registrations")


class WebinarLinkClaim(Base):
    __tablename__ = "webinar_link_claims"
    __table_args__ = (
        UniqueConstraint("user_id", "webinar_id", name="uq_webinar_claim_user_webinar"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    webinar_id: Mapped[int | None] = mapped_column(
        ForeignKey("webinars.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="webinar_claims")
    webinar: Mapped[Webinar | None] = relationship(back_populates="claims")


class GiftFileClaim(Base):
    __tablename__ = "gift_file_claims"
    __table_args__ = (UniqueConstraint("user_id", name="uq_gift_claim_user"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="gift_claims")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="support_messages")


class RequiredChannel(Base):
    """Force-join channels managed by admin (or seeded from env)."""

    __tablename__ = "required_channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BotSetting(Base):
    """Key-value store for bot runtime state."""

    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
