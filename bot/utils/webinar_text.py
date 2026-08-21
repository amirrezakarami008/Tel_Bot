"""Webinar announcement text. Only the URL comes from env."""

from bot.config import get_settings

WEBINAR_TIME = "21:00"

PLACEHOLDER_TEXT = (
    "💻 لینک وبینار\n"
    "\n"
    f"⏰ ساعت {WEBINAR_TIME}\n"
    "\n"
    "⚠️ لطفا با نام و نام خانوادگی به عنوان شنونده وارد شوید."
)

WITH_LINK_TEMPLATE = (
    "💻 لینک وبینار :\n"
    "{link}\n"
    "\n"
    f"⏰ ساعت {WEBINAR_TIME}\n"
    "\n"
    "⚠️ لطفا با نام و نام خانوادگی به عنوان شنونده وارد شوید.\n"
    "\n"
    "توجه: نیاز به نصب نرم افزار یا برنامه خاصی نیست  "
    "با کلیک روی لینک بصورت تحت وب وارد فضای کلاس می‌شوید."
)


def build_webinar_message(link: str | None = None) -> str:
    """Build the user-facing webinar message. Empty link → placeholder without URL."""
    url = link if link is not None else get_settings().webinar_link
    if not url:
        return PLACEHOLDER_TEXT
    return WITH_LINK_TEMPLATE.format(link=url)
