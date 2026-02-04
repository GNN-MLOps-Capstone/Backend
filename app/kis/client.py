"""
KIS Open API HTTP 클라이언트 래퍼
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.config import Settings
from app.kis.errors import KISError
from app.kis.token_manager import TokenManager


class KISClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client

        async with self._client_lock:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=self._settings.kis_timeout)
            return self._http_client

    async def aclose(self) -> None:
        async with self._client_lock:
            if self._http_client is not None:
                await self._http_client.aclose()
                self._http_client = None

    async def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        retries: int = 1,
    ) -> dict[str, Any]:
        """
        공통 헤더와 간단한 재시도를 포함해 KIS API를 호출합니다.

        KIS 헤더:
        - authorization: Bearer <access_token>
        - appkey / appsecret: KIS 발급 키
        - tr_id: API 거래 ID
        - custtype: 기본 P(개인)
        """
        if not self._settings.kis_base_url:
            raise KISError("KIS base URL not configured", status_code=500)

        base_url = self._settings.kis_base_url.rstrip("/")
        url = f"{base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                token = await TokenManager.get_access_token(self._settings)
                headers = {
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": self._settings.kis_app_key,
                    "appsecret": self._settings.kis_app_secret,
                    "tr_id": tr_id,
                    "custtype": "P",
                }
                client = await self._get_http_client()
                resp = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                )
                if resp.status_code >= 400:
                    raise KISError(
                        f"KIS request HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )
                try:
                    return resp.json()
                except ValueError as exc:
                    raise KISError("KIS response is not JSON", status_code=502) from exc
            except (httpx.TimeoutException, httpx.RequestError, KISError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                if isinstance(exc, KISError):
                    raise exc
                raise KISError(f"KIS request failed: {exc}", status_code=502) from exc

        raise KISError(f"KIS request failed: {last_error}", status_code=502)
