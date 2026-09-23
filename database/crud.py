from datetime import date, datetime, timedelta, timezone
from typing import Optional
import logging

from sqlalchemy import select, update, and_, text
from sqlalchemy.exc import IntegrityError

from database.engine import async_session_factory
from database.models import Account, DailyStats, Post, Story, NotificationDelivery, SyncRun, SyncLease

logger = logging.getLogger(__name__)


# ────────────────────── Accounts ──────────────────────


async def save_or_update_account(
    instagram_user_id: str,
    username: str,
    name: str,
    access_token: str,
) -> Account:
    async with async_session_factory() as session:
        existing = await session.execute(
            select(Account).where(Account.instagram_user_id == instagram_user_id)
        )
        account = existing.scalar_one_or_none()

        if account:
            account.username = username
            account.name = name
            account.access_token = access_token
            account.is_active = True
            account.updated_at = datetime.now(timezone.utc)
        else:
            account = Account(
                instagram_user_id=instagram_user_id,
                username=username,
                name=name,
                access_token=access_token,
                is_active=True,
            )
            session.add(account)

        await session.commit()
        await session.refresh(account)
        return account


async def get_all_accounts() -> list[Account]:
    async with async_session_factory() as session:
        result = await session.execute(select(Account).where(Account.is_active.is_(True)))
        return list(result.scalars().all())


async def get_account_by_username(username: str) -> Optional[Account]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Account).where(Account.username == username, Account.is_active.is_(True))
        )
        return result.scalar_one_or_none()


async def get_account_by_id(account_id: int) -> Optional[Account]:
    async with async_session_factory() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()


async def update_account_token(instagram_user_id: str, new_token: str) -> None:
    async with async_session_factory() as session:
        await session.execute(
            update(Account)
            .where(Account.instagram_user_id == instagram_user_id)
            .values(access_token=new_token, updated_at=datetime.now(timezone.utc))
        )
        await session.commit()


