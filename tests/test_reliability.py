"""Reliability tests for the Instagram client, scheduler and DB writes."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine as create_sync_engine, inspect, select, text

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import crud
from database.engine import _apply_lightweight_migrations, _normalize_sqlite_datetime_strings
from database.models import Account, Base, DailyStats, SyncLease, SyncRun
from instagram.client import InstagramAPIError, InstagramClient, TokenExpiredError
from services.data_sync import _account_lock, make_insights_filter, sync_account_data
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
    async def test_startup_recovers_orphaned_sync_runs_but_keeps_live_leases(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime(2026, 9, 30, 12)

        async with factory() as session:
            orphan = Account(
                instagram_user_id="orphan", username="orphan", name="Orphan",
                access_token="token", sync_status="running",
            )
            active = Account(
                instagram_user_id="active", username="active", name="Active",
                access_token="token", sync_status="running",
            )
            expired = Account(
                instagram_user_id="expired", username="expired", name="Expired",
                access_token="token", sync_status="running",
            )
            session.add_all([orphan, active, expired])
            await session.flush()
            runs = [
                SyncRun(account_id=account.id, sync_type="full", started_at=now, status="running")
                for account in (orphan, active, expired)
            ]
            session.add_all(runs)
            session.add_all([
                SyncLease(
                    account_id=active.id, sync_type="full", owner="active-worker",
                    acquired_at=now, expires_at=now + timedelta(hours=1),
                ),
                SyncLease(
                    account_id=expired.id, sync_type="full", owner="dead-worker",
                    acquired_at=now - timedelta(hours=3), expires_at=now - timedelta(hours=1),
                ),
            ])
            await session.commit()
            run_ids = [run.id for run in runs]
            account_ids = [orphan.id, active.id, expired.id]

        with patch.object(crud, "async_session_factory", factory):
            recovered = await crud.recover_orphaned_sync_runs(now)
            async with factory() as session:
                persisted_runs = [await session.get(SyncRun, run_id) for run_id in run_ids]
                persisted_accounts = [await session.get(Account, account_id) for account_id in account_ids]

        await engine.dispose()
        self.assertEqual(recovered, 2)
        self.assertEqual([run.status for run in persisted_runs], ["interrupted", "running", "interrupted"])
        self.assertEqual([account.sync_status for account in persisted_accounts], ["failed", "running", "failed"])
        self.assertEqual(persisted_runs[0].finished_at, now)
        self.assertIn("interrupted", persisted_accounts[0].last_sync_error)

    async def test_skipped_sync_returns_full_result_shape(self):
        """Regression: skipped syncs (asyncio lock or DB lease held) must return
        the same keys as a completed sync — report generation reads
        api_delay_dates/partial unconditionally."""
        account = SimpleNamespace(id=1, username="demo")
        since = datetime(2026, 9, 1)
        until = datetime(2026, 9, 2)
        expected_keys = {"skipped", "reason", "api_delay_dates", "partial", "partial_reasons"}

        lock = _account_lock(account.id)
        await lock.acquire()
        try:
            overlapping = await sync_account_data(account, since, until)
        finally:
            lock.release()
        self.assertEqual(overlapping["reason"], "already_running")
        self.assertEqual(set(overlapping), expected_keys)

        with patch.object(crud, "acquire_sync_lease", new=AsyncMock(return_value=False)):
            lease_held = await sync_account_data(account, since, until)
        self.assertEqual(lease_held["reason"], "lease_held")
        self.assertEqual(set(lease_held), expected_keys)
        self.assertEqual(lease_held["api_delay_dates"], [])
        self.assertTrue(lease_held["partial"])

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

    async def test_daily_stats_provenance_is_cumulative_across_partial_refetch(self):
        """Regression: a partial re-fetch overwrote metrics_present with the
        reduced set, erasing provenance of metrics seen earlier (values were
        preserved, so reports claimed missing metrics that were actually there)."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        with patch.object(crud, "async_session_factory", factory):
            async with factory() as session:
                account = Account(
                    instagram_user_id="user", username="demo", name="Demo",
                    access_token="token",
                )
                session.add(account)
                await session.flush()
                account_id = account.id

            await crud.save_daily_stats(
                account_id=account_id, stats_date=date(2026, 9, 30),
                followers=100, following=20, media_count=4,
                reach=500, follower_count=7, views=900, accounts_engaged=40,
                collected_at=datetime(2026, 9, 30, 12, 0), is_partial=False,
                metrics_present=["follower_count", "reach", "views", "accounts_engaged"],
            )
            # Partial re-fetch: only old-format metrics came back.
            await crud.save_daily_stats(
                account_id=account_id, stats_date=date(2026, 9, 30),
                followers=None, following=None, media_count=None,
                reach=520, follower_count=7, views=None, accounts_engaged=None,
                collected_at=datetime(2026, 9, 30, 13, 0), is_partial=True,
                metrics_present=["follower_count", "reach"],
            )
            async with factory() as session:
                row = (await session.execute(select(DailyStats))).scalar_one()
                self.assertEqual(row.views, 900)
                self.assertEqual(row.accounts_engaged, 40)
                self.assertEqual(row.reach, 520)
                self.assertEqual(
                    set(json.loads(row.metrics_present)),
                    {"follower_count", "reach", "views", "accounts_engaged"},
                )

        await engine.dispose()

    async def test_datetime_columns_stay_naive_utc_and_migration_strips_offsets(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text(
                "INSERT INTO accounts "
                "(instagram_user_id, username, access_token, sync_status, created_at, updated_at) "
                "VALUES ('legacy', 'legacy', 'token', 'never', "
                "'2026-09-30 19:30:21.261000+00:00', '2026-09-30 19:30:21.261000+00:00')"
            ))
            await connection.run_sync(_normalize_sqlite_datetime_strings)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            legacy = (await session.execute(select(Account))).scalar_one()
            self.assertEqual(legacy.created_at, datetime(2026, 9, 30, 19, 30, 21, 261000))
            self.assertIsNone(legacy.created_at.tzinfo)
            account = Account(
                instagram_user_id="new", username="new", name="New", access_token="token",
            )
            session.add(account)
            await session.commit()
            self.assertIsNone(account.created_at.tzinfo)
            self.assertIsNone(account.updated_at.tzinfo)

        await engine.dispose()

    async def test_sqlite_engine_pragmas_enable_wal_and_busy_timeout(self):
        import os
        import tempfile

        from database.engine import configure_sqlite_engine

        with tempfile.TemporaryDirectory() as tmp:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{os.path.join(tmp, 'pragma.db')}"
            )
            configure_sqlite_engine(engine)
            async with engine.connect() as connection:
                journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
                busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one()
            await engine.dispose()

        self.assertEqual(journal_mode, "wal")
        self.assertEqual(busy_timeout, 5000)

    async def test_day_buckets_follow_instagram_boundaries(self):
        """Regression: Instagram's insight day D is the UTC calendar day; the
        API stamps its value with end_time = D 07:00 UTC. Every metric family
        must use these exact windows and share one day label."""
        from instagram.client import InstagramClient

        client = InstagramClient("user", "token")
        client.get_account_insights = AsyncMock(return_value={"data": [
            {"name": "reach", "values": [
                {"value": 601, "end_time": "2026-09-01T07:00:00+0000"},
                {"value": 390, "end_time": "2026-09-02T07:00:00+0000"},
            ]},
            {"name": "follower_count", "values": [
                {"value": 9, "end_time": "2026-09-01T07:00:00+0000"},
                {"value": 2, "end_time": "2026-09-02T07:00:00+0000"},
            ]},
        ]})
        buckets, series = await client.get_day_buckets(
            datetime(2026, 8, 31), datetime(2026, 9, 3)
        )
        self.assertEqual([b.day_key for b in buckets], ["2026-09-01", "2026-09-02"])
        self.assertEqual(buckets[0].start, datetime(2026, 9, 1, 0, 0))
        self.assertEqual(buckets[0].end, datetime(2026, 9, 2, 0, 0))
        self.assertEqual(series["reach"]["2026-09-01"], 601)
        self.assertEqual(series["reach"]["2026-09-02"], 390)
        self.assertEqual(series["follower_count"]["2026-09-02"], 2)

    async def test_save_daily_stats_persists_account_level_metrics(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        with patch.object(crud, "async_session_factory", factory):
            async with factory() as session:
                account = Account(
                    instagram_user_id="user", username="demo", name="Demo",
                    access_token="token",
                )
                session.add(account)
                await session.flush()
                account_id = account.id

            await crud.save_daily_stats(
                account_id=account_id, stats_date=date(2026, 9, 14),
                followers=None, following=None, media_count=None,
                reach=5936, follower_count=12,
                views=16438, accounts_engaged=307,
                collected_at=datetime(2026, 9, 15, 12, 0), is_partial=False,
                metrics_present=[
                    "reach", "follower_count", "views", "accounts_engaged",
                    "likes", "total_interactions",
                ],
                bucket_start=datetime(2026, 9, 14, 7, 0),
                bucket_end=datetime(2026, 9, 15, 7, 0),
                account_metrics={
                    "likes": 297, "comments": 21, "saves": 27, "shares": 195,
                    "total_interactions": 553, "profile_views": 300,
                },
            )
            async with factory() as session:
                row = (await session.execute(select(DailyStats))).scalar_one()
                self.assertEqual(row.likes, 297)
                self.assertEqual(row.shares, 195)
                self.assertEqual(row.total_interactions, 553)
                self.assertEqual(row.profile_views, 300)
                self.assertEqual(row.bucket_start, datetime(2026, 9, 14, 7, 0))
                self.assertEqual(row.bucket_end, datetime(2026, 9, 15, 7, 0))

        await engine.dispose()

    async def test_period_snapshot_round_trip(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        with patch.object(crud, "async_session_factory", factory):
            async with factory() as session:
                account = Account(
                    instagram_user_id="user", username="demo", name="Demo",
                    access_token="token",
                )
                session.add(account)
                await session.flush()
                account_id = account.id

            await crud.upsert_period_snapshot(
                account_id, date(2026, 9, 1), date(2026, 9, 29),
                {"reach": 16663, "accounts_engaged": 1053, "views": 160856},
                collected_at=datetime(2026, 9, 30, 12, 0),
            )
            await crud.upsert_period_snapshot(
                account_id, date(2026, 9, 1), date(2026, 9, 29),
                {"reach": 16700},
                collected_at=datetime(2026, 10, 1, 12, 0),
            )
            snapshot = await crud.get_period_snapshot(
                account_id, date(2026, 9, 1), date(2026, 9, 29)
            )
            self.assertEqual(snapshot.reach, 16700)
            self.assertEqual(snapshot.views, 160856)
            self.assertEqual(
                set(json.loads(snapshot.metrics_present)),
                {"reach", "accounts_engaged", "views"},
            )

        await engine.dispose()
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


    async def test_snapshot_day_windows_are_midnight_aligned(self):
        """Regression: off-midnight since/until must not shift new-format
        day metrics (views/accounts_engaged) into neighbouring days. IG
        returns the total for exactly the requested window."""
        client = InstagramClient("user", "token")
        requested: list[tuple[int, int]] = []

        async def fake_user_info():
            return {"username": "u"}

        async def fake_account_insights(since, until, metrics=None):
            return {"data": []}

        async def fake_new_metrics(day_start, day_end):
            requested.append((day_start, day_end))
            return {"data": []}

        with (
            patch.object(client, "get_user_info", fake_user_info),
            patch.object(client, "get_account_insights", fake_account_insights),
            patch.object(client, "get_account_insights_new_metrics_day", fake_new_metrics),
        ):
            # 18:27 UTC boundaries — the shape that caused the day shift
            since = int(datetime(2026, 9, 28, 18, 27).timestamp())
            until = int(datetime(2026, 9, 30, 18, 27).timestamp())
            snapshot = await client.collect_full_snapshot(since, until)

        self.assertTrue(requested)
        for day_start, day_end in requested:
            start = datetime.fromtimestamp(day_start, tz=timezone.utc)
            end = datetime.fromtimestamp(day_end, tz=timezone.utc)
            self.assertEqual((start.hour, start.minute, start.second), (0, 0, 0))
            self.assertEqual(end - start, timedelta(days=1))


    async def test_image_insights_include_shares(self):
        """Regression: MEDIA_METRICS_MAP['IMAGE'] omitted 'shares', so photo
        reposts were never fetched while total_interactions included them —
        reports showed «📤 0» for photos with 195 real shares."""
        client = InstagramClient("user", "token")
        captured = {}

        async def fake_request(url, params=None):
            captured["url"] = url
            captured["params"] = params
            return {"data": [
                {"name": "reach", "total_value": {"value": 100}},
                {"name": "shares", "total_value": {"value": 7}},
                {"name": "total_interactions", "total_value": {"value": 30}},
            ]}

        with patch.object(client, "_request", fake_request):
            result = await client.get_media_insights("media-1", "IMAGE")

        requested = captured["params"]["metric"].split(",")
        self.assertIn("shares", requested)
        self.assertEqual(result["shares"], 7)

    def test_expected_metrics_cover_image_shares(self):
        """If shares are requested for IMAGE, completeness tracking must expect
        them too — otherwise every photo is flagged as having missing metrics."""
        from analytics.calculations import calculate_content_summary

        post = SimpleNamespace(
            media_type="IMAGE", likes=10, comments=2, saved=3, shares=5,
            reach=100, total_interactions=20,
            insights_present='["reach", "saved", "shares", "total_interactions"]',
        )
        summary = calculate_content_summary([post])
        self.assertEqual(summary["partial_insights_posts"], 0)
        self.assertNotIn("shares", summary["missing_insight_metrics"])

