"""Background tasks: stories polling + daily auto-report to admin."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot

from config import settings
from database import crud
from instagram.client import utc_now_naive
from services.data_sync import sync_account_data
from services.stories_collector import collect_stories_for_all_accounts

logger = logging.getLogger(__name__)


async def stories_polling_loop(bot: Bot) -> None:
    """Collect stories every STORIES_POLL_INTERVAL_HOURS hours."""
    interval = max(settings.STORIES_POLL_INTERVAL_HOURS, 0.5) * 3600
    logger.info(f"Stories polling started (every {settings.STORIES_POLL_INTERVAL_HOURS}h)")
    while True:
        try:
            await collect_stories_for_all_accounts(bot=bot)
        except Exception as e:
            logger.error(f"Stories polling round failed: {e}", exc_info=True)
        await asyncio.sleep(interval)


async def daily_report_loop(bot: Bot) -> None:
    """Send a compact digest for yesterday to the admin every day at
    DAILY_REPORT_HOUR (local server time)."""
    from reports.generator import generate_daily_digest

    logger.info(f"Daily report started (at {settings.DAILY_REPORT_HOUR}:00 local)")
    last_sent: date | None = None
    while True:
        now = datetime.now()
        # `>=` makes the first run reliable even when the process starts after
        # the configured hour. `last_sent` prevents duplicates within a day.
        if now.hour >= settings.DAILY_REPORT_HOUR and last_sent != now.date():
            last_sent = now.date()
            try:
                accounts = await crud.get_all_accounts()
                if accounts:
                    lines = ["🌅 Ежедневная сводка за вчера:\n"]
                    for account in accounts:
                        try:
                            lines.append(await generate_daily_digest(account))
                        except Exception as e:
                            logger.error(f"Digest failed for @{account.username}: {e}", exc_info=True)
                            lines.append(f"@{account.username}: ❌ ошибка сбора ({e})")
                    await bot.send_message(settings.ADMIN_TELEGRAM_ID, "\n\n".join(lines))
            except Exception as e:
                logger.error(f"Daily report failed: {e}", exc_info=True)
        await asyncio.sleep(60)


async def data_sync_loop() -> None:
    """Keep the rolling data window fresh in the background."""
    interval = max(settings.DATA_SYNC_INTERVAL_HOURS, 0.5) * 3600
    logger.info(
        "Data sync started (every %sh, window %sd)",
        settings.DATA_SYNC_INTERVAL_HOURS,
        settings.DATA_SYNC_WINDOW_DAYS,
    )
    while True:
        try:
            now = utc_now_naive()
            until_dt = datetime(now.year, now.month, now.day) - timedelta(seconds=1)
            since_dt = datetime(now.year, now.month, now.day) - timedelta(
                days=settings.DATA_SYNC_WINDOW_DAYS
            )
            accounts = await crud.get_all_accounts()
            for account in accounts:
                try:
                    await sync_account_data(account, since_dt, until_dt)
                except Exception:
                    logger.error("Data sync failed for @%s", account.username, exc_info=True)
        except Exception:
            logger.error("Data sync round failed", exc_info=True)
        await asyncio.sleep(interval)


def start_background_tasks(bot: Bot) -> list[asyncio.Task]:
    """Start enabled background tasks. Returns the list of tasks so the
    caller can cancel them on shutdown."""
    tasks: list[asyncio.Task] = []
    if settings.STORIES_POLL_ENABLED:
        tasks.append(asyncio.create_task(stories_polling_loop(bot)))
    if settings.DAILY_REPORT_ENABLED and settings.ADMIN_TELEGRAM_ID:
        tasks.append(asyncio.create_task(daily_report_loop(bot)))
    if settings.DATA_SYNC_ENABLED:
        tasks.append(asyncio.create_task(data_sync_loop()))
    return tasks


async def stop_background_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
