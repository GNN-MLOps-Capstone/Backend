"""
4/14~15, 4/20 - KIS 실시간 시세 및 주식 서비스 인증/개요 조회 단위 테스트
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import respx
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
from app.kis.cache import TTLCache
from app.kis.client import KISClient
from app.kis.errors import KISError
from app.services.kis_service import KISService


async def _reset_stock_service_state() -> None:
    stock_service.cache._store.clear()
    for state in list(stock_service._overview_states_by_loop.values()):
        state.inflight.clear()
    stock_service._overview_states_by_loop.clear()


@pytest.fixture
def bearer_credentials() -> HTTPAuthorizationCredentials:
    token = create_access_token({"sub": "google-user-123"})
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture
def fake_settings():
    return SimpleNamespace(
        kis_base_url="https://kis.example.test",
        kis_app_key="app-key",
        kis_app_secret="app-secret",
        kis_timeout=1.0,
        kis_max_requests_per_second=0,
        resolved_kis_rest_max_requests_per_second=0,
    )


@pytest_asyncio.fixture
async def isolated_stock_service_state():
    await _reset_stock_service_state()
    yield
    await _reset_stock_service_state()


@pytest_asyncio.fixture
async def isolated_stock_service(monkeypatch, fake_settings):
    test_client = KISClient(fake_settings)
    test_cache = TTLCache()
    monkeypatch.setattr(stock_service, "client", test_client)
    monkeypatch.setattr(stock_service, "cache", test_cache)
    await _reset_stock_service_state()
    yield fake_settings, test_client, test_cache
    await _reset_stock_service_state()
    await test_client.aclose()


def _build_kis_service(fake_settings) -> KISService:
    service = KISService.__new__(KISService)
    service._settings = fake_settings
    service.BASE_URL = fake_settings.kis_base_url
    service.app_key = fake_settings.kis_app_key
    service.app_secret = fake_settings.kis_app_secret
    service.timeout = fake_settings.kis_timeout
    service._access_token = None
    service._token_expires_at = None
    service._price_cache = {}
    return service


# =============================================================================
# 인증 의존성 테스트
# =============================================================================


@pytest.mark.asyncio
class TestAuthDependency:
    async def test_jwt_sub를_db조회없이_현재_주체로_반환한다(self, bearer_credentials):
        subject = await get_current_subject(bearer_credentials)

        assert subject == "google-user-123"


# =============================================================================
# 실시간 시세 조회 테스트
# =============================================================================


class TestKISRealtimeService:
    @respx.mock
    async def test_현재가_조회_성공시_파싱및_캐시(self, fake_settings):
        service = _build_kis_service(fake_settings)
        service._access_token = "cached-token"
        service._token_expires_at = datetime.now() + timedelta(hours=1)

        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        route = respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output": {
                        "stck_prpr": "75000",
                        "prdy_vrss": "1200",
                        "prdy_ctrt": "1.63",
                        "acml_vol": "12345",
                        "stck_hgpr": "75500",
                        "stck_lwpr": "74500",
                    },
                },
            )
        )

        first = await service.get_stock_price("005930")
        second = await service.get_stock_price("005930")

        assert first == {
            "price": 75000,
            "change": 1200,
            "change_rate": 1.63,
            "volume": 12345,
            "high": 75500,
            "low": 74500,
        }
        assert second == first
        assert route.call_count == 1

    @respx.mock
    async def test_API_실패시_만료된_캐시라도_fallback(self, fake_settings):
        service = _build_kis_service(fake_settings)
        service._access_token = "cached-token"
        service._token_expires_at = datetime.now() + timedelta(hours=1)
        service._price_cache["005930"] = {
            "data": {
                "price": 70000,
                "change": -500,
                "change_rate": -0.71,
                "volume": 999,
                "high": 71000,
                "low": 69500,
            },
            "expires_at": datetime.now() - timedelta(seconds=1),
        }

        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        respx.get(url).mock(
            return_value=httpx.Response(200, json={"rt_cd": "1", "msg1": "failure"})
        )

        result = await service.get_stock_price("005930")

        assert result["price"] == 70000
        assert result["change_rate"] == -0.71


# =============================================================================
# 주식 개요 조회 테스트
# =============================================================================


class TestStockOverviewService:
    def test_비정상_rt_cd는_KISError(self):
        with pytest.raises(KISError) as exc_info:
            stock_service.ensure_kis_ok(
                {"rt_cd": "1", "msg1": "bad request", "msg_cd": "ERR001"}
            )

        assert exc_info.value.status_code == 200
        assert exc_info.value.code == "ERR001"

    @respx.mock
    async def test_최근_유효_일봉_포인트_반환(self, isolated_stock_service):
        fake_settings, _, _ = isolated_stock_service
        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output2": [
                        {
                            "stck_bsop_date": "20260407",
                            "stck_oprc": "71000",
                            "stck_hgpr": "71500",
                            "stck_lwpr": "70000",
                            "stck_clpr": "0",
                            "acml_vol": "20000",
                        },
                        {
                            "stck_bsop_date": "20260406",
                            "stck_oprc": "70000",
                            "stck_hgpr": "71200",
                            "stck_lwpr": "69500",
                            "stck_clpr": "70500",
                            "acml_vol": "18000",
                        },
                    ],
                },
            )
        )

        with patch(
            "app.kis.token_manager.TokenManager.get_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = "access-token"
            point = await stock_service.fetch_latest_daily_point("005930")

        assert point == {
            "t": point["t"],
            "o": 70000,
            "h": 71200,
            "l": 69500,
            "c": 70500,
            "v": 18000,
        }

    @respx.mock
    async def test_현재가가_0이면_일봉으로_fallback(self, isolated_stock_service):
        fake_settings, _, _ = isolated_stock_service
        overview_url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        daily_url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

        respx.get(overview_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output": {
                        "hts_kor_isnm": "삼성전자",
                        "stck_prpr": "0",
                        "prdy_vrss": "500",
                        "prdy_vrss_sign": "2",
                        "prdy_ctrt": "0.71",
                        "stck_oprc": "0",
                        "stck_hgpr": "0",
                        "stck_lwpr": "0",
                        "acml_vol": "0",
                        "acml_tr_pbmn": "123456789",
                    },
                },
            )
        )
        respx.get(daily_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output2": [
                        {
                            "stck_bsop_date": "20260407",
                            "stck_oprc": "70000",
                            "stck_hgpr": "72000",
                            "stck_lwpr": "69800",
                            "stck_clpr": "71200",
                            "acml_vol": "25000",
                        }
                    ],
                },
            )
        )

        with patch(
            "app.kis.token_manager.TokenManager.get_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = "access-token"
            overview = await stock_service.fetch_stock_overview("005930")

        assert overview["code"] == "005930"
        assert overview["name"] == "삼성전자"
        assert overview["last_price"] == 71200
        assert overview["open"] == 70000
        assert overview["high"] == 72000
        assert overview["low"] == 69800
        assert overview["volume"] == 25000
        assert overview["change_rate"] == 0.71

    @respx.mock
    async def test_개요조회는_캐시를_재사용(self, isolated_stock_service):
        fake_settings, _, _ = isolated_stock_service
        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        route = respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output": {
                        "hts_kor_isnm": "삼성전자",
                        "stck_prpr": "75000",
                        "prdy_vrss": "500",
                        "prdy_vrss_sign": "2",
                        "prdy_ctrt": "0.67",
                        "stck_oprc": "74800",
                        "stck_hgpr": "75100",
                        "stck_lwpr": "74400",
                        "acml_vol": "54321",
                        "acml_tr_pbmn": "123456789",
                    },
                },
            )
        )

        with patch(
            "app.kis.token_manager.TokenManager.get_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = "access-token"
            first = await stock_service.fetch_stock_overview("005930")
            second = await stock_service.fetch_stock_overview("005930")

        assert first == second
        assert route.call_count == 1


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
        assert stock_service._get_overview_state().inflight == {}

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
