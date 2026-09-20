import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database.engine import init_db
from database import crud
from bot.handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def seed_accounts():
    """Save configured Instagram accounts to DB."""
    for acc in settings.ACCOUNTS:
        await crud.save_or_update_account(
            instagram_user_id=acc["user_id"],
            username=acc["name"],
            name=acc["name"],
            access_token=acc["access_token"],
        )
        logger.info(f"Account @{acc['name']} saved to DB")


async def main():
    logger.info("Starting bot...")

    await init_db()
    logger.info("Database initialized")

    # Reset database if --reset flag is passed
    if "--reset" in sys.argv:
        await crud.clear_all_data()
        logger.info("Database cleared (--reset)")

    await seed_accounts()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot is running!")
    try:
        await dp.start_polling(bot)
    finally:
        from instagram.client import close_shared_session
        await close_shared_session()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())