"""Full data synchronization: daily stats + posts from Instagram API into DB.

Shared by report generation (on demand) and the background scheduler
(keeps the whole DB fresh). Uses a 48h cache for daily stats and a tiered
refresh policy for post insights to stay well under API rate limits.

All datetimes are naive UTC.
"""
import logging
import asyncio
import os
import uuid
import json
from datetime import date, datetime, timedelta, timezone

from config import settings
from database import crud
from instagram.client import InstagramClient, utc_now_naive

logger = logging.getLogger(__name__)

_account_sync_locks: dict[int, asyncio.Lock] = {}


def _account_lock(account_id: int) -> asyncio.Lock:
    return _account_sync_locks.setdefault(account_id, asyncio.Lock())


def _to_unix(dt: datetime) -> int:
    """Naive UTC datetime -> unix timestamp."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _day_end(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59)


async def fix_followers_history(
    account_id: int, current_followers: int, anchor_date: date
) -> None:
    """Rebuild only the continuous, known follower history before the snapshot."""
    if current_followers is None:
        return
    stats_list = await crud.get_all_daily_stats_dates(account_id)
    rows = {row.date: row for row in stats_list}
    anchor = rows.get(anchor_date)
    if anchor is None:
        return
    mapping = {anchor_date: current_followers}
    cursor = anchor_date
    while True:
        previous_date = cursor - timedelta(days=1)
        row = rows.get(previous_date)
        next_row = rows[cursor]
        if row is None or next_row.metrics_present is None:
            break
        if "follower_count" not in json.loads(next_row.metrics_present):
            break
        mapping[previous_date] = max(
            mapping[cursor] - (next_row.follower_count or 0), 0
        )
        cursor = previous_date
    await crud.bulk_update_followers(account_id, mapping)


def make_insights_filter(posts_cached: list, now: datetime):
    """Tiered refresh policy for post insights:
    - new post (not in DB)          -> always fetch
    - age <= POSTS_HOT_DAYS         -> fetch every sync
    - age <= POSTS_WARM_DAYS        -> fetch at most once per POSTS_WARM_INTERVAL_HOURS
    - older                         -> frozen (likes/comments still update for free)
    """
    cached = {p.instagram_media_id: p for p in posts_cached}
    hot = timedelta(days=settings.POSTS_HOT_DAYS)
    warm = timedelta(days=settings.POSTS_WARM_DAYS)
    warm_interval = timedelta(hours=settings.POSTS_WARM_INTERVAL_HOURS)

    def should_refresh(media_id: str, ts: datetime) -> bool:
        existing = cached.get(media_id)
        if existing is None:
            return True
        age = now - ts
        if (
            getattr(existing, "insights_present", None) is None
            and age <= timedelta(days=settings.DATA_SYNC_WINDOW_DAYS)
        ):
            return True
        if age <= hot:
            return True
        if age <= warm:
            last = existing.insights_updated_at
            return last is None or (now - last) >= warm_interval
        return False

    return should_refresh


async def sync_account_data(account, since_dt: datetime, until_dt: datetime) -> dict:
    """Synchronize one account and persist an observable sync status."""
    lock = _account_lock(account.id)
    if lock.locked():
        logger.warning("Skipping overlapping sync for @%s", account.username)
        return {"skipped": True, "reason": "already_running", "partial": True}
    async with lock:
        owner = f"pid:{os.getpid()}:{uuid.uuid4().hex}"
        if not await crud.acquire_sync_lease(
            account.id, owner, ttl_seconds=settings.SYNC_LEASE_TTL_SECONDS
        ):
            logger.warning("Skipping cross-process overlapping sync for @%s", account.username)
            return {"skipped": True, "reason": "lease_held", "partial": True}
        heartbeat = asyncio.create_task(_renew_lease(account.id, owner))
        run_id: int | None = None
        try:
            run_id = await crud.start_sync_run(account.id, since_dt.date(), until_dt.date())
            await crud.update_account_sync_status(account.id, "running")
            sync_task = asyncio.create_task(_sync_account_data_impl(account, since_dt, until_dt))
            done, _ = await asyncio.wait(
                {sync_task, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                sync_task.cancel()
                await asyncio.gather(sync_task, return_exceptions=True)
                raise RuntimeError("Sync lease was lost before the collection finished")
            result = await sync_task
        except Exception as error:
            if run_id is not None:
                await crud.finish_sync_run(run_id, "failed", str(error))
            await crud.update_account_sync_status(account.id, "failed", str(error))
            raise
        else:
            await crud.finish_sync_run(
                run_id,
                "partial" if result.get("partial") else "success",
                api_delay_days=len(result.get("api_delay_dates", [])),
                is_partial=bool(result.get("partial")),
            )
            await crud.update_account_sync_status(
                account.id,
                "partial" if result.get("partial") else "success",
                None,
            )
            return result
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await crud.release_sync_lease(account.id, owner)


async def _renew_lease(account_id: int, owner: str) -> None:
    ttl = settings.SYNC_LEASE_TTL_SECONDS
    interval = max(1, ttl // 3)
    while True:
        await asyncio.sleep(interval)
        try:
            renewed = await crud.renew_sync_lease(account_id, owner, ttl_seconds=ttl)
        except Exception:
            logger.exception("Could not renew sync lease for account %s", account_id)
            continue
        if not renewed:
            logger.error("Sync lease lost for account %s", account_id)
            return


async def _sync_account_data_impl(account, since_dt: datetime, until_dt: datetime) -> dict:
    """Fetch fresh data from Instagram API where needed and save to DB.

    Uses the DB cache for days older than CACHE_FRESHNESS_HOURS and the
    tiered refresh policy for post insights.
    Returns {"api_delay_dates": [...], "partial": bool}.
    All datetimes are naive UTC.
    """
    logger.info(f"Syncing @{account.username} ({since_dt.date()} — {until_dt.date()})")

    now = utc_now_naive()
    freshness_cutoff = now - timedelta(hours=settings.CACHE_FRESHNESS_HOURS)

    all_days = [since_dt.date() + timedelta(days=i)
                for i in range((until_dt.date() - since_dt.date()).days + 1)]
    saved = await crud.get_daily_stats(account.id, since_dt.date(), until_dt.date())
    saved_dates = {s.date for s in saved}
    saved_by_date = {s.date: s for s in saved}

    # Days that are missing or still "fresh" (IG data can change within 48h)
    days_to_fetch = {d for d in all_days
                     if d not in saved_dates
                     or _day_end(d) >= freshness_cutoff
                     or (
                         d >= now.date() - timedelta(days=settings.DATA_SYNC_WINDOW_DAYS)
                         and getattr(saved_by_date[d], "metrics_present", None) is None
                     )}

    # Posts: sync whenever the window touches the freshness zone or is empty
    posts_cached = await crud.get_posts(account.id, since_dt, until_dt)
    refresh_posts = until_dt >= freshness_cutoff or not posts_cached

    partial = False
    user_info: dict = {}
    today_followers_delta = None

    if days_to_fetch or refresh_posts:
        client = InstagramClient(account.instagram_user_id, account.access_token)
        try:
            if days_to_fetch:
                snapshot = await client.collect_full_snapshot(
                    _to_unix(since_dt), _to_unix(until_dt), days_to_fetch
                )
                user_info = snapshot["user_info"]
                partial = partial or snapshot.get("partial", False)
                partial_days = set(snapshot.get("partial_days", []))
                metric_presence = snapshot.get("metric_presence", {})
                for day_str, metrics in snapshot["insights"].items():
                    day_date = date.fromisoformat(day_str)
                    if day_date not in days_to_fetch:
                        continue  # don't overwrite cached stable days
                    await crud.save_daily_stats(
                        account_id=account.id,
                        stats_date=day_date,
                        followers=None,  # set from a date-aligned follower snapshot
                        following=user_info.get("follows_count"),
                        media_count=user_info.get("media_count"),
                        reach=metrics.get("reach"),
                        follower_count=metrics.get("follower_count"),
                        views=metrics.get("views"),
                        accounts_engaged=metrics.get("accounts_engaged"),
                        collected_at=now,
                        is_partial=day_str in partial_days,
                        metrics_present=metric_presence.get(day_str, []),
                    )
            else:
                try:
                    user_info = await client.get_user_info()
                except Exception as e:
                    logger.warning(f"user_info fetch failed for @{account.username}: {e}")
                    partial = True

            if user_info.get("followers_count") is not None:
                try:
                    today_followers_delta = await client.get_current_day_follower_change(now)
                except Exception as error:
                    logger.warning(
                        "Today's follower delta unavailable for @%s: %s",
                        account.username, error,
                    )
                if today_followers_delta is None:
                    partial = True

            if refresh_posts:
                insights_filter = make_insights_filter(posts_cached, now)
                posts_data, posts_partial = await client.collect_posts_with_insights(
                    since_dt, until_dt, insights_filter=insights_filter
                )
                partial = partial or posts_partial
                for p in posts_data:
                    p["account_id"] = account.id
                await crud.save_posts(posts_data)
                # Clean up posts deleted from Instagram — only when the fetch
                # was complete, otherwise we'd delete legit posts
                if not posts_partial:
                    fetched_ids = {p["instagram_media_id"] for p in posts_data}
                    await crud.delete_stale_posts(account.id, since_dt, until_dt, fetched_ids)
        finally:
            await client.close()

    # Store the current total against today's partial follower delta. This
    # prevents today's total from being assigned to yesterday's row.
    if user_info.get("followers_count") is not None and today_followers_delta is not None:
        today = now.date()
        await crud.save_daily_stats(
            account_id=account.id,
            stats_date=today,
            followers=user_info["followers_count"],
            following=None,
            media_count=None,
            reach=None,
            follower_count=today_followers_delta,
            collected_at=now,
            is_partial=True,
        )
        await fix_followers_history(account.id, user_info["followers_count"], today)

    # Missing days: within 48h = expected IG API delay, older = bug
    saved_stats = await crud.get_daily_stats(account.id, since_dt.date(), until_dt.date())
    saved_dates = {s.date for s in saved_stats}
    missing = [d for d in all_days if d not in saved_dates]

    api_delay_dates = []
    for d in missing:
        hours_since = (now - _day_end(d)).total_seconds() / 3600
        if hours_since <= 48:
            api_delay_dates.append(d)
        else:
            logger.error(f"BUG: Data missing outside API delay window: {d} (@{account.username})")

    return {
        "api_delay_dates": [d.strftime("%d.%m") for d in api_delay_dates],
        "partial": partial,
    }
