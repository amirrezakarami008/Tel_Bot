"""Handler registration."""

from telegram.ext import Application

from bot.handlers import admin, admin_panel, gift_files, start, support, webinar


def register_handlers(application: Application) -> None:
    admin_panel.register(application)
    start.register(application)
    webinar.register(application)
    gift_files.register(application)
    admin.register(application)
    # Support last so it remains the catch-all for unmatched private messages.
    support.register(application)