async def update_account_sync_status(
    account_id: int,
    status: str,
    error: str | None = None,
) -> None:
    """Persist the latest background/on-demand synchronization state."""
    values = {
        "sync_status": status,
        "last_sync_error": error[:1000] if error else None,
    }
    if status == "success":
        values["last_sync_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session_factory() as session:
        await session.execute(
            update(Account).where(Account.id == account_id).values(**values)
        )
        await session.commit()


async def start_sync_run(account_id: int, since_date: date, until_date: date,
                         sync_type: str = "full") -> int:
    async with async_session_factory() as session:
        run = SyncRun(
            account_id=account_id,
            sync_type=sync_type,
            since_date=since_date,
            until_date=until_date,
            status="running",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run.id


async def finish_sync_run(
    run_id: int,
    status: str,
    error: str | None = None,
    api_delay_days: int = 0,
    is_partial: bool = False,
) -> None:
    async with async_session_factory() as session:
        await session.execute(
            update(SyncRun)
            .where(SyncRun.id == run_id)
            .values(
                status=status,
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                error=error[:2000] if error else None,
                api_delay_days=api_delay_days,
                is_partial=is_partial,
            )
        )
        await session.commit()


async def acquire_sync_lease(
    account_id: int,
    owner: str,
    ttl_seconds: int = 7200,
    sync_type: str = "full",
) -> bool:
    """Acquire a DB-backed lease, reclaiming only expired leases."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + timedelta(seconds=ttl_seconds)
    async with async_session_factory() as session:
        session.add(SyncLease(
            account_id=account_id,
            sync_type=sync_type,
            owner=owner,
            acquired_at=now,
            expires_at=expires_at,
        ))
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()

        result = await session.execute(
            update(SyncLease)
            .where(
                SyncLease.account_id == account_id,
                SyncLease.sync_type == sync_type,
                SyncLease.expires_at <= now,
            )
            .values(owner=owner, acquired_at=now, expires_at=expires_at)
        )
        await session.commit()
        return result.rowcount == 1


async def release_sync_lease(account_id: int, owner: str, sync_type: str = "full") -> None:
    async with async_session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM sync_leases "
                "WHERE account_id = :account_id AND sync_type = :sync_type AND owner = :owner"
            ),
            {"account_id": account_id, "sync_type": sync_type, "owner": owner},
        )
        await session.commit()


async def claim_notification_delivery(
    delivery_key: str,
    notification_type: str,
    recipient_id: int | str,
) -> bool:
    """Atomically claim a scheduled notification key.

    A unique constraint makes this safe when two scheduler instances race.
    """
    async with async_session_factory() as session:
        session.add(NotificationDelivery(
            delivery_key=delivery_key,
            notification_type=notification_type,
            recipient_id=str(recipient_id),
        ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
        return True


async def release_notification_delivery(delivery_key: str) -> None:
    async with async_session_factory() as session:
        await session.execute(
            text("DELETE FROM notification_deliveries WHERE delivery_key = :key"),
            {"key": delivery_key},
        )
        await session.commit()


# ────────────────────── Daily Stats ──────────────────────


async def save_daily_stats(
    account_id: int,
    stats_date: date,
    followers: int | None,
    following: int | None,
    media_count: int | None,
    reach: int | None,
    follower_count: int | None,
    views: int | None = None,
    accounts_engaged: int | None = None,
    collected_at: datetime | None = None,
    is_partial: bool | None = None,
) -> None:
    async with async_session_factory() as session:
        existing = await session.execute(
            select(DailyStats).where(
                DailyStats.account_id == account_id, DailyStats.date == stats_date
            )
        )
        row = existing.scalar_one_or_none()

        if row:
            for field, value in {
                "followers": followers,
                "following": following,
                "media_count": media_count,
                "reach": reach,
                "follower_count": follower_count,
                "views": views,
                "accounts_engaged": accounts_engaged,
                "collected_at": collected_at,
                "is_partial": is_partial,
            }.items():
                if value is not None:
                    setattr(row, field, value)
        else:
            session.add(DailyStats(
                account_id=account_id, date=stats_date,
                followers=followers or 0, following=following or 0,
                media_count=media_count or 0, reach=reach or 0,
                follower_count=follower_count or 0,
                views=views or 0, accounts_engaged=accounts_engaged or 0,
                collected_at=collected_at,
                is_partial=bool(is_partial),
            ))

        await session.commit()


async def update_followers_field(account_id: int, stats_date: date, followers: int) -> None:
    """Update only the followers field for a specific day."""
    async with async_session_factory() as session:
        await session.execute(
            update(DailyStats)
            .where(DailyStats.account_id == account_id, DailyStats.date == stats_date)
            .values(followers=followers)
        )
        await session.commit()


async def bulk_update_followers(account_id: int, followers_by_date: dict[date, int]) -> None:
    """Update the followers field for many days in a single transaction."""
    if not followers_by_date:
        return
    async with async_session_factory() as session:
        result = await session.execute(
            select(DailyStats).where(
                DailyStats.account_id == account_id,
                DailyStats.date.in_(list(followers_by_date.keys())),
            )
        )
        for row in result.scalars().all():
            if row.date in followers_by_date:
                row.followers = followers_by_date[row.date]
        await session.commit()


async def get_daily_stats(
    account_id: int, date_from: date, date_to: date
) -> list[DailyStats]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DailyStats)
            .where(
                and_(
                    DailyStats.account_id == account_id,
                    DailyStats.date >= date_from,
                    DailyStats.date <= date_to,
                )
            )
            .order_by(DailyStats.date)
        )
        return list(result.scalars().all())


async def get_latest_stats(account_id: int) -> Optional[DailyStats]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DailyStats)
            .where(DailyStats.account_id == account_id)
            .order_by(DailyStats.date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


# ────────────────────── Posts ──────────────────────


async def save_posts(posts_data: list[dict]) -> None:
    """Upsert posts. Posts with skip_insights=True keep their stored insight
    metrics (reach/saved/shares/views/total_interactions) — only the free
    fields (likes/comments/caption/etc.) are updated. Prevents overwriting
    good data with zeros for posts we intentionally did not re-fetch."""
    if not posts_data:
        return
    async with async_session_factory() as session:
        for post in posts_data:
            post = dict(post)
            skip_insights = post.pop("skip_insights", False)
            existing = await session.execute(
                select(Post).where(Post.instagram_media_id == post["instagram_media_id"])
            )
            row = existing.scalar_one_or_none()

            if row:
                if post.get("media_type") is not None:
                    row.media_type = post["media_type"]
                if post.get("caption") is not None:
                    row.caption = post["caption"]
                if post.get("permalink") is not None:
                    row.permalink = post["permalink"]
                if post.get("likes") is not None:
                    row.likes = post["likes"]
                if post.get("comments") is not None:
                    row.comments = post["comments"]
                if not skip_insights:
                    insights_updated = False
                    for field in ("saved", "shares", "reach", "total_interactions", "views"):
                        value = post.get(field)
                        if value is not None:
                            setattr(row, field, value)
                            insights_updated = True
                    if insights_updated:
                        row.insights_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                post["caption"] = post.get("caption") or ""
                post["permalink"] = post.get("permalink") or ""
                post["likes"] = post.get("likes") or 0
                post["comments"] = post.get("comments") or 0
                has_insights = any(
                    post.get(field) is not None
                    for field in ("saved", "shares", "reach", "total_interactions", "views")
                )
                for field in ("saved", "shares", "reach", "total_interactions", "views"):
                    post[field] = post.get(field) or 0
                if not skip_insights and has_insights:
                    post["insights_updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
                session.add(Post(**post))

        await session.commit()


async def delete_stale_posts(account_id: int, date_from: datetime, date_to: datetime, current_media_ids: set[str]) -> int:
    """Delete posts from DB that are no longer returned by Instagram API (deleted posts).
    Returns number of deleted posts."""
    async with async_session_factory() as session:
        existing = await session.execute(
            select(Post).where(
                and_(
                    Post.account_id == account_id,
                    Post.timestamp >= date_from,
                    Post.timestamp <= date_to,
                )
            )
        )
        existing_posts = existing.scalars().all()
        to_delete = [p for p in existing_posts if p.instagram_media_id not in current_media_ids]

        if to_delete:
            for p in to_delete:
                await session.delete(p)
            await session.commit()
            logger.info(f"Deleted {len(to_delete)} stale posts for account {account_id}")

        return len(to_delete)


async def get_posts(
    account_id: int, date_from: datetime, date_to: datetime
) -> list[Post]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Post)
            .where(
                and_(
                    Post.account_id == account_id,
                    Post.timestamp >= date_from,
                    Post.timestamp <= date_to,
                )
            )
            .order_by(Post.timestamp.desc())
        )
        return list(result.scalars().all())


async def get_post_by_media_id(media_id: str) -> Optional[Post]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Post).where(Post.instagram_media_id == media_id)
        )
        return result.scalar_one_or_none()


async def get_top_posts(
    account_id: int,
    date_from: datetime,
    date_to: datetime,
    metric: str = "total_interactions",
    limit: int = 5,
) -> list[Post]:
    async with async_session_factory() as session:
        order_col = getattr(Post, metric, Post.total_interactions)
        result = await session.execute(
            select(Post)
            .where(
                and_(
                    Post.account_id == account_id,
                    Post.timestamp >= date_from,
                    Post.timestamp <= date_to,
                )
            )
            .order_by(order_col.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ────────────────────── Stories ──────────────────────

# Fields of Story that are filled from insight metrics
_STORY_METRIC_FIELDS = (
    "views", "reach", "replies", "shares", "total_interactions",
    "profile_activity", "follows",
    "tap_forward", "tap_back", "tap_exit", "swipe_forward",
)


async def upsert_story(account_id: int, story: dict) -> Story:
    """Insert a new story or update mutable fields of an existing one.

    `story` keys: instagram_media_id, media_type, permalink, timestamp,
    expires_at (+ optional metric fields).
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Story).where(Story.instagram_media_id == story["instagram_media_id"])
        )
        row = result.scalar_one_or_none()

        if row:
            row.media_type = story.get("media_type", row.media_type)
            row.permalink = story.get("permalink", row.permalink)
            row.is_active = True
        else:
            row = Story(
                account_id=account_id,
                instagram_media_id=story["instagram_media_id"],
                media_type=story.get("media_type", "IMAGE"),
                permalink=story.get("permalink", ""),
                timestamp=story["timestamp"],
                expires_at=story["expires_at"],
                is_active=True,
            )
            session.add(row)

        await session.commit()
        await session.refresh(row)
        return row


