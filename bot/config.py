"""Central configuration loaded and validated from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _strip_inline_comment(value: str) -> str:
    """Remove trailing `# comment` while keeping values that are not comments."""
    return value.split("#", 1)[0].strip()


class Settings(BaseSettings):
    """Application settings sourced exclusively from `.env` / environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_telegram_ids: str = Field(..., alias="ADMIN_TELEGRAM_IDS")
    required_channels: str = Field(default="", alias="REQUIRED_CHANNELS")
    webinar_link: str | None = Field(default=None, alias="WEBINAR_LINK")
    gift_files_dir: Path = Field(default=Path("./gift_files"), alias="GIFT_FILES_DIR")
    database_url: str = Field(..., alias="DATABASE_URL")
    postgres_user: str | None = Field(default=None, alias="POSTGRES_USER")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")
    postgres_db: str | None = Field(default=None, alias="POSTGRES_DB")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Optional: http://host:port or socks5://host:port (needed when Telegram is blocked)
    telegram_proxy: str | None = Field(default=None, alias="TELEGRAM_PROXY")

    @field_validator("bot_token", "database_url", "admin_telegram_ids")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("required_channels", mode="before")
    @classmethod
    def strip_required_channels(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("webinar_link", "telegram_proxy", mode="before")
    @classmethod
    def empty_optional_as_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(sorted(allowed))}")
        return level

    @property
    def admin_ids(self) -> list[int]:
        ids: list[int] = []
        for part in self.admin_telegram_ids.split(","):
            part = _strip_inline_comment(part)
            if part:
                ids.append(int(part))
        if not ids:
            raise ValueError("ADMIN_TELEGRAM_IDS must contain at least one ID")
        return ids

    def env_channel_entries(self) -> list[dict[str, str]]:
        """Parse REQUIRED_CHANNELS for one-time DB seeding. Empty is allowed."""
        result: list[dict[str, str]] = []
        if not self.required_channels:
            return result
        for part in self.required_channels.split(","):
            part = _strip_inline_comment(part)
            if not part:
                continue

            username: str | None = None
            if ":" in part and not part.startswith("@"):
                id_part, user_part = part.split(":", 1)
                chat_id = id_part.strip()
                username = user_part.strip().lstrip("@") or None
            elif part.lstrip("-").isdigit():
                chat_id = part
            else:
                username = part.lstrip("@")
                chat_id = f"@{username}"

            entry: dict[str, str] = {"chat_id": chat_id}
            if username:
                entry["username"] = username
                entry["invite_link"] = f"https://t.me/{username}"
            result.append(entry)
        return result

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
