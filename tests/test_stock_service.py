import os
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.security import HTTPAuthorizationCredentials

os.environ["DEBUG"] = "true"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

from app.routers.users import create_access_token, get_current_subject
from app.services import stock_service


class LightweightAuthDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_current_subject_returns_jwt_sub_without_db_lookup(self) -> None:
        token = create_access_token({"sub": "google-user-123"})
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        subject = await get_current_subject(credentials)

        self.assertEqual(subject, "google-user-123")


class FetchStockOverviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stock_service.cache._store.clear()
        async with stock_service._overview_inflight_lock:
            stock_service._overview_inflight.clear()

    async def asyncTearDown(self) -> None:
        stock_service.cache._store.clear()
        async with stock_service._overview_inflight_lock:
            stock_service._overview_inflight.clear()

    async def test_fetch_stock_overview_coalesces_duplicate_inflight_requests(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        call_count = 0
        expected = {"code": "005930", "last_price": 70000}

        async def fake_request(code: str) -> dict:
            nonlocal call_count
            call_count += 1
            started.set()
            await release.wait()
            return {**expected, "code": code}

        with patch.object(stock_service, "_load_stock_overview_from_kis", side_effect=fake_request):
            first_task = asyncio.create_task(stock_service.fetch_stock_overview("005930"))
            await started.wait()
            second_task = asyncio.create_task(stock_service.fetch_stock_overview("005930"))
            await asyncio.sleep(0)

            self.assertEqual(call_count, 1)

            release.set()
            first_result, second_result = await asyncio.gather(first_task, second_task)
            await asyncio.sleep(0)

        self.assertEqual(first_result, expected)
        self.assertEqual(second_result, expected)
        self.assertEqual(call_count, 1)
        self.assertEqual(await stock_service.cache.get("overview:005930"), expected)
        self.assertEqual(stock_service._overview_inflight, {})

    async def test_fetch_stock_overview_reuses_success_cache(self) -> None:
        expected = {"code": "000660", "last_price": 120000}

        with patch.object(
            stock_service,
            "_load_stock_overview_from_kis",
            new=AsyncMock(return_value=expected),
        ) as request_mock:
            first_result = await stock_service.fetch_stock_overview("000660")
            second_result = await stock_service.fetch_stock_overview("000660")

        self.assertEqual(first_result, expected)
        self.assertEqual(second_result, expected)
        self.assertEqual(request_mock.await_count, 1)