async def update_story_insights(instagram_media_id: str, metrics: dict) -> None:
    """Update insight metrics of a story. Values never decrease — keep the max,
    because a story added to highlights may return inconsistent data."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Story).where(Story.instagram_media_id == instagram_media_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return
        for field in _STORY_METRIC_FIELDS:
            value = metrics.get(field)
            if value is not None:
                setattr(row, field, max(getattr(row, field) or 0, value))
        row.insights_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()


async def mark_stories_inactive(account_id: int, active_media_ids: set[str]) -> None:
    """Mark stories that are no longer returned by /stories as expired."""
    async with async_session_factory() as session:
        await session.execute(
            update(Story)
            .where(
                Story.account_id == account_id,
                Story.is_active.is_(True),
                Story.instagram_media_id.not_in(active_media_ids or {""}),
            )
            .values(is_active=False)
        )
        await session.commit()


async def get_stories(account_id: int, date_from: datetime, date_to: datetime) -> list[Story]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Story)
            .where(
                and_(
                    Story.account_id == account_id,
                    Story.timestamp >= date_from,
                    Story.timestamp <= date_to,
                )
            )
            .order_by(Story.timestamp)
        )
        return list(result.scalars().all())


async def get_stories_for_insights_refresh(account_id: int, max_age_hours: int) -> list[Story]:
    """Stories published within the last `max_age_hours` whose insights can
    still be refreshed (IG keeps story insights only ~24h)."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max_age_hours)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Story).where(
                and_(
                    Story.account_id == account_id,
                    Story.timestamp >= cutoff,
                )
            )
        )
        return list(result.scalars().all())


async def get_all_daily_stats_dates(account_id: int) -> list[DailyStats]:
    """All daily stat rows for an account, ordered by date."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(DailyStats)
            .where(DailyStats.account_id == account_id)
            .order_by(DailyStats.date)
        )
        return list(result.scalars().all())


async def clear_all_data() -> None:
    """Delete all data from all tables. Used for reset."""
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM sync_leases"))
        await session.execute(text("DELETE FROM sync_runs"))
        await session.execute(text("DELETE FROM notification_deliveries"))
        await session.execute(text("DELETE FROM stories"))
        await session.execute(text("DELETE FROM posts"))
        await session.execute(text("DELETE FROM daily_stats"))
        await session.execute(text("DELETE FROM accounts"))
        await session.commit()
