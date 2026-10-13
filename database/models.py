from datetime import datetime, date, timezone
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, Date, DateTime,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instagram_user_id = Column(String(64), unique=True, nullable=False)
    username = Column(String(128), nullable=False)
    name = Column(String(256), default="")
    access_token = Column(String(512), nullable=False)
    is_active = Column(Boolean, default=True)
    sync_status = Column(String(32), default="never", nullable=False)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_error = Column(Text, nullable=True)
    current_followers = Column(Integer, nullable=True)
    current_followers_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    daily_stats = relationship("DailyStats", back_populates="account", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="account", cascade="all, delete-orphan")
    stories = relationship("Story", back_populates="account", cascade="all, delete-orphan")


class DailyStats(Base):
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)

    # Account-level metrics from Instagram API
    followers = Column(Integer, default=0)
    following = Column(Integer, default=0)
    media_count = Column(Integer, default=0)
    reach = Column(Integer, default=0)
    follower_count = Column(Integer, default=0)  # daily follower change
    views = Column(Integer, default=0)  # total views (reels, posts, stories)
    accounts_engaged = Column(Integer, default=0)  # unique accounts interacted
    collected_at = Column(DateTime, nullable=True)  # naive UTC
    is_partial = Column(Boolean, default=False, nullable=False)
    metrics_present = Column(Text, nullable=True)  # JSON list; NULL means legacy/unknown

    # Instagram day-bucket boundaries (naive UTC) — the row's `date` is the
    # Instagram insight day (UTC calendar day, stamped end_time = day 07:00).
    bucket_start = Column(DateTime, nullable=True)
    bucket_end = Column(DateTime, nullable=True)

    # Account-level metrics from Instagram account insights (NOT post sums)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    saves = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    total_interactions = Column(Integer, default=0)
    profile_views = Column(Integer, default=0)
    website_clicks = Column(Integer, default=0)

    # Extended account metrics (live-API probe 2026-10): reposts, contact
    # buttons, follows/unfollows split and the daily online-followers count.
    reposts = Column(Integer, default=0)
    follows = Column(Integer, default=0)  # follows_and_unfollows -> follows
    unfollows = Column(Integer, default=0)  # follows_and_unfollows -> unfollows
    profile_links_taps = Column(Integer, default=0)  # contact buttons total
    online_followers = Column(Integer, nullable=True)  # followers online that day
    breakdowns = Column(Text, nullable=True)  # JSON breakdown sets (see client)

    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_account_date"),
    )

    account = relationship("Account", back_populates="daily_stats")


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    instagram_media_id = Column(String(64), unique=True, nullable=False)

    media_type = Column(String(32), nullable=False)  # IMAGE, VIDEO, CAROUSEL_ALBUM, REELS
    caption = Column(Text, default="")
    permalink = Column(String(512), default="")
    timestamp = Column(DateTime, nullable=False)

    # Engagement metrics from Instagram API
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    saved = Column(Integer, default=0)
    shares = Column(Integer, default=0)

    # Reach & interactions
    reach = Column(Integer, default=0)
    total_interactions = Column(Integer, default=0)
    views = Column(Integer, default=0)  # for VIDEO/REELS only

    # When insight metrics (reach/saved/shares/views/total_interactions)
    # were last refreshed from the API. None = never. Used by the tiered
    # refresh logic in services/data_sync.py. Naive UTC.
    insights_updated_at = Column(DateTime, nullable=True)
    insights_present = Column(Text, nullable=True)  # JSON list; NULL means legacy/unknown

    # Extended media metrics (live-API probe 2026-10). Reels reject the
    # profile family and feed posts reject watch-time, so every column is
    # nullable and is only set when Instagram actually returned the metric.
    follows = Column(Integer, nullable=True)  # new followers from this media
    profile_visits = Column(Integer, nullable=True)
    profile_activity = Column(Integer, nullable=True)  # sum of profile actions
    profile_activity_breakdown = Column(Text, nullable=True)  # JSON action_type
    avg_watch_time = Column(Integer, nullable=True)  # ms, reels/videos
    total_watch_time = Column(Integer, nullable=True)  # ms, reels/videos

    account = relationship("Account", back_populates="posts")


