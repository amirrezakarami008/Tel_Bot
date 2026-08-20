"""Bot entrypoint: logging, database init, polling Application."""

from __future__ import annotations

import logging
import sys

from telegram.ext import Application, ApplicationBuilder

from bot.config import get_settings
from bot.database.session import init_db
from bot.handlers import register_handlers


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


async def post_init(application: Application) -> None:
    del application
    logging.getLogger(__name__).info("Initializing database...")
    await init_db()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(post_init)
        .build()
    )
    register_handlers(application)

    logger.info("Starting bot in polling mode...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
