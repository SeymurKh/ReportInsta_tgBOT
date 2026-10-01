import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from config import settings
from database.models import Base

# Create data directory for SQLite
if "sqlite" in settings.DATABASE_URL:
    os.makedirs("data", exist_ok=True)

engine: AsyncEngine = create_async_engine(settings.DATABASE_URL, echo=False)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

SQLITE_PRAGMAS = (
    "PRAGMA journal_mode=WAL",    # concurrent reads while background sync writes
    "PRAGMA busy_timeout=5000",   # wait instead of raising 'database is locked'
    "PRAGMA synchronous=NORMAL",  # safe with WAL
)


def configure_sqlite_engine(target_engine: AsyncEngine) -> None:
    """Apply production-hardening PRAGMAs to every new SQLite connection."""

    @event.listens_for(target_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        for pragma in SQLITE_PRAGMAS:
            cursor.execute(pragma)
        cursor.close()


if "sqlite" in settings.DATABASE_URL:
    configure_sqlite_engine(engine)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_lightweight_migrations)
        if conn.dialect.name == "sqlite":
            await conn.run_sync(_normalize_sqlite_datetime_strings)


def _normalize_sqlite_datetime_strings(sync_conn) -> None:
    """Strip '+00:00' offsets from datetime strings written by older versions.

    The project convention is naive UTC datetimes; aware values made columns
    mix both storage formats. SQLite keeps datetimes as text, so this one-time
    normalization at startup is safe and idempotent.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    for table in inspector.get_table_names():
        for column in inspector.get_columns(table):
            col_type = str(column.get("type", "")).upper()
            if "DATETIME" not in col_type and "TIMESTAMP" not in col_type:
                continue
            name = column["name"]
            sync_conn.execute(text(
                f"UPDATE {table} SET {name} = substr({name}, 1, length({name}) - 6) "
                f"WHERE {name} LIKE '%+00:00'"
            ))


def _apply_lightweight_migrations(sync_conn) -> None:
    """Add columns introduced after the initial schema (create_all does not
    alter existing tables). SQLite/Postgres-compatible via plain ALTER TABLE."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    existing_columns = {
        (table, col["name"])
        for table in existing_tables
        for col in inspector.get_columns(table)
    }
    timestamp_type = "TIMESTAMP" if sync_conn.dialect.name == "postgresql" else "DATETIME"
    migrations = {
        ("posts", "insights_updated_at"):
            f"ALTER TABLE posts ADD COLUMN insights_updated_at {timestamp_type}",
        ("posts", "insights_present"):
            "ALTER TABLE posts ADD COLUMN insights_present TEXT",
        ("stories", "metrics_present"):
            "ALTER TABLE stories ADD COLUMN metrics_present TEXT",
        ("accounts", "sync_status"):
            "ALTER TABLE accounts ADD COLUMN sync_status VARCHAR(32) DEFAULT 'never'",
        ("accounts", "last_sync_at"):
            f"ALTER TABLE accounts ADD COLUMN last_sync_at {timestamp_type}",
        ("accounts", "last_sync_error"):
            "ALTER TABLE accounts ADD COLUMN last_sync_error TEXT",
        ("accounts", "current_followers"):
            "ALTER TABLE accounts ADD COLUMN current_followers INTEGER",
        ("accounts", "current_followers_at"):
            f"ALTER TABLE accounts ADD COLUMN current_followers_at {timestamp_type}",
        ("daily_stats", "collected_at"):
            f"ALTER TABLE daily_stats ADD COLUMN collected_at {timestamp_type}",
        ("daily_stats", "is_partial"):
            "ALTER TABLE daily_stats ADD COLUMN is_partial BOOLEAN DEFAULT FALSE",
        ("daily_stats", "metrics_present"):
            "ALTER TABLE daily_stats ADD COLUMN metrics_present TEXT",
        ("notification_deliveries", "status"):
            "ALTER TABLE notification_deliveries ADD COLUMN status VARCHAR(16) DEFAULT 'sent'",
        ("notification_deliveries", "claimed_at"):
            f"ALTER TABLE notification_deliveries ADD COLUMN claimed_at {timestamp_type}",
        ("notification_deliveries", "expires_at"):
            f"ALTER TABLE notification_deliveries ADD COLUMN expires_at {timestamp_type}",
        ("daily_stats", "bucket_start"):
            f"ALTER TABLE daily_stats ADD COLUMN bucket_start {timestamp_type}",
        ("daily_stats", "bucket_end"):
            f"ALTER TABLE daily_stats ADD COLUMN bucket_end {timestamp_type}",
        ("daily_stats", "likes"):
            "ALTER TABLE daily_stats ADD COLUMN likes INTEGER DEFAULT 0",
        ("daily_stats", "comments"):
            "ALTER TABLE daily_stats ADD COLUMN comments INTEGER DEFAULT 0",
        ("daily_stats", "saves"):
            "ALTER TABLE daily_stats ADD COLUMN saves INTEGER DEFAULT 0",
        ("daily_stats", "shares"):
            "ALTER TABLE daily_stats ADD COLUMN shares INTEGER DEFAULT 0",
        ("daily_stats", "replies"):
            "ALTER TABLE daily_stats ADD COLUMN replies INTEGER DEFAULT 0",
        ("daily_stats", "total_interactions"):
            "ALTER TABLE daily_stats ADD COLUMN total_interactions INTEGER DEFAULT 0",
        ("daily_stats", "profile_views"):
            "ALTER TABLE daily_stats ADD COLUMN profile_views INTEGER DEFAULT 0",
        ("daily_stats", "website_clicks"):
            "ALTER TABLE daily_stats ADD COLUMN website_clicks INTEGER DEFAULT 0",
    }
    for (table, column), ddl in migrations.items():
        if table in existing_tables and (table, column) not in existing_columns:
            sync_conn.execute(text(ddl))
