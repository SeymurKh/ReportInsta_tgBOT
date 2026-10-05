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
from instagram.client import InstagramClient, InstagramAPIError, utc_now_naive
from utils.timezones import day_is_unfinalized

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
        try:
            metrics_present = json.loads(next_row.metrics_present)
        except (TypeError, ValueError):
            logger.warning(
                "Cannot rebuild follower history for account %s: invalid metrics metadata on %s",
                account_id,
                cursor,
            )
            break
        if not isinstance(metrics_present, list) or "follower_count" not in metrics_present:
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
    - older                         -> fetch at most once per POSTS_COLD_INTERVAL_DAYS
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
        last = existing.insights_updated_at
        cold_interval = timedelta(days=max(1, settings.POSTS_COLD_INTERVAL_DAYS))
        return last is None or (now - last) >= cold_interval

    return should_refresh


def _skipped_sync_result(reason: str) -> dict:
    """Uniform result shape — callers (report generator) read these keys
    unconditionally, so a skipped sync must look like a partial sync."""
    return {
        "skipped": True,
        "reason": reason,
        "api_delay_dates": [],
        "partial": True,
        "partial_reasons": ["синхронизация уже выполняется"],
    }


async def sync_account_data(account, since_dt: datetime, until_dt: datetime) -> dict:
    """Synchronize one account and persist an observable sync status."""
    lock = _account_lock(account.id)
    if lock.locked():
        logger.warning("Skipping overlapping sync for @%s", account.username)
        return _skipped_sync_result("already_running")
    async with lock:
        owner = f"pid:{os.getpid()}:{uuid.uuid4().hex}"
        if not await crud.acquire_sync_lease(
            account.id, owner, ttl_seconds=settings.SYNC_LEASE_TTL_SECONDS
        ):
            logger.warning("Skipping cross-process overlapping sync for @%s", account.username)
            return _skipped_sync_result("lease_held")
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
            partial_reason = "; ".join(result.get("partial_reasons", [])) or None
            await crud.finish_sync_run(
                run_id,
                "partial" if result.get("partial") else "success",
                error=partial_reason,
                api_delay_days=len(result.get("api_delay_dates", [])),
                is_partial=bool(result.get("partial")),
            )
            await crud.update_account_sync_status(
                account.id,
                "partial" if result.get("partial") else "success",
                partial_reason,
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
    """Fetch fresh data from Instagram API and save to DB.

    Days are Instagram's own day buckets (US Pacific days): time-series
    metrics (reach, follower_count) come in one call, per-bucket account
    metrics come from single total_value requests over exactly those buckets.
    Days older than CACHE_FRESHNESS_HOURS are considered final and stay in the
    DB cache. All datetimes are naive UTC.
    """
    logger.info(f"Syncing @{account.username} ({since_dt.date()} — {until_dt.date()})")

    now = utc_now_naive()
    freshness_cutoff = now - timedelta(hours=settings.CACHE_FRESHNESS_HOURS)

    partial = False
    partial_reasons: list[str] = []
    failed_days: set[str] = set()
    day_payloads: dict[str, dict] = {}

    posts_cached = await crud.get_posts(account.id, since_dt, until_dt)
    # Posts: sync whenever the window touches the freshness zone or is empty
    refresh_posts = until_dt >= freshness_cutoff or not posts_cached

    user_info: dict = {}
    today_followers_delta = None

    client = InstagramClient(account.instagram_user_id, account.access_token)
    try:
        # 1. Instagram's own day buckets + time-series metrics
        buckets, series = [], {}
        try:
            buckets, series = await client.get_day_buckets(since_dt, until_dt)
        except InstagramAPIError as error:
            logger.error(f"Day buckets fetch failed for @{account.username}: {error}")
            partial = True
            partial_reasons.append("дневные метрики недоступны")

        saved = {
            row.date: row
            for row in await crud.get_daily_stats(account.id, since_dt.date(), until_dt.date())
        }

        # 2. Per-bucket account metrics for buckets that still change or are new
        for bucket in buckets:
            row = saved.get(bucket.label)
            needs_refresh = (
                bucket.end >= freshness_cutoff
                or row is None
                or getattr(row, "bucket_start", None) is None
            )
            if not needs_refresh and row is not None and row.metrics_present:
                try:
                    present = set(json.loads(row.metrics_present))
                except (TypeError, ValueError):
                    present = set()
                needs_refresh = "total_interactions" not in present
            if not needs_refresh:
                continue
            try:
                totals = await client.get_account_day_totals(bucket.start, bucket.end)
            except InstagramAPIError as error:
                logger.error(
                    "Day metrics failed for @%s (%s): %s",
                    account.username, bucket.day_key, error,
                )
                partial = True
                failed_days.add(bucket.day_key)
                totals = {}
            day_payloads[bucket.day_key] = {"bucket": bucket, "totals": totals}

        # 3. Persist refreshed buckets (time-series + account metrics together)
        for day_key, payload in sorted(day_payloads.items()):
            bucket = payload["bucket"]
            totals = payload["totals"]
            day_values = {name: values.get(day_key) for name, values in series.items()}
            if not day_values.get("follower_count") and day_is_unfinalized(bucket.label, now):
                # Instagram has not filled this day's follower delta yet: a zero
                # here means «unknown», so it must not claim provenance.
                day_values.pop("follower_count", None)
            present = [name for name, value in day_values.items() if value is not None]
            present += [name for name in totals]
            await crud.save_daily_stats(
                account_id=account.id,
                stats_date=bucket.label,
                followers=None,  # set from a date-aligned follower snapshot
                following=None,
                media_count=None,
                reach=day_values.get("reach"),
                follower_count=day_values.get("follower_count"),
                views=totals.get("views"),
                accounts_engaged=totals.get("accounts_engaged"),
                collected_at=now,
                is_partial=day_key in failed_days,
                metrics_present=present,
                bucket_start=bucket.start,
                bucket_end=bucket.end,
                account_metrics=totals,
            )
        # 4. Profile totals
        try:
            user_info = await client.get_user_info()
        except Exception as error:
            logger.warning(f"user_info fetch failed for @{account.username}: {error}")
            partial = True
            partial_reasons.append("не удалось получить профильные поля")

        if user_info.get("followers_count") is not None:
            await crud.update_current_followers(
                account.id, user_info["followers_count"], now
            )
            account.current_followers = user_info["followers_count"]
            account.current_followers_at = now
            try:
                today_followers_delta = await client.get_current_day_follower_change(now)
            except Exception as error:
                logger.warning(
                    "Today's follower delta unavailable for @%s: %s",
                    account.username, error,
                )
            if today_followers_delta is None:
                partial = True
                partial_reasons.append("Instagram не вернул дневной прирост подписчиков")
        else:
            partial = True
            partial_reasons.append("Instagram не вернул общий счётчик подписчиков")

        # 5. Posts
        if refresh_posts:
            insights_filter = make_insights_filter(posts_cached, now)
            posts_data, posts_partial = await client.collect_posts_with_insights(
                since_dt, until_dt, insights_filter=insights_filter
            )
            partial = partial or posts_partial
            if posts_partial:
                partial_reasons.append("часть Insights публикаций недоступна")
            for p in posts_data:
                p["account_id"] = account.id
            await crud.save_posts(posts_data)
            # Clean up posts deleted from Instagram — only when the fetch
            # was complete, otherwise we'd delete legit posts
            if not posts_partial:
                fetched_ids = {p["instagram_media_id"] for p in posts_data}
                await crud.delete_stale_posts(account.id, since_dt, until_dt, fetched_ids)

        # 6. Period totals (unique metrics) for the standard windows
        await _refresh_standard_period_snapshots(client, account, until_dt.date(), now)
    finally:
        await client.close()
    # A profile total is useful even when the separate daily-delta metric is absent.
    if user_info.get("followers_count") is not None:
        today = now.date()
        await crud.save_follower_snapshot(
            account_id=account.id,
            stats_date=today,
            followers=user_info["followers_count"],
            daily_delta=today_followers_delta,
            collected_at=now,
            following=user_info.get("follows_count"),
            media_count=user_info.get("media_count"),
        )
        await fix_followers_history(account.id, user_info["followers_count"], today)

    # Missing days: within 48h = expected IG API delay, older = bug
    expected_labels = [bucket.label for bucket in buckets]
    saved_stats = await crud.get_daily_stats(account.id, since_dt.date(), until_dt.date())
    saved_dates = {s.date for s in saved_stats}
    missing = [d for d in expected_labels if d not in saved_dates]

    api_delay_dates = []
    for d in missing:
        hours_since = (now - _day_end(d)).total_seconds() / 3600
        if hours_since <= 48:
            api_delay_dates.append(d)
        else:
            logger.error(f"BUG: Data missing outside API delay window: {d} (@{account.username})")
            partial = True
            partial_reasons.append(f"отсутствуют дневные данные за {d.isoformat()}")

    if partial_reasons:
        logger.warning(
            "Sync for @%s completed with partial data: %s",
            account.username,
            "; ".join(dict.fromkeys(partial_reasons)),
        )

    return {
        "api_delay_dates": [d.strftime("%d.%m") for d in api_delay_dates],
        "partial": partial,
        "partial_reasons": list(dict.fromkeys(partial_reasons)),
    }


def _day_start(d: date) -> datetime:
    """Return the UTC start-of-day for a calendar date."""
    return datetime(d.year, d.month, d.day)
async def _refresh_standard_period_snapshots(
    client: InstagramClient, account, period_end: date, now: datetime
) -> None:
    """Keep period totals (unique metrics) fresh for the standard windows."""
    for window_days in (7, 14, 30, 90):
        start = period_end - timedelta(days=window_days - 1)
        try:
            totals = await client.get_account_totals(_day_start(start), _day_end(period_end))
        except InstagramAPIError as error:
            logger.warning(
                "Period totals %sd failed for @%s: %s", window_days, account.username, error
            )
            continue
        await crud.upsert_period_snapshot(
            account.id, start, period_end, totals, collected_at=now
        )


def _snapshot_to_dict(snapshot) -> dict:
    return {name: getattr(snapshot, name, None) for name in crud.PERIOD_SNAPSHOT_METRICS}


async def ensure_period_snapshot(account, date_from: date, date_to: date) -> dict:
    """Authoritative API totals (unique metrics) for an arbitrary window.

    Cached in period_snapshots; refetched when older than 6 hours so recent
    windows pick up Instagram's 48h revisions.
    """
    now = utc_now_naive()
    snapshot = await crud.get_period_snapshot(account.id, date_from, date_to)
    if (
        snapshot is not None
        and getattr(snapshot, "collected_at", None) is not None
        and now - snapshot.collected_at <= timedelta(hours=6)
    ):
        return _snapshot_to_dict(snapshot)

    client = InstagramClient(account.instagram_user_id, account.access_token)
    try:
        totals = await client.get_account_totals(_day_start(date_from), _day_end(date_to))
    except InstagramAPIError as error:
        logger.warning(
            "Period totals fetch failed for @%s (%s..%s): %s",
            account.username, date_from, date_to, error,
        )
        return _snapshot_to_dict(snapshot) if snapshot is not None else {}
    finally:
        await client.close()

    await crud.upsert_period_snapshot(account.id, date_from, date_to, totals, collected_at=now)
    return totals