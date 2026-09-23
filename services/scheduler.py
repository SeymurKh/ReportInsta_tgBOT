"""Background tasks: stories polling, data sync and daily digest."""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from config import settings
from database import crud
from instagram.client import utc_now_naive
from services.data_sync import sync_account_data
from services.stories_collector import collect_stories_for_all_accounts
from services.maintenance import create_configured_backup, collect_sync_health_issues

logger = logging.getLogger(__name__)


async def stories_polling_loop(bot: Bot) -> None:
    """Collect stories every STORIES_POLL_INTERVAL_HOURS hours."""
    interval = max(settings.STORIES_POLL_INTERVAL_HOURS, 0.5) * 3600
    logger.info("Stories polling started (every %sh)", settings.STORIES_POLL_INTERVAL_HOURS)
    while True:
        try:
            await collect_stories_for_all_accounts(bot=bot)
        except Exception as error:
            logger.error("Stories polling round failed: %s", error, exc_info=True)
        await asyncio.sleep(interval)


async def daily_report_loop(bot: Bot) -> None:
    """Send one compact digest per calendar day in the configured timezone."""
    from reports.generator import generate_daily_digest

    app_timezone = ZoneInfo(settings.APP_TIMEZONE)
    logger.info(
        "Daily report started (at %s:00 %s)",
        settings.DAILY_REPORT_HOUR,
        settings.APP_TIMEZONE,
    )
    while True:
        now = datetime.now(app_timezone)
        if now.hour >= settings.DAILY_REPORT_HOUR:
            delivery_key = f"daily_digest:{now.date().isoformat()}"
            try:
                accounts = await crud.get_all_accounts()
                if accounts:
                    claimed = await crud.claim_notification_delivery(
                        delivery_key, "daily_digest", settings.ADMIN_TELEGRAM_ID
                    )
                    if not claimed:
                        await asyncio.sleep(60)
                        continue

                    lines = ["🌅 Ежедневная сводка за вчера:\n"]
                    for account in accounts:
                        try:
                            lines.append(await generate_daily_digest(
                                account,
                                target_date=now.date() - timedelta(days=1),
                            ))
                        except Exception as error:
                            logger.error(
                                "Digest failed for @%s: %s",
                                account.username,
                                error,
                                exc_info=True,
                            )
                            lines.append(f"@{account.username}: ❌ ошибка сбора ({error})")
                    try:
                        await bot.send_message(
                            settings.ADMIN_TELEGRAM_ID,
                            "\n\n".join(lines),
                        )
                    except Exception:
                        # Allow a retry if Telegram was temporarily unavailable.
                        await crud.release_notification_delivery(delivery_key)
                        raise
            except Exception as error:
                logger.error("Daily report failed: %s", error, exc_info=True)
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
                await _sync_with_backoff(account, since_dt, until_dt)
        except Exception:
            logger.error("Data sync round failed", exc_info=True)
        await asyncio.sleep(interval)


async def maintenance_loop(bot: Bot) -> None:
    """Run local maintenance without blocking the event loop."""
    interval = max(settings.BACKUP_INTERVAL_HOURS, 1) * 3600
    logger.info("Maintenance started (backups every %sh)", settings.BACKUP_INTERVAL_HOURS)
    while True:
        try:
            if settings.BACKUP_ENABLED:
                await asyncio.to_thread(create_configured_backup)
            if settings.ADMIN_TELEGRAM_ID:
                issues = await collect_sync_health_issues()
                if issues:
                    key = f"sync_health:{datetime.now(ZoneInfo(settings.APP_TIMEZONE)).date().isoformat()}"
                    if await crud.claim_notification_delivery(
                        key, "sync_health", settings.ADMIN_TELEGRAM_ID
                    ):
                        try:
                            await bot.send_message(
                                settings.ADMIN_TELEGRAM_ID,
                                "⚠️ Проблемы актуальности данных:\n" + "\n".join(f"• {item}" for item in issues),
                                parse_mode=None,
                            )
                        except Exception:
                            await crud.release_notification_delivery(key)
                            raise
        except Exception:
            logger.error("Maintenance round failed", exc_info=True)
        await asyncio.sleep(interval)


async def _sync_with_backoff(account, since_dt: datetime, until_dt: datetime) -> None:
    """Retry a complete account sync after transient failures.

    The Instagram client already retries individual requests. This outer retry
    covers failures between requests, DB hiccups and interrupted sync rounds.
    """
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            result = await sync_account_data(account, since_dt, until_dt)
            if result.get("skipped"):
                return
            return
        except Exception:
            if attempt == max_attempts:
                logger.error(
                    "Data sync failed permanently for @%s after %d attempts",
                    account.username, max_attempts, exc_info=True,
                )
                return
            delay = min(300, 2 ** (attempt - 1) * 10)
            logger.warning(
                "Data sync attempt %d/%d failed for @%s; retrying in %ss",
                attempt, max_attempts, account.username, delay,
                exc_info=True,
            )
            await asyncio.sleep(delay)


def start_background_tasks(bot: Bot) -> list[asyncio.Task]:
    """Start enabled background tasks and return them for shutdown."""
    tasks: list[asyncio.Task] = []
    if settings.STORIES_POLL_ENABLED:
        tasks.append(asyncio.create_task(stories_polling_loop(bot)))
    if settings.DAILY_REPORT_ENABLED and settings.ADMIN_TELEGRAM_ID:
        tasks.append(asyncio.create_task(daily_report_loop(bot)))
    if settings.DATA_SYNC_ENABLED:
        tasks.append(asyncio.create_task(data_sync_loop()))
    if settings.BACKUP_ENABLED:
        tasks.append(asyncio.create_task(maintenance_loop(bot)))
    return tasks


async def stop_background_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
