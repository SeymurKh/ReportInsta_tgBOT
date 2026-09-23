"""Local maintenance tasks: SQLite backups and sync health checks."""

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import settings
from database import crud

logger = logging.getLogger(__name__)


async def collect_sync_health_issues(now: datetime | None = None) -> list[str]:
    """Return actionable freshness/sync problems for active accounts."""
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    stale_after = timedelta(hours=max(settings.DATA_SYNC_INTERVAL_HOURS * 2, 12))
    issues = []
    for account in await crud.get_all_accounts():
        status = getattr(account, "sync_status", "never") or "never"
        last_sync_at = getattr(account, "last_sync_at", None)
        if status == "failed":
            error = (getattr(account, "last_sync_error", None) or "неизвестная ошибка")[:180]
            issues.append(f"@{account.username}: ошибка синхронизации — {error}")
        elif not last_sync_at:
            issues.append(f"@{account.username}: синхронизация ещё не выполнялась")
        elif current - last_sync_at > stale_after:
            age_hours = round((current - last_sync_at).total_seconds() / 3600, 1)
            issues.append(f"@{account.username}: данные устарели ({age_hours} ч.)")
    return issues


def sqlite_path_from_url(database_url: str) -> Path | None:
    """Resolve a local SQLite URL; return None for PostgreSQL/unsupported URLs."""
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix):]
    if not raw_path:
        return None
    return Path(raw_path)


def create_sqlite_backup(
    source_path: str | Path,
    backup_dir: str | Path,
    retention_days: int = 14,
) -> Path:
    """Create a consistent SQLite backup and remove expired backup files."""
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"SQLite database not found: {source}")
    if retention_days <= 0:
        raise ValueError("retention_days must be greater than 0")

    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / f"instagram_bot_{stamp}.db"
    temp_destination = destination.with_suffix(".tmp")

    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(temp_destination)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    temp_destination.replace(destination)

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for candidate in destination_dir.glob("instagram_bot_*.db"):
        if candidate == destination:
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            candidate.unlink()
            logger.info("Removed expired DB backup: %s", candidate)
    logger.info("SQLite backup created: %s", destination)
    return destination


def create_configured_backup() -> Path | None:
    """Create a backup when the configured DB is local SQLite."""
    source = sqlite_path_from_url(settings.DATABASE_URL)
    if source is None:
        logger.info("Database backup skipped: configured database is not SQLite")
        return None
    return create_sqlite_backup(
        source,
        settings.BACKUP_DIR,
        settings.BACKUP_RETENTION_DAYS,
    )
