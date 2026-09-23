"""Background collection of Instagram stories.

Stories live 24h and their insights are available only ~24h after
publishing, so they must be polled regularly — they cannot be fetched
retroactively. This module is used both by the scheduler and by report
generation (to refresh fresh stories on demand).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from config import settings
from database import crud
from instagram.client import (
    InstagramClient, InstagramAPIError, TokenExpiredError, parse_ig_timestamp,
)

logger = logging.getLogger(__name__)

# account_id -> last time we notified admin about an expired token
_token_notify_cooldown: dict[int, datetime] = {}
NOTIFY_COOLDOWN = timedelta(hours=6)


async def _notify_admin_token_expired(bot: Bot | None, account) -> None:
    if bot is None or not settings.ADMIN_TELEGRAM_ID:
        return
    last = _token_notify_cooldown.get(account.id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if last and now - last < NOTIFY_COOLDOWN:
        return
    _token_notify_cooldown[account.id] = now
    try:
        await bot.send_message(
            settings.ADMIN_TELEGRAM_ID,
            f"🔑 Токен аккаунта @{account.username} истёк или недействителен.\n"
            "Обновите access_token в INSTAGRAM_ACCOUNTS (.env) и перезапустите бота.",
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about expired token: {e}")


async def collect_stories_for_account(account, bot: Bot | None = None) -> int:
    """Collect active stories of one account and refresh insights of fresh
    ones. Returns number of stories seen/updated.

    Raises TokenExpiredError so callers can react (scheduler notifies admin).
    """
    client = InstagramClient(account.instagram_user_id, account.access_token)
    try:
        active = await client.get_active_stories()
    except TokenExpiredError:
        await _notify_admin_token_expired(bot, account)
        raise
    except InstagramAPIError as e:
        logger.warning(f"Stories fetch failed for @{account.username}: {e}")
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_ids: set[str] = set()
    seen = 0

    for item in active:
        ts_str = item.get("timestamp")
        if not ts_str:
            continue
        media_id = item["id"]
        active_ids.add(media_id)
        ts = parse_ig_timestamp(ts_str)
        await crud.upsert_story(account.id, {
            "instagram_media_id": media_id,
            "media_type": item.get("media_type", "IMAGE"),
            "permalink": item.get("permalink", ""),
            "timestamp": ts,
            "expires_at": ts + timedelta(hours=24),
        })
        seen += 1

    # Mark stories that disappeared from /stories as expired
    await crud.mark_stories_inactive(account.id, active_ids)

    # Refresh insights of all stories still inside the insights window
    fresh = await crud.get_stories_for_insights_refresh(
        account.id, settings.STORIES_INSIGHTS_WINDOW_HOURS
    )
    semaphore = asyncio.Semaphore(5)

    async def refresh_one(story):
        async with semaphore:
            metrics = await client.get_story_insights(story.instagram_media_id)
            if metrics:
                await crud.update_story_insights(story.instagram_media_id, metrics)

    if fresh:
        results = await asyncio.gather(
            *(refresh_one(s) for s in fresh), return_exceptions=True
        )
        for story, result in zip(fresh, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Story insights refresh failed for %s: %s",
                    story.instagram_media_id,
                    result,
                )

    logger.info(f"Stories @{account.username}: {seen} active, {len(fresh)} insights refreshed")
    return seen


async def collect_stories_for_all_accounts(bot: Bot | None = None) -> None:
    """One polling round over all active accounts."""
    accounts = await crud.get_all_accounts()
    for account in accounts:
        try:
            await collect_stories_for_account(account, bot=bot)
        except TokenExpiredError:
            continue  # admin already notified
        except Exception as e:
            logger.error(f"Stories collection error @{account.username}: {e}", exc_info=True)
