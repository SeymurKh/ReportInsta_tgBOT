from aiogram import Router

from bot.handlers import common, reports, comparison, ai_chat

# Order matters: ai_chat contains the catch-all text handler and must be last.
router = Router()
router.include_routers(common.router, reports.router, comparison.router, ai_chat.router)
