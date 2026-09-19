from datetime import datetime, date
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
    created_at = Column(DateTime, default=lambda: datetime.utcnow())
    updated_at = Column(DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow())

    daily_stats = relationship("DailyStats", back_populates="account", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="account", cascade="all, delete-orphan")


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

    account = relationship("Account", back_populates="posts")