"""
4/14~15 — KIS 실시간 시세 조회 기능 단위 테스트
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import respx

import app.services.stock_service as stock_service
from app.kis.cache import TTLCache
from app.kis.client import KISClient
from app.kis.errors import KISError
from app.services.kis_service import KISService


@pytest.fixture
def fake_settings():
    return SimpleNamespace(
        kis_base_url="https://kis.example.test",
        kis_app_key="app-key",
        kis_app_secret="app-secret",
        kis_timeout=1.0,
        kis_max_requests_per_second=0,
    )


@pytest_asyncio.fixture
async def isolated_stock_service(monkeypatch, fake_settings):
    test_client = KISClient(fake_settings)
    test_cache = TTLCache()
    monkeypatch.setattr(stock_service, "client", test_client)
    monkeypatch.setattr(stock_service, "cache", test_cache)
    yield fake_settings, test_client, test_cache
    await test_client.aclose()


def _build_kis_service(fake_settings) -> KISService:
    service = KISService.__new__(KISService)
    service.BASE_URL = fake_settings.kis_base_url
    service.app_key = fake_settings.kis_app_key
    service.app_secret = fake_settings.kis_app_secret
    service.timeout = fake_settings.kis_timeout
    service._access_token = None
    service._token_expires_at = None
    service._price_cache = {}
    return service


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
            "data": {"price": 70000, "change": -500, "change_rate": -0.71, "volume": 999, "high": 71000, "low": 69500},
            "expires_at": datetime.now() - timedelta(seconds=1),
        }

        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        respx.get(url).mock(return_value=httpx.Response(200, json={"rt_cd": "1", "msg1": "failure"}))

        result = await service.get_stock_price("005930")

        assert result["price"] == 70000
        assert result["change_rate"] == -0.71


# =============================================================================
# 주식 개요 조회 테스트
# =============================================================================

class TestStockOverviewService:
    def test_비정상_rt_cd는_KISError(self):
        with pytest.raises(KISError) as exc_info:
            stock_service.ensure_kis_ok({"rt_cd": "1", "msg1": "bad request", "msg_cd": "ERR001"})

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
