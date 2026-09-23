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
        if user is not None and user.id == settings.ADMIN_TELEGRAM_ID:
            return await handler(event, data)

        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            await event.answer("⛔ Доступ запрещён.")
        return None