class Story(Base):
    """Instagram story. Stories live 24h and their insights are available
    only ~24h after publishing, so they are collected by the background
    polling service (services/stories_collector.py), not on demand.

    All datetimes are stored as naive UTC.
    """
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    instagram_media_id = Column(String(64), unique=True, nullable=False)

    media_type = Column(String(32), default="IMAGE")  # IMAGE / VIDEO
    permalink = Column(String(512), default="")
    timestamp = Column(DateTime, nullable=False)      # published at (naive UTC)
    expires_at = Column(DateTime, nullable=False)     # timestamp + 24h (naive UTC)

    # Insight metrics (available only while the story is fresh)
    views = Column(Integer, default=0)
    reach = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    total_interactions = Column(Integer, default=0)
    profile_activity = Column(Integer, default=0)
    follows = Column(Integer, default=0)

    # Navigation breakdown (story_navigation_action_type)
    tap_forward = Column(Integer, default=0)
    tap_back = Column(Integer, default=0)
    tap_exit = Column(Integer, default=0)
    swipe_forward = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)
    insights_updated_at = Column(DateTime, nullable=True)
    metrics_present = Column(Text, nullable=True)  # JSON list; NULL means legacy/unknown

    account = relationship("Account", back_populates="stories")


class AudienceSnapshot(Base):
    """Demographics of the follower / engaged / reached audience.

    Instagram exposes these as lifetime metrics with a timeframe and top-45
    buckets. One row per (scope, timeframe, dimension, as_of): reports point at
    the nearest snapshot at or before the period end.
    """

    __tablename__ = "audience_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    scope = Column(String(16), nullable=False)  # follower | engaged | reached
    timeframe = Column(String(16), nullable=False)  # this_month | last_30_days | ...
    dimension = Column(String(16), nullable=False)  # age_gender | city | country
    as_of = Column(Date, nullable=False)
    payload = Column(Text, nullable=False)  # JSON [{"key": ..., "value": n}, ...]
    collected_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "account_id", "scope", "timeframe", "dimension", "as_of",
            name="uq_audience_snapshot",
        ),
    )


class NotificationDelivery(Base):
    """Idempotency record for scheduled Telegram notifications."""

    __tablename__ = "notification_deliveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_key = Column(String(255), unique=True, nullable=False)
    notification_type = Column(String(64), nullable=False)
    recipient_id = Column(String(64), nullable=False)
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    status = Column(String(16), default="pending", nullable=False)
    claimed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)


class PeriodSnapshot(Base):
    """Authoritative period totals from the API (metric_type=total_value).

    Unique metrics (reach, accounts_engaged) are only correct here: summing
    daily rows double-counts accounts active on several days.
    """

    __tablename__ = "period_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    reach = Column(Integer, nullable=True)
    accounts_engaged = Column(Integer, nullable=True)
    views = Column(Integer, nullable=True)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    saves = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    total_interactions = Column(Integer, nullable=True)
    profile_views = Column(Integer, nullable=True)
    # Extended metrics (probe 2026-10): period totals keep reports honest for
    # any window even when day-level collection has gaps.
    profile_links_taps = Column(Integer, nullable=True)
    reposts = Column(Integer, nullable=True)
    follows = Column(Integer, nullable=True)
    unfollows = Column(Integer, nullable=True)
    breakdowns = Column(Text, nullable=True)  # JSON breakdown sets for the window
    collected_at = Column(DateTime, nullable=True)
    metrics_present = Column(Text, nullable=True)  # JSON list of metric names

    account = relationship("Account")

    __table_args__ = (
        UniqueConstraint("account_id", "period_start", "period_end", name="uq_period_snapshot"),
    )


class SyncRun(Base):
    """Durable journal of account synchronization attempts."""

    __tablename__ = "sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    sync_type = Column(String(32), nullable=False, default="full")
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(32), nullable=False, default="running")
    error = Column(Text, nullable=True)
    since_date = Column(Date, nullable=True)
    until_date = Column(Date, nullable=True)
    api_delay_days = Column(Integer, default=0, nullable=False)
    is_partial = Column(Boolean, default=False, nullable=False)


class SyncLease(Base):
    """Short-lived cross-process lock for one account sync job."""

    __tablename__ = "sync_leases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    sync_type = Column(String(32), nullable=False, default="full")
    owner = Column(String(128), nullable=False)
    acquired_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "sync_type", name="uq_sync_lease_account_type"),
    )
