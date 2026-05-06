"""
4/9~10 — KIS 증권사 API 통신 및 토큰 갱신 기능 단위 테스트
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import weakref

import httpx
import pytest
import pytest_asyncio
import respx

from app.kis.client import KISClient
from app.kis.errors import KISError
from app.kis.token_manager import TokenManager


@pytest.fixture(autouse=True)
def reset_token_manager_state():
    TokenManager._access_token = None
    TokenManager._expires_at = None
    TokenManager._locks_by_loop = weakref.WeakKeyDictionary()
    yield
    TokenManager._access_token = None
    TokenManager._expires_at = None
    TokenManager._locks_by_loop = weakref.WeakKeyDictionary()


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
async def kis_client(fake_settings):
    client = KISClient(fake_settings)
    yield client
    await client.aclose()


# =============================================================================
# TokenManager 토큰 관리 테스트
# =============================================================================

class TestTokenManager:
    async def test_유효한_캐시_토큰_재사용(self, fake_settings):
        TokenManager._access_token = "cached-token"
        TokenManager._expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        with patch.object(TokenManager, "_issue_token", new_callable=AsyncMock) as mock_issue:
            token = await TokenManager.get_access_token(fake_settings)

        assert token == "cached-token"
        mock_issue.assert_not_awaited()

    async def test_만료임박_토큰은_재발급(self, fake_settings):
        TokenManager._access_token = "old-token"
        TokenManager._expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)

        with patch.object(TokenManager, "_issue_token", new_callable=AsyncMock) as mock_issue:
            mock_issue.return_value = ("new-token", 3600)
            token = await TokenManager.get_access_token(fake_settings)

        assert token == "new-token"
        assert TokenManager._access_token == "new-token"
        assert TokenManager._expires_at is not None
        mock_issue.assert_awaited_once_with(fake_settings)

    async def test_동시요청에도_토큰은_한번만_발급(self, fake_settings):
        async def slow_issue_token(settings):
            await asyncio.sleep(0.01)
            return ("shared-token", 1800)

        with patch.object(TokenManager, "_issue_token", new=AsyncMock(side_effect=slow_issue_token)) as mock_issue:
            tokens = await asyncio.gather(
                *[TokenManager.get_access_token(fake_settings) for _ in range(5)]
            )

        assert tokens == ["shared-token"] * 5
        assert mock_issue.await_count == 1

    @respx.mock
    async def test_토큰발급_재시도_후_성공(self, fake_settings):
        url = f"{fake_settings.kis_base_url}/oauth2/tokenP"
        route = respx.post(url).mock(
            side_effect=[
                httpx.Response(503, json={"msg1": "temporary error"}),
                httpx.Response(
                    200,
                    json={"access_token": "issued-token", "expires_in": 7200},
                ),
            ]
        )

        with patch("app.kis.token_manager.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            token, expires_in = await TokenManager._issue_token(fake_settings)

        assert token == "issued-token"
        assert expires_in == 7200
        assert route.call_count == 2
        mock_sleep.assert_awaited_once()

    @respx.mock
    async def test_토큰응답_필수필드_누락시_에러(self, fake_settings):
        url = f"{fake_settings.kis_base_url}/oauth2/tokenP"
        respx.post(url).mock(return_value=httpx.Response(200, json={"expires_in": 3600}))

        with pytest.raises(KISError) as exc_info:
            await TokenManager._issue_token(fake_settings)

        assert exc_info.value.status_code == 502
        assert "missing fields" in str(exc_info.value)


# =============================================================================
# KISClient 통신 동작 테스트
# =============================================================================

class TestKISClientCommunication:
    @respx.mock
    async def test_재시도가능_에러후_정상응답(self, kis_client, fake_settings):
        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        route = respx.get(url).mock(
            side_effect=[
                httpx.Response(500, json={"msg1": "temporary failure"}),
                httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "75000"}}),
            ]
        )

        with patch(
            "app.kis.token_manager.TokenManager.get_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = "access-token"
            with patch("app.kis.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                data = await kis_client.request(
                    "GET",
                    "/uapi/domestic-stock/v1/quotations/inquire-price",
                    tr_id="FHKST01010100",
                    params={"FID_INPUT_ISCD": "005930"},
                    retries=1,
                )

        assert data["output"]["stck_prpr"] == "75000"
        assert route.call_count == 2
        mock_sleep.assert_awaited_once()

    @respx.mock
    async def test_요청헤더_구성_확인(self, kis_client, fake_settings):
        url = f"{fake_settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        route = respx.get(url).mock(return_value=httpx.Response(200, json={"rt_cd": "0"}))

        with patch(
            "app.kis.token_manager.TokenManager.get_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = "access-token"
            await kis_client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={"FID_INPUT_ISCD": "005930"},
            )

        request = route.calls[0].request
        assert request.headers["authorization"] == "Bearer access-token"
        assert request.headers["appkey"] == fake_settings.kis_app_key
        assert request.headers["appsecret"] == fake_settings.kis_app_secret
        assert request.headers["tr_id"] == "FHKST01010100"
        assert request.headers["custtype"] == "P"
