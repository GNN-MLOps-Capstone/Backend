# tests/test_kis.py
"""
4/5~6 — KIS 증권사 API 에러 처리 및 캐시 레이어 단위 테스트
"""

import asyncio
import pytest
import httpx
import respx
from unittest.mock import patch, AsyncMock

from app.kis.errors import KISError
from app.kis.cache import TTLCache
from app.kis.client import KISClient
from app.config import get_settings


# =============================================================================
# 공통 픽스처
# =============================================================================

@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def kis_client(settings):
    return KISClient(settings)


# =============================================================================
# KISError 테스트
# =============================================================================

class TestKISError:

    def test_기본_생성(self):
        err = KISError("API 실패")
        assert str(err) == "API 실패"
        assert err.status_code == 500
        assert err.code is None

    def test_전체_필드(self):
        err = KISError("Rate limit", status_code=429, code="EGW00201")
        assert err.status_code == 429
        assert err.code == "EGW00201"

    def test_Exception_상속(self):
        with pytest.raises(KISError):
            raise KISError("테스트 에러")


# =============================================================================
# TTLCache 테스트 (unittest.mock — 외부 의존 없음)
# =============================================================================

class TestTTLCache:

    async def test_set_후_get_성공(self):
        cache = TTLCache()
        await cache.set("key1", {"price": 75000}, ttl_seconds=10)
        result = await cache.get("key1")
        assert result == {"price": 75000}

    async def test_없는_키는_None(self):
        cache = TTLCache()
        result = await cache.get("nonexistent")
        assert result is None

    async def test_ttl_만료_후_None(self):
        cache = TTLCache()
        await cache.set("expire_key", "값", ttl_seconds=0.05)  # 50ms TTL
        await asyncio.sleep(0.1)  # 100ms 대기
        result = await cache.get("expire_key")
        assert result is None

    async def test_ttl_0이하_저장_안됨(self):
        cache = TTLCache()
        await cache.set("zero_ttl", "값", ttl_seconds=0)
        result = await cache.get("zero_ttl")
        assert result is None

    async def test_덮어쓰기(self):
        cache = TTLCache()
        await cache.set("key", "처음값", ttl_seconds=10)
        await cache.set("key", "덮어쓴값", ttl_seconds=10)
        result = await cache.get("key")
        assert result == "덮어쓴값"

    async def test_여러_키_독립적(self):
        cache = TTLCache()
        await cache.set("a", 1, ttl_seconds=10)
        await cache.set("b", 2, ttl_seconds=10)
        assert await cache.get("a") == 1
        assert await cache.get("b") == 2

    async def test_다양한_값_타입(self):
        cache = TTLCache()
        await cache.set("list", [1, 2, 3], ttl_seconds=10)
        await cache.set("dict", {"key": "val"}, ttl_seconds=10)
        await cache.set("int", 42, ttl_seconds=10)
        assert await cache.get("list") == [1, 2, 3]
        assert await cache.get("dict") == {"key": "val"}
        assert await cache.get("int") == 42


# =============================================================================
# KISClient._is_retriable_error 테스트 (순수 로직, 모킹 불필요)
# =============================================================================

class TestIsRetriableError:

    def test_timeout_재시도_가능(self):
        assert KISClient._is_retriable_error(httpx.TimeoutException("timeout"))

    def test_request_error_재시도_가능(self):
        assert KISClient._is_retriable_error(httpx.ConnectError("connect"))

    def test_429_재시도_가능(self):
        assert KISClient._is_retriable_error(KISError("rate limit", status_code=429))

    def test_500_재시도_가능(self):
        assert KISClient._is_retriable_error(KISError("server error", status_code=500))

    def test_503_재시도_가능(self):
        assert KISClient._is_retriable_error(KISError("unavailable", status_code=503))

    def test_EGW00201_재시도_가능(self):
        assert KISClient._is_retriable_error(KISError("token expired", code="EGW00201"))

    def test_400_재시도_불가(self):
        assert not KISClient._is_retriable_error(KISError("bad request", status_code=400))

    def test_401_재시도_불가(self):
        assert not KISClient._is_retriable_error(KISError("unauthorized", status_code=401))

    def test_일반_Exception_재시도_불가(self):
        assert not KISClient._is_retriable_error(ValueError("일반 에러"))


# =============================================================================
# KISClient.request 테스트 (respx로 HTTP 모킹)
# =============================================================================

