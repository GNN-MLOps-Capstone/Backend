"""
==============================================================================
한국투자증권 API 서비스 (kis_service.py)
==============================================================================

주식 현재가 조회를 위한 한국투자증권 Open API 연동

API 문서: https://apiportal.koreainvestment.com/

==============================================================================
"""

import os
import httpx
from datetime import datetime, timedelta
from typing import Optional
import asyncio


class KISService:
    """한국투자증권 API 서비스"""

    # 실전투자 도메인
    BASE_URL = "https://openapi.koreainvestment.com:9443"

    # 가격 캐시 유효 시간 (초)
    CACHE_TTL = 30

    def __init__(self):
        self.app_key = os.environ.get("KIS_APP_KEY", "")
        self.app_secret = os.environ.get("KIS_APP_SECRET", "")
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        # 가격 캐시: {종목코드: {"data": 가격정보, "expires_at": 만료시간}}
        self._price_cache: dict[str, dict] = {}

    async def _get_access_token(self) -> str:
        """액세스 토큰 발급 (캐싱)"""
        # 토큰이 유효하면 재사용
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at - timedelta(minutes=5):
                return self._access_token

        url = f"{self.BASE_URL}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            data = response.json()

            if "access_token" in data:
                self._access_token = data["access_token"]
                # 토큰 만료 시간 설정 (기본 24시간)
                expires_in = data.get("expires_in", 86400)
                self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                return self._access_token
            else:
                raise Exception(f"토큰 발급 실패: {data}")

    async def get_stock_price(self, stock_code: str, use_cache: bool = True) -> dict:
        """
        주식 현재가 조회 (캐싱 지원)

        Args:
            stock_code: 종목코드 (6자리, 예: "005930")
            use_cache: 캐시 사용 여부

        Returns:
            {
                "price": 현재가,
                "change": 전일대비,
                "change_rate": 등락률,
                "volume": 거래량,
            }
        """
        # 캐시 확인
        if use_cache and stock_code in self._price_cache:
            cached = self._price_cache[stock_code]
            if datetime.now() < cached["expires_at"]:
                return cached["data"]

        try:
            token = await self._get_access_token()

            url = f"{self.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "FHKST01010100",  # 주식현재가 시세
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",  # 주식
                "FID_INPUT_ISCD": stock_code,
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params)
                data = response.json()

                if data.get("rt_cd") == "0":  # 성공
                    output = data.get("output", {})
                    result = {
                        "price": int(output.get("stck_prpr", 0)),  # 현재가
                        "change": int(output.get("prdy_vrss", 0)),  # 전일대비
                        "change_rate": float(output.get("prdy_ctrt", 0)),  # 등락률
                        "volume": int(output.get("acml_vol", 0)),  # 누적거래량
                        "high": int(output.get("stck_hgpr", 0)),  # 최고가
                        "low": int(output.get("stck_lwpr", 0)),  # 최저가
                    }
                    # 캐시 저장
                    self._price_cache[stock_code] = {
                        "data": result,
                        "expires_at": datetime.now() + timedelta(seconds=self.CACHE_TTL),
                    }
                    return result
                else:
                    print(f"KIS API 오류: {data.get('msg1', 'Unknown error')}")
                    # 캐시에 있으면 만료되어도 반환 (fallback)
                    if stock_code in self._price_cache:
                        return self._price_cache[stock_code]["data"]
                    return {}

        except Exception as e:
            print(f"get_stock_price 오류: {e}")
            # 캐시에 있으면 만료되어도 반환 (fallback)
            if stock_code in self._price_cache:
                return self._price_cache[stock_code]["data"]
            return {}

    async def get_multiple_prices(self, stock_codes: list[str]) -> dict[str, dict]:
        """
        여러 종목 현재가 조회 (최대 20개 지원)

        Args:
            stock_codes: 종목코드 리스트

        Returns:
            {종목코드: 가격정보, ...}
        """
        results = {}
        codes_to_fetch = []

        # 캐시에서 먼저 확인
        for code in stock_codes:
            if code in self._price_cache:
                cached = self._price_cache[code]
                if datetime.now() < cached["expires_at"]:
                    results[code] = cached["data"]
                    continue
            codes_to_fetch.append(code)

        # 캐시에 없는 종목만 API 호출
        for code in codes_to_fetch:
            price = await self.get_stock_price(code, use_cache=False)
            results[code] = price
            # 각 요청 사이에 0.5초 대기 (초당 2건 이하로 안전하게)
            if code != codes_to_fetch[-1]:  # 마지막이 아니면
                await asyncio.sleep(0.5)

        return results


# 싱글톤 인스턴스
kis_service = KISService()
