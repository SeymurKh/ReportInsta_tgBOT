"""Reliability tests for the Instagram client, scheduler and DB writes."""

import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine as create_sync_engine, inspect, select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import crud
from database.engine import _apply_lightweight_migrations
from database.models import Account, Base, DailyStats
from instagram.client import InstagramAPIError, InstagramClient, TokenExpiredError
from services.data_sync import make_insights_filter
from services.scheduler import _sync_with_backoff


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, url, params=None):
        return next(self.responses)


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_post_insights_refresh_weekly_instead_of_freezing(self):
        now = datetime(2026, 9, 30, 12)
        post = SimpleNamespace(
            instagram_media_id="old-media",
            insights_present='["reach", "total_interactions"]',
            insights_updated_at=datetime(2026, 9, 20, 12),
        )
        should_refresh = make_insights_filter([post], now)
        self.assertTrue(should_refresh("old-media", datetime(2026, 9, 1)))

        post.insights_updated_at = datetime(2026, 9, 25, 12)
        should_refresh = make_insights_filter([post], now)
        self.assertFalse(should_refresh("old-media", datetime(2026, 9, 1)))

    async def test_account_migration_adds_snapshot_columns_without_losing_rows(self):
        engine = create_sync_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE accounts (id INTEGER PRIMARY KEY, instagram_user_id TEXT, "
                "username TEXT, name TEXT, access_token TEXT, is_active BOOLEAN, "
                "created_at DATETIME, updated_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO accounts (id, instagram_user_id, username, name, access_token) "
                "VALUES (1, 'u1', 'demo', 'Demo', 'token')"
            )
            _apply_lightweight_migrations(connection)
            _apply_lightweight_migrations(connection)
            columns = {column["name"] for column in inspect(connection).get_columns("accounts")}
            row = connection.exec_driver_sql(
                "SELECT username, current_followers, current_followers_at "
                "FROM accounts WHERE id = 1"
            ).one()
        engine.dispose()
        self.assertIn("current_followers", columns)
        self.assertIn("current_followers_at", columns)
        self.assertEqual(row, ("demo", None, None))

    async def test_current_day_follower_change_ignores_previous_day_value(self):
        client = InstagramClient("user", "token")
        payload = {"data": [{"name": "follower_count", "values": [
            {"end_time": "2026-09-29T23:59:59+0000", "value": 14},
            {"end_time": "invalid-time", "value": 99},
            {"end_time": "2026-09-30T11:59:59+0000", "value": 3},
        ]}]}
        with patch.object(client, "get_account_insights", new=AsyncMock(return_value=payload)):
            result = await client.get_current_day_follower_change(
                datetime(2026, 9, 30, 12, 0)
            )
        self.assertEqual(result, 3)

    async def test_client_retries_rate_limit_then_succeeds(self):
        client = InstagramClient("user", "token")
        session = FakeSession([
            FakeResponse(429, {"error": {"code": 4, "message": "rate limit"}}),
            FakeResponse(200, {"data": [{"ok": True}]}),
        ])

        with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
            with patch("instagram.client.asyncio.sleep", new=AsyncMock()):
                result = await client._request("https://example.test")

        self.assertEqual(result, {"data": [{"ok": True}]})

    async def test_client_does_not_retry_expired_token(self):
        client = InstagramClient("user", "token")
        session = FakeSession([
            FakeResponse(400, {"error": {"code": 190, "message": "expired"}}),
        ])

        with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
            with self.assertRaises(TokenExpiredError):
                await client._request("https://example.test")

    async def test_client_raises_after_transient_failures(self):
        client = InstagramClient("user", "token")
        session = FakeSession([
            FakeResponse(503, {"error": {"code": 0, "message": "temporary"}}),
            FakeResponse(503, {"error": {"code": 0, "message": "temporary"}}),
            FakeResponse(503, {"error": {"code": 0, "message": "temporary"}}),
        ])

        with patch.object(client, "_get_session", new=AsyncMock(return_value=session)):
            with patch("instagram.client.asyncio.sleep", new=AsyncMock()):
                with self.assertRaises(InstagramAPIError):
                    await client._request("https://example.test")

    async def test_scheduler_retries_full_sync_with_backoff(self):
        account = SimpleNamespace(username="demo")
        sync = AsyncMock(side_effect=[RuntimeError("temporary"), {"partial": False}])

        with patch("services.scheduler.sync_account_data", new=sync):
            with patch("services.scheduler.asyncio.sleep", new=AsyncMock()) as sleep:
                await _sync_with_backoff(account, None, None)

        self.assertEqual(sync.await_count, 2)
        sleep.assert_awaited_once_with(10)

    async def test_scheduler_does_not_retry_known_partial_data(self):
        account = SimpleNamespace(username="demo")
        sync = AsyncMock(return_value={"partial": True, "partial_reasons": ["metric gap"]})
        with patch("services.scheduler.sync_account_data", new=sync):
            with patch("services.scheduler.asyncio.sleep", new=AsyncMock()) as sleep:
                await _sync_with_backoff(account, None, None)
        sync.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_snapshot_write_preserves_existing_metrics_and_saves_current_total(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        collected_at = datetime(2026, 9, 30, 12, 0)

        with patch.object(crud, "async_session_factory", factory):
            async with factory() as session:
                account = Account(
                    instagram_user_id="user", username="demo", name="Demo",
                    access_token="token", current_followers=100,
                )
                session.add(account)
                await session.flush()
                account_id = account.id
                session.add(DailyStats(
                    account_id=account_id, date=date(2026, 9, 30), followers=100,
                    following=20, media_count=4, reach=500, follower_count=7,
                    views=900, accounts_engaged=40, collected_at=collected_at,
                    is_partial=False,
                    metrics_present=json.dumps(["follower_count", "reach", "views", "accounts_engaged"]),
                ))
                await session.commit()

            await crud.update_current_followers(account_id, 128, collected_at)
            await crud.save_follower_snapshot(
                account_id, date(2026, 9, 30), 128, None, collected_at,
                following=22, media_count=5,
            )
            async with factory() as session:
                account = await session.get(Account, account_id)
                row = (await session.execute(select(DailyStats))).scalar_one()
                self.assertEqual(account.current_followers, 128)
                self.assertEqual(account.current_followers_at, collected_at)
                self.assertEqual(row.followers, 128)
                self.assertEqual(row.follower_count, 7)
                self.assertEqual(row.reach, 500)
                self.assertEqual(row.views, 900)
                self.assertFalse(row.is_partial)
                self.assertIn("follower_count", json.loads(row.metrics_present))

            await crud.update_current_followers(account_id, 129, collected_at)
            await crud.save_follower_snapshot(
                account_id, date(2026, 10, 1), 129, None, collected_at
            )
            async with factory() as session:
                rows = list((await session.execute(
                    select(DailyStats).order_by(DailyStats.date)
                )).scalars())
                self.assertEqual(rows[1].followers, 129)
                self.assertNotIn("follower_count", json.loads(rows[1].metrics_present))

        await engine.dispose()

    async def test_sync_lease_blocks_second_owner_and_releases(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        with patch.object(crud, "async_session_factory", factory):
            self.assertTrue(await crud.acquire_sync_lease(1, "owner-a"))
            self.assertFalse(await crud.acquire_sync_lease(1, "owner-b"))
            await crud.release_sync_lease(1, "owner-a")
            self.assertTrue(await crud.acquire_sync_lease(1, "owner-b"))
            await crud.release_sync_lease(1, "owner-b")

        await engine.dispose()

