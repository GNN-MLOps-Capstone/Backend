"""
KIS WebSocket 실시간 현재가 스트리밍 클라이언트
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx
import websockets

from app.config import Settings
from app.kis.errors import KISError


class _ClientDisconnected(Exception):
    pass


_H0UNCNT0_COLUMNS = [
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
    "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU",
    "CTTR",
    "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN",
    "CNTG_CLS_CODE",
    "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR",
    "HGPR_HOUR",
    "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR",
    "LWPR_HOUR",
    "LWPR_VRSS_PRPR_SIGN",
    "LWPR_VRSS_PRPR",
    "BSOP_DATE",
    "NEW_MKOP_CLS_CODE",
    "TRHT_YN",
    "ASKP_RSQN1",
    "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
]


class KISWSClient:
    _approval_key: str | None = None
    _approval_expires_at: datetime | None = None
    _lock = asyncio.Lock()

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _get_approval_key(self) -> str:
        if self._approval_key and self._approval_expires_at:
            if datetime.now(timezone.utc) < (self._approval_expires_at - timedelta(seconds=60)):
                return self._approval_key

        async with self._lock:
            if self._approval_key and self._approval_expires_at:
                if datetime.now(timezone.utc) < (self._approval_expires_at - timedelta(seconds=60)):
                    return self._approval_key

            if not self._settings.kis_app_key or not self._settings.kis_app_secret:
                raise KISError("KIS app key/secret not configured", status_code=500)

            base_url = self._settings.kis_base_url.rstrip("/")
            url = f"{base_url}/oauth2/Approval"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self._settings.kis_app_key,
                "secretkey": self._settings.kis_app_secret,
            }

            try:
                async with httpx.AsyncClient(timeout=self._settings.kis_timeout) as client:
                    resp = await client.post(
                        url,
                        data=json.dumps(payload),
                        headers={"content-type": "application/json; charset=utf-8"},
                    )
            except httpx.RequestError as exc:
                raise KISError(f"KIS WS approval request failed: {exc}", status_code=502) from exc

            if resp.status_code >= 400:
                raise KISError(
                    f"KIS WS approval HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )

            try:
                data = resp.json()
            except ValueError as exc:
                raise KISError("KIS WS approval response is not JSON", status_code=502) from exc

            approval_key = data.get("approval_key")
            if not approval_key:
                raise KISError("KIS WS approval missing approval_key", status_code=502)

            self._approval_key = str(approval_key)
            self._approval_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            print("KIS WS approval key issued")
            return self._approval_key

    async def stream_current_price(
        self,
        code: str,
        on_message: Callable[[dict[str, Any]], Any],
    ) -> None:
        ws_url = self._settings.kis_ws_base_url.rstrip("/") + self._settings.kis_ws_path
        retry_count = 0

        while True:
            try:
                approval_key = await self._get_approval_key()
                print(f"KIS WS connecting: {ws_url}")
                subscribe_msg = {
                    "header": {
                        "content-type": "utf-8",
                        "approval_key": approval_key,
                        "tr_type": "1",
                        "custtype": "P",
                    },
                    "body": {
                        "input": {
                            "tr_id": "H0UNCNT0",
                            "tr_key": code,
                        }
                    },
                }

                async with websockets.connect(ws_url, ping_interval=None) as ws:
                    print("KIS WS connected")
                    await ws.send(json.dumps(subscribe_msg))
                    print(f"KIS WS subscribed: {code}")
                    retry_count = 0

                    async for raw in ws:
                        if raw.startswith(("0", "1")):
                            payload = _parse_data_row(raw, _H0UNCNT0_COLUMNS)
                            if payload is not None:
                                try:
                                    await on_message(payload)
                                except Exception as exc:
                                    print(f"Client send error: {exc}")
                                    raise _ClientDisconnected() from exc
                            continue

                        try:
                            system = json.loads(raw)
                        except ValueError:
                            continue

                        tr_id = system.get("header", {}).get("tr_id")
                        if tr_id == "PINGPONG":
                            await ws.pong(raw)
            except _ClientDisconnected:
                print("Client disconnected")
                return
            except Exception as exc:
                retry_count += 1
                print(f"KIS WS stream error: {exc}")
                # 네트워크 오류나 승인키 문제일 수 있어 재발급 유도
                self._approval_expires_at = None
                backoff = min(5 + retry_count, 20)
                print(f"KIS WS reconnect in {backoff}s (attempt {retry_count})")
                await asyncio.sleep(backoff)
                continue


def _parse_data_row(raw: str, columns: list[str]) -> Optional[dict[str, Any]]:
    parts = raw.split("|")
    if len(parts) < 4:
        return None
    data = parts[3]
    values = data.split("^")
    if len(values) < len(columns):
        return None
    row = dict(zip(columns, values))

    return {
        "code": row.get("MKSC_SHRN_ISCD"),
        "time": row.get("STCK_CNTG_HOUR"),
        "price": _to_int(row.get("STCK_PRPR")),
        "change": _to_signed(
            row.get("PRDY_VRSS"),
            row.get("PRDY_VRSS_SIGN"),
        ),
        "change_rate": _to_signed(
            row.get("PRDY_CTRT"),
            row.get("PRDY_VRSS_SIGN"),
        ),
        "open": _to_int(row.get("STCK_OPRC")),
        "high": _to_int(row.get("STCK_HGPR")),
        "low": _to_int(row.get("STCK_LWPR")),
        "volume": _to_int(row.get("ACML_VOL")),
        "trading_value": _to_int(row.get("ACML_TR_PBMN")),
    }


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_signed(value: Any, sign: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text == "":
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    sign_text = str(sign or "")
    if sign_text in {"4", "5"}:
        return -abs(num)
    return num