class TestKISClientRequest:

    @respx.mock
    async def test_정상_응답(self, kis_client, settings):
        """200 응답 + rt_cd=0 → 정상 반환"""
        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        respx.get(url).mock(return_value=httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "정상처리", "output": {"stck_prpr": "75000"}}
        ))

        with patch("app.kis.token_manager.TokenManager.get_access_token", new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "fake-access-token"
            result = await kis_client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={"fid_input_iscd": "005930"},
            )

        assert result["rt_cd"] == "0"
        assert result["output"]["stck_prpr"] == "75000"

    @respx.mock
    async def test_HTTP_401_KISError_발생(self, kis_client, settings):
        """HTTP 401 → KISError(status_code=401)"""
        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        respx.get(url).mock(return_value=httpx.Response(
            401,
            json={"msg1": "Unauthorized", "msg_cd": "EGW00123"}
        ))

        with patch("app.kis.token_manager.TokenManager.get_access_token", new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "fake-token"
            with pytest.raises(KISError) as exc_info:
                await kis_client.request(
                    "GET",
                    "/uapi/domestic-stock/v1/quotations/inquire-price",
                    tr_id="FHKST01010100",
                )

        assert exc_info.value.status_code == 401

    @respx.mock
    async def test_HTTP_500_KISError_발생(self, kis_client, settings):
        """HTTP 500 → KISError(status_code=500)"""
        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        respx.get(url).mock(return_value=httpx.Response(500, json={"msg1": "Internal Error"}))

        with patch("app.kis.token_manager.TokenManager.get_access_token", new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "fake-token"
            with pytest.raises(KISError) as exc_info:
                await kis_client.request(
                    "GET",
                    "/uapi/domestic-stock/v1/quotations/inquire-price",
                    tr_id="FHKST01010100",
                )

        assert exc_info.value.status_code == 500

    @respx.mock
    async def test_rt_cd_비정상_KISError_발생(self, kis_client, settings):
        """HTTP 200이지만 rt_cd != 0 → KISError"""
        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        respx.get(url).mock(return_value=httpx.Response(
            200,
            json={"rt_cd": "1", "msg1": "종목코드 오류", "msg_cd": "KIOK0209"}
        ))

        with patch("app.kis.token_manager.TokenManager.get_access_token", new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "fake-token"
            with pytest.raises(KISError) as exc_info:
                await kis_client.request(
                    "GET",
                    "/uapi/domestic-stock/v1/quotations/inquire-price",
                    tr_id="FHKST01010100",
                )

        assert "종목코드 오류" in str(exc_info.value)

    @respx.mock
    async def test_JSON_파싱_실패_KISError(self, kis_client, settings):
        """응답이 JSON이 아닌 경우 → KISError(status_code=502)"""
        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        respx.get(url).mock(return_value=httpx.Response(200, content=b"not json"))

        with patch("app.kis.token_manager.TokenManager.get_access_token", new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "fake-token"
            with pytest.raises(KISError) as exc_info:
                await kis_client.request(
                    "GET",
                    "/uapi/domestic-stock/v1/quotations/inquire-price",
                    tr_id="FHKST01010100",
                )

        assert exc_info.value.status_code == 502

    async def test_base_url_미설정_KISError(self):
        """kis_base_url이 비어있으면 요청 전에 KISError"""
        from unittest.mock import MagicMock
        fake_settings = MagicMock()
        fake_settings.kis_base_url = ""
        fake_settings.kis_app_key = "k"
        fake_settings.kis_app_secret = "s"
        fake_settings.kis_timeout = 1.0
        fake_settings.kis_max_requests_per_second = 0
        client = KISClient(fake_settings)
        with pytest.raises(KISError) as exc_info:
            await client.request("GET", "/some/path", tr_id="XXXX")

        assert exc_info.value.status_code == 500

    async def test_timeout_KISError_발생(self):
        """타임아웃 시 KISError(status_code=502)"""
        from unittest.mock import MagicMock
        
        # kis_base_url이 설정된 가짜 settings 직접 생성
        fake_settings = MagicMock()
        fake_settings.kis_base_url = "https://openapi.koreainvestment.com:9443"
        fake_settings.kis_app_key = "fake-key"
        fake_settings.kis_app_secret = "fake-secret"
        fake_settings.kis_timeout = 10.0
        fake_settings.kis_max_requests_per_second = 0  # rate limit 비활성화

        client = KISClient(fake_settings)

        with patch("app.kis.token_manager.TokenManager.get_access_token", new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "fake-token"
            with patch.object(client, "_get_http_client", new_callable=AsyncMock) as mock_http:
                mock_client = AsyncMock()
                mock_client.request.side_effect = httpx.TimeoutException("timeout")
                mock_http.return_value = mock_client

                with pytest.raises(KISError) as exc_info:
                    await client.request(
                        "GET", "/some/path", tr_id="XXXX", retries=0
                    )

        assert exc_info.value.status_code == 502