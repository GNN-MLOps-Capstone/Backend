"""
KIS Open API HTTP 클라이언트 래퍼
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.config import Settings
from app.kis.errors import KISError
from app.kis import rest_rate_limiter
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
                await rest_rate_limiter.acquire_kis_rest_rate_limit_slot(self._settings)
                resp = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                )
                if resp.status_code >= 400:
                    message = f"KIS request HTTP {resp.status_code}"
                    kis_code: str | None = None
                    try:
                        payload = resp.json()
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        msg = payload.get("msg1")
                        if msg:
                            message = f"{message}: {msg}"
                        raw_code = payload.get("msg_cd")
                        kis_code = str(raw_code) if raw_code else None
                    raise KISError(
                        message,
                        status_code=resp.status_code,
                        code=kis_code,
                    )
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise KISError("KIS response is not JSON", status_code=502) from exc
                rt_cd = data.get("rt_cd") if isinstance(data, dict) else None
                if rt_cd is not None and str(rt_cd) != "0":
                    raise KISError(
                        data.get("msg1") or "KIS API error",
                        status_code=200,
                        code=data.get("msg_cd"),
                    )
                return data
            except (httpx.TimeoutException, httpx.RequestError, KISError) as exc:
                if attempt < retries and self._is_retriable_error(exc):
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                if isinstance(exc, KISError):
                    raise
                raise KISError(f"KIS request failed: {exc}", status_code=502) from exc
        raise KISError("KIS request failed after retries", status_code=502)

    @staticmethod
    def _is_retriable_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
            return True
        if isinstance(exc, KISError):
            status_code = int(exc.status_code or 0)
            if status_code in (408, 429) or status_code >= 500:
                return True
            code = str(exc.code or "").upper()
            if code == "EGW00201":
                return True
        return False
