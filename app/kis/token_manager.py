"""
KIS Open API 접근토큰 발급 및 갱신
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Tuple

import httpx

from app.config import Settings
from app.kis.errors import KISError


class TokenManager:
    # 캐시된 토큰 + 만료시각(UTC)
    _access_token: str | None = None
    _expires_at: datetime | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_access_token(cls, settings: Settings) -> str:
        """
        유효한 접근토큰을 반환합니다.

        갱신 로직:
        - 토큰이 없거나 60초 이내 만료면 재발급
        """
        if cls._access_token and cls._expires_at:
            if datetime.now(timezone.utc) < (cls._expires_at - timedelta(seconds=60)):
                return cls._access_token

        async with cls._lock:
            # 락 획득 후 재확인
            if cls._access_token and cls._expires_at:
                if datetime.now(timezone.utc) < (cls._expires_at - timedelta(seconds=60)):
                    return cls._access_token

            token, expires_in = await cls._issue_token(settings)
            cls._access_token = token
            cls._expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            return token

    @classmethod
    async def _issue_token(cls, settings: Settings) -> Tuple[str, int]:
        if not settings.kis_app_key or not settings.kis_app_secret:
            raise KISError("KIS app key/secret not configured", status_code=500)

        base_url = settings.kis_base_url.rstrip("/")
        url = f"{base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        }

        try:
            async with httpx.AsyncClient(timeout=settings.kis_timeout) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"content-type": "application/json; charset=utf-8"},
                )
        except httpx.RequestError as exc:
            raise KISError(f"KIS token request failed: {exc}", status_code=502) from exc

        if resp.status_code >= 400:
            raise KISError(
                f"KIS token request HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise KISError("KIS token response is not JSON", status_code=502) from exc

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not access_token or expires_in is None:
            raise KISError("KIS token response missing fields", status_code=502)

        return str(access_token), int(expires_in)
