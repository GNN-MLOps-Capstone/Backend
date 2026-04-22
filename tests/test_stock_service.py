"""
4/20 - 주식 서비스 인증 의존성 및 개요 조회 단위 테스트
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.security import HTTPAuthorizationCredentials


def _bootstrap_test_env() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ.setdefault("SECRET_KEY", "test-secret-key")
    os.environ.setdefault("ALGORITHM", "HS256")
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    os.environ["DEBUG"] = "true"


_bootstrap_test_env()

from app.routers.users import create_access_token, get_current_subject
from app.services import stock_service


async def _reset_stock_service_state() -> None:
    stock_service.cache._store.clear()
    async with stock_service._overview_inflight_lock:
        stock_service._overview_inflight.clear()


# =============================================================================
# 공통 픽스처
# =============================================================================


@pytest.fixture
def bearer_credentials() -> HTTPAuthorizationCredentials:
    token = create_access_token({"sub": "google-user-123"})
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest_asyncio.fixture
async def isolated_stock_service_state():
    await _reset_stock_service_state()
    yield
    await _reset_stock_service_state()


# =============================================================================
# 인증 의존성 테스트
# =============================================================================


@pytest.mark.asyncio
class TestAuthDependency:
    async def test_jwt_sub를_db조회없이_현재_주체로_반환한다(self, bearer_credentials):
        subject = await get_current_subject(bearer_credentials)

        assert subject == "google-user-123"


# =============================================================================
# 주식 개요 조회 테스트
# =============================================================================


@pytest.mark.asyncio
class TestFetchStockOverview:
    async def test_동일한_종목_요청은_inflight_작업을_공유한다(
        self,
        isolated_stock_service_state,
    ):
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

        with patch.object(
            stock_service,
            "_load_stock_overview_from_kis",
            side_effect=fake_request,
        ):
            first_task = asyncio.create_task(stock_service.fetch_stock_overview("005930"))
            await started.wait()

            second_task = asyncio.create_task(stock_service.fetch_stock_overview("005930"))
            await asyncio.sleep(0)

            assert call_count == 1

            release.set()
            first_result, second_result = await asyncio.gather(first_task, second_task)
            await asyncio.sleep(0)

        assert first_result == expected
        assert second_result == expected
        assert call_count == 1
        assert await stock_service.cache.get("overview:005930") == expected
        assert stock_service._overview_inflight == {}

    async def test_성공한_개요조회는_캐시를_재사용한다(
        self,
        isolated_stock_service_state,
    ):
        expected = {"code": "000660", "last_price": 120000}

        with patch.object(
            stock_service,
            "_load_stock_overview_from_kis",
            new=AsyncMock(return_value=expected),
        ) as request_mock:
            first_result = await stock_service.fetch_stock_overview("000660")
            second_result = await stock_service.fetch_stock_overview("000660")

        assert first_result == expected
        assert second_result == expected
        assert request_mock.await_count == 1
