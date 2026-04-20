import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ["DEBUG"] = "true"

from app.config import Settings, infer_kis_rest_max_requests_per_second
from app.kis.client import KISClient
from app.services.kis_service import KISService


class KISRateLimitConfigTests(unittest.TestCase):
    def test_infers_mock_investment_rate_limit_from_vts_url(self) -> None:
        self.assertEqual(
            infer_kis_rest_max_requests_per_second(
                "https://openapivts.koreainvestment.com:29443"
            ),
            2,
        )

    def test_infers_live_rate_limit_from_production_url(self) -> None:
        self.assertEqual(
            infer_kis_rest_max_requests_per_second(
                "https://openapi.koreainvestment.com:9443"
            ),
            20,
        )

    def test_explicit_rate_limit_overrides_url_inference(self) -> None:
        settings = Settings(
            secret_key="test-secret-key",
            algorithm="HS256",
            google_client_id="test-google-client-id",
            kis_base_url="https://openapivts.koreainvestment.com:29443",
            kis_max_requests_per_second=7,
        )

        self.assertEqual(settings.resolved_kis_rest_max_requests_per_second, 7)

    def test_settings_defaults_to_mock_inference_when_rate_limit_is_omitted(self) -> None:
        settings = Settings(
            secret_key="test-secret-key",
            algorithm="HS256",
            google_client_id="test-google-client-id",
            kis_base_url="https://openapivts.koreainvestment.com:29443",
        )

        self.assertIsNone(settings.kis_max_requests_per_second)
        self.assertEqual(settings.resolved_kis_rest_max_requests_per_second, 2)


class SharedKISRateLimiterUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_and_service_share_same_rate_limiter_entrypoint(self) -> None:
        settings = Settings(
            secret_key="test-secret-key",
            algorithm="HS256",
            google_client_id="test-google-client-id",
            kis_base_url="https://openapivts.koreainvestment.com:29443",
            kis_app_key="app-key",
            kis_app_secret="app-secret",
            kis_timeout=1.0,
        )
        limiter_mock = AsyncMock()

        client = KISClient(settings)
        service = KISService()
        service._settings = settings
        service.BASE_URL = settings.kis_base_url
        service.app_key = settings.kis_app_key
        service.app_secret = settings.kis_app_secret
        service.timeout = settings.kis_timeout

        class FakeResponse:
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

        class FakeAsyncClient:
            def __init__(self, response: FakeResponse) -> None:
                self._response = response
                self.get = AsyncMock(return_value=response)

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        fake_response = FakeResponse()
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
            patch.object(client, "_get_http_client", new=AsyncMock(return_value=fake_http_client)),
            patch.object(service, "_get_access_token", new=AsyncMock(return_value="test-token")),
            patch(
                "app.services.kis_service.httpx.AsyncClient",
                return_value=FakeAsyncClient(fake_response),
            ),
        ):
            client_result = await client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"},
            )
            service_result = await service.get_stock_price("005930", use_cache=False)

        self.assertEqual(client_result["rt_cd"], "0")
        self.assertEqual(service_result["price"], 70000)
        self.assertEqual(limiter_mock.await_count, 2)
        self.assertEqual(limiter_mock.await_args_list[0].args[0], settings)
        self.assertEqual(limiter_mock.await_args_list[1].args[0], settings)
