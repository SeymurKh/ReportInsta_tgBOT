"""Tests for local maintenance helpers."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.maintenance import create_sqlite_backup, sqlite_path_from_url, collect_sync_health_issues


class MaintenanceTests(unittest.TestCase):
    def test_sqlite_url_resolution(self):
        self.assertEqual(
            sqlite_path_from_url("sqlite+aiosqlite:///data/instagram_bot.db"),
            Path("data/instagram_bot.db"),
        )
        self.assertIsNone(sqlite_path_from_url("postgresql+asyncpg://db"))

    def test_backup_is_readable_and_keeps_previous_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source)
            connection.execute("create table metrics (value integer)")
            connection.execute("insert into metrics values (42)")
            connection.commit()
            connection.close()

            first = create_sqlite_backup(source, backup_dir)
            second = create_sqlite_backup(source, backup_dir)

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertGreaterEqual(len(list(backup_dir.glob("*.db"))), 1)
            restored = sqlite3.connect(second)
            self.assertEqual(restored.execute("select value from metrics").fetchone()[0], 42)
            restored.close()

    def test_health_detects_failed_and_stale_accounts(self):
        now = datetime(2026, 9, 24, 12, 0)
        accounts = [
            SimpleNamespace(username="failed", sync_status="failed", last_sync_at=now, last_sync_error="429"),
            SimpleNamespace(username="stale", sync_status="success", last_sync_at=now - timedelta(hours=30), last_sync_error=None),
            SimpleNamespace(username="fresh", sync_status="success", last_sync_at=now - timedelta(hours=1), last_sync_error=None),
        ]

        async def run():
            with patch("services.maintenance.crud.get_all_accounts", new=AsyncMock(return_value=accounts)):
                return await collect_sync_health_issues(now)

        issues = __import__("asyncio").run(run())
        assert any("@failed" in issue and "429" in issue for issue in issues)
        assert any("@stale" in issue and "устарели" in issue for issue in issues)
        assert not any("@fresh" in issue for issue in issues)
