"""Full data synchronization: daily stats + posts from Instagram API into DB.

Shared by report generation (on demand) and the background scheduler
(keeps the whole DB fresh). Uses a 48h cache for daily stats and a tiered
refresh policy for post insights to stay well under API rate limits.

All datetimes are naive UTC.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from config import settings
from database import crud
from instagram.client import InstagramClient, utc_now_naive

logger = logging.getLogger(__name__)


def _to_unix(dt: datetime) -> int:
    """Naive UTC datetime -> unix timestamp."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _day_end(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59)


async def fix_followers_history(account_id: int, current_followers: int) -> None:
    """Recalculate the followers field for ALL stored days, working backwards
    from the known current total. Single bulk transaction."""
    if not current_followers:
        return
    stats_list = await crud.get_all_daily_stats_dates(account_id)
    if not stats_list:
        return

    mapping: dict[date, int] = {}
    prev_row = None
    for row in reversed(stats_list):
        if prev_row is None:
            total = current_followers
        else:
            # end_of_day(i) = end_of_day(i+1) - net_change(i+1)
            total = mapping[prev_row.date] - prev_row.follower_count
        mapping[row.date] = max(total, 0)
        prev_row = row

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
        if age <= hot:
            return True
        if age <= warm:
            last = existing.insights_updated_at
            return last is None or (now - last) >= warm_interval
        return False

    return should_refresh


async def sync_account_data(account, since_dt: datetime, until_dt: datetime) -> dict:
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

    # Days that are missing or still "fresh" (IG data can change within 48h)
    days_to_fetch = {d for d in all_days
                     if d not in saved_dates or _day_end(d) >= freshness_cutoff}

    # Posts: sync whenever the window touches the freshness zone or is empty
    posts_cached = await crud.get_posts(account.id, since_dt, until_dt)
    refresh_posts = until_dt >= freshness_cutoff or not posts_cached

    partial = False
    user_info: dict = {}

    if days_to_fetch or refresh_posts:
        client = InstagramClient(account.instagram_user_id, account.access_token)
        try:
            if days_to_fetch:
                snapshot = await client.collect_full_snapshot(
                    _to_unix(since_dt), _to_unix(until_dt), days_to_fetch
                )
                user_info = snapshot["user_info"]
                partial = partial or snapshot.get("partial", False)
                for day_str, metrics in snapshot["insights"].items():
                    day_date = date.fromisoformat(day_str)
                    if day_date not in days_to_fetch:
                        continue  # don't overwrite cached stable days
                    await crud.save_daily_stats(
                        account_id=account.id,
                        stats_date=day_date,
                        followers=None,  # recalculated below when available
                        following=user_info.get("follows_count"),
                        media_count=user_info.get("media_count"),
                        reach=metrics.get("reach"),
                        follower_count=metrics.get("follower_count"),
                        views=metrics.get("views"),
                        accounts_engaged=metrics.get("accounts_engaged"),
                    )
            else:
                try:
                    user_info = await client.get_user_info()
                except Exception as e:
                    logger.warning(f"user_info fetch failed for @{account.username}: {e}")

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

    # Anchor the followers history at the current total (full history recalc)
    if user_info.get("followers_count"):
        await fix_followers_history(account.id, user_info["followers_count"])

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
