import os
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from config import settings
from database.models import Base

# Create data directory for SQLite
if "sqlite" in settings.DATABASE_URL:
    os.makedirs("data", exist_ok=True)

engine: AsyncEngine = create_async_engine(settings.DATABASE_URL, echo=False)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_lightweight_migrations)


def _apply_lightweight_migrations(sync_conn) -> None:
    """Add columns introduced after the initial schema (create_all does not
    alter existing tables). SQLite/Postgres-compatible via plain ALTER TABLE."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    existing_columns = {
        (table, col["name"])
        for table in inspector.get_table_names()
        for col in inspector.get_columns(table)
    }
    timestamp_type = "TIMESTAMP" if sync_conn.dialect.name == "postgresql" else "DATETIME"
    migrations = {
        ("posts", "insights_updated_at"):
            f"ALTER TABLE posts ADD COLUMN insights_updated_at {timestamp_type}",
        ("accounts", "sync_status"):
            "ALTER TABLE accounts ADD COLUMN sync_status VARCHAR(32) DEFAULT 'never'",
        ("accounts", "last_sync_at"):
            f"ALTER TABLE accounts ADD COLUMN last_sync_at {timestamp_type}",
        ("accounts", "last_sync_error"):
            "ALTER TABLE accounts ADD COLUMN last_sync_error TEXT",
        ("daily_stats", "collected_at"):
            f"ALTER TABLE daily_stats ADD COLUMN collected_at {timestamp_type}",
        ("daily_stats", "is_partial"):
            "ALTER TABLE daily_stats ADD COLUMN is_partial BOOLEAN DEFAULT FALSE",
    }
    for (table, column), ddl in migrations.items():
        if (table, column) not in existing_columns:
            sync_conn.execute(text(ddl))
