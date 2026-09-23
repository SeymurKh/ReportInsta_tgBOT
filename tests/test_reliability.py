"""Reliability tests for the Instagram client and scheduler retry layer."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import crud
from database.models import Base
from instagram.client import InstagramAPIError, InstagramClient, TokenExpiredError
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

