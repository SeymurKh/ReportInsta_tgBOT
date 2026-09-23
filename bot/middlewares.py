import logging

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from config import settings

logger = logging.getLogger(__name__)


class AdminMiddleware(BaseMiddleware):
    """Allow only the admin. Everyone else gets a reply to /start
    and is silently ignored otherwise."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        username = (getattr(user, "username", "") or "").lower()
        username = username.lstrip("@")
        allowed_by_username = username in settings.ALLOWED_TELEGRAM_USERNAMES
        if user is not None and (
            user.id == settings.ADMIN_TELEGRAM_ID or allowed_by_username
        ):
            return await handler(event, data)

        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            await event.answer("⛔ Доступ запрещён.")
        return None
