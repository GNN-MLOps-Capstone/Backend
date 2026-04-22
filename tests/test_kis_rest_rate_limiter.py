"""
4/20 - KIS REST 유량 제한 설정 및 공용 진입점 단위 테스트
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


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

from app.config import Settings, infer_kis_rest_max_requests_per_second
from app.kis.client import KISClient
from app.services.kis_service import KISService


def _build_kis_service(settings: Settings) -> KISService:
    service = KISService.__new__(KISService)
    service._settings = settings
    service.BASE_URL = settings.kis_base_url
    service.app_key = settings.kis_app_key
    service.app_secret = settings.kis_app_secret
    service.timeout = settings.kis_timeout
    service._access_token = None
    service._token_expires_at = None
    service._price_cache = {}
    return service


class _FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000",
                "prdy_vrss": "1000",
                "prdy_ctrt": "1.45",
                "acml_vol": "123456",
                "stck_hgpr": "71000",
                "stck_lwpr": "69000",
            },
        }


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.get = AsyncMock(return_value=response)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


# =============================================================================
# 공통 픽스처
# =============================================================================


@pytest.fixture
def kis_settings() -> Settings:
    return Settings(
        secret_key="test-secret-key",
        algorithm="HS256",
        google_client_id="test-google-client-id",
        kis_base_url="https://openapivts.koreainvestment.com:29443",
        kis_app_key="app-key",
        kis_app_secret="app-secret",
        kis_timeout=1.0,
    )


@pytest_asyncio.fixture
async def isolated_kis_components(kis_settings: Settings):
    client = KISClient(kis_settings)
    service = _build_kis_service(kis_settings)
    yield kis_settings, client, service
    await client.aclose()


# =============================================================================
# 설정 추론 테스트
# =============================================================================


class TestKISRateLimitConfig:
    def test_모의투자_url은_초당_2회로_추론한다(self):
        assert (
            infer_kis_rest_max_requests_per_second(
                "https://openapivts.koreainvestment.com:29443"
            )
            == 2
        )

    def test_실전_url은_초당_20회로_추론한다(self):
        assert (
            infer_kis_rest_max_requests_per_second(
                "https://openapi.koreainvestment.com:9443"
            )
            == 20
        )

    def test_명시적_rate_limit은_url_추론보다_우선한다(self):
        settings = Settings(
            secret_key="test-secret-key",
            algorithm="HS256",
            google_client_id="test-google-client-id",
            kis_base_url="https://openapivts.koreainvestment.com:29443",
            kis_max_requests_per_second=7,
        )

        assert settings.resolved_kis_rest_max_requests_per_second == 7

    def test_설정값이_없으면_url로_rate_limit을_추론한다(self):
        settings = Settings(
            secret_key="test-secret-key",
            algorithm="HS256",
            google_client_id="test-google-client-id",
            kis_base_url="https://openapivts.koreainvestment.com:29443",
        )

        assert settings.kis_max_requests_per_second is None
        assert settings.resolved_kis_rest_max_requests_per_second == 2


# =============================================================================
# 공용 유량 제한 진입점 테스트
# =============================================================================


@pytest.mark.asyncio
class TestSharedKISRateLimiterUsage:
    async def test_클라이언트와_서비스가_같은_유량제한_진입점을_공유한다(
        self,
        isolated_kis_components,
    ):
        settings, client, service = isolated_kis_components
        limiter_mock = AsyncMock()
        fake_response = _FakeResponse()
        fake_http_client = AsyncMock()
        fake_http_client.request = AsyncMock(return_value=fake_response)

        with (
            patch(
                "app.kis.rest_rate_limiter.acquire_kis_rest_rate_limit_slot",
                new=limiter_mock,
            ),
            patch(
                "app.kis.client.TokenManager.get_access_token",
                new=AsyncMock(return_value="test-token"),
            ),
            patch.object(
                client,
                "_get_http_client",
                new=AsyncMock(return_value=fake_http_client),
            ),
            patch.object(
                service,
                "_get_access_token",
                new=AsyncMock(return_value="test-token"),
            ),
            patch(
                "app.services.kis_service.httpx.AsyncClient",
                return_value=_FakeAsyncClient(fake_response),
            ),
        ):
            client_result = await client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": "005930",
                },
            )
            service_result = await service.get_stock_price("005930", use_cache=False)

        assert client_result["rt_cd"] == "0"
        assert service_result["price"] == 70000
        assert limiter_mock.await_count == 2
        assert limiter_mock.await_args_list[0].args[0] == settings
        assert limiter_mock.await_args_list[1].args[0] == settings
