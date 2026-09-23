import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher

from config import settings
from database.engine import init_db
from database import crud
from bot.handlers import router
from bot.middlewares import AdminMiddleware


def _setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        "logs/bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler])


logger = logging.getLogger(__name__)


async def seed_accounts() -> None:
    """Save configured Instagram accounts to DB."""
    for acc in settings.ACCOUNTS:
        await crud.save_or_update_account(
            instagram_user_id=acc["user_id"],
            username=acc["name"],
            name=acc["name"],
            access_token=acc["access_token"],
        )
        logger.info(f"Account @{acc['name']} saved to DB")


async def main() -> None:
    _setup_logging()
    logger.info("Starting bot...")

    # Fail fast on critical configuration problems
    errors, warnings = settings.validate()
    for w in warnings:
        logger.warning(f"CONFIG: {w}")
    if errors:
        for e in errors:
            logger.error(f"CONFIG: {e}")
        logger.error("Fix the configuration (.env) and restart. Exiting.")
        sys.exit(1)

    await init_db()
    logger.info("Database initialized")

    # Reset database if --reset flag is passed
    if "--reset" in sys.argv:
        await crud.clear_all_data()
        logger.info("Database cleared (--reset)")

    await seed_accounts()

    bot = Bot(token=settings.BOT_TOKEN)  # plain text everywhere; no parse_mode surprises
    dp = Dispatcher()

    # Global access control
    dp.message.middleware(AdminMiddleware())
    dp.callback_query.middleware(AdminMiddleware())

    dp.include_router(router)

    # Global error handler — notify admin instead of failing silently
    @dp.error()
    async def error_handler(event, *args, **kwargs):
        logger.error(f"Unhandled error: {event.exception}", exc_info=True)
        try:
            await bot.send_message(
                settings.ADMIN_TELEGRAM_ID,
                f"⚠️ Внутренняя ошибка бота:\n{type(event.exception).__name__}: {event.exception}",
            )
        except Exception:
            pass

    # Background tasks: stories polling + daily report
    from services.scheduler import start_background_tasks, stop_background_tasks
    bg_tasks = start_background_tasks(bot)

    logger.info("Bot is running!")
    try:
        await dp.start_polling(bot)
    finally:
        await stop_background_tasks(bg_tasks)
        from instagram.client import close_shared_session
        await close_shared_session()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
