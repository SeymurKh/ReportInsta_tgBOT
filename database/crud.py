from datetime import date, datetime, timezone
from typing import Optional
import logging

from sqlalchemy import select, update, and_, text

from database.engine import async_session_factory
from database.models import Account, DailyStats, Post

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


# ────────────────────── Daily Stats ──────────────────────


async def save_daily_stats(
    account_id: int,
    stats_date: date,
    followers: int,
    following: int,
    media_count: int,
    reach: int,
    follower_count: int,
    views: int = 0,
    accounts_engaged: int = 0,
) -> None:
    async with async_session_factory() as session:
        existing = await session.execute(
            select(DailyStats).where(
                DailyStats.account_id == account_id, DailyStats.date == stats_date
            )
        )
        row = existing.scalar_one_or_none()

        if row:
            row.followers = followers
            row.following = following
            row.media_count = media_count
            row.reach = reach
            row.follower_count = follower_count
            row.views = views
            row.accounts_engaged = accounts_engaged
        else:
            session.add(DailyStats(
                account_id=account_id, date=stats_date,
                followers=followers, following=following, media_count=media_count,
                reach=reach, follower_count=follower_count,
                views=views, accounts_engaged=accounts_engaged,
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
    if not posts_data:
        return
    async with async_session_factory() as session:
        for post in posts_data:
            existing = await session.execute(
                select(Post).where(Post.instagram_media_id == post["instagram_media_id"])
            )
            row = existing.scalar_one_or_none()

            if row:
                row.media_type = post["media_type"]
                row.caption = post.get("caption", "")
                row.permalink = post.get("permalink", "")
                row.likes = post["likes"]
                row.comments = post["comments"]
                row.saved = post["saved"]
                row.shares = post.get("shares", 0)
                row.reach = post.get("reach", 0)
                row.total_interactions = post.get("total_interactions", 0)
                row.views = post.get("views", 0)
            else:
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


async def clear_all_data() -> None:
    """Delete all data from all tables. Used for reset."""
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM posts"))
        await session.execute(text("DELETE FROM daily_stats"))
        await session.execute(text("DELETE FROM accounts"))
        await session.commit()