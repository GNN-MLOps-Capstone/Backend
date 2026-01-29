"""
KIS Open API를 사용하는 주식 라우터
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, WebSocket, WebSocketDisconnect, Query
import re
import asyncio
import contextlib

from app.config import get_settings
from app.kis.cache import TTLCache
from app.kis.client import KISClient
from app.kis.errors import KISError
from app.kis.transformers import transform_overview, transform_series_time, transform_series_daily, KST
from app.kis.ws_client import KISWSClient
from app.schemas import StockOverviewResponse, StockSeriesResponse, StockSeriesQuery


router = APIRouter(
    prefix="/api/stocks",
    tags=["stocks"],
)

settings = get_settings()
client = KISClient(settings)
ws_client = KISWSClient(settings)
cache = TTLCache()


def _raise_kis_http_error(exc: KISError) -> None:
    raise HTTPException(
        status_code=502,
        detail={
            "status_code": exc.status_code,
            "code": exc.code,
            "message": exc.message,
        },
    )


def _ensure_kis_ok(data: dict) -> None:
    rt_cd = data.get("rt_cd")
    if rt_cd is not None and str(rt_cd) != "0":
        raise KISError(
            data.get("msg1") or "KIS API error",
            status_code=200,
            code=data.get("msg_cd"),
        )


_ALNUM6_RE = re.compile(r"^[A-Za-z0-9]{6}$")


@router.websocket("/ws/current")
async def stream_current_price(
    websocket: WebSocket,
    code: str = Query(..., min_length=6, max_length=6, description="종목코드 (6자리)"),
):
    """
    국내주식 실시간 현재가(WebSocket).
    """
    if not _ALNUM6_RE.fullmatch(code):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    async def _send(payload: dict) -> None:
        await websocket.send_json(payload)

    async def _wait_disconnect() -> None:
        try:
            while True:
                await websocket.receive()
        except WebSocketDisconnect:
            return

    stream_task = asyncio.create_task(ws_client.stream_current_price(code, _send))
    disconnect_task = asyncio.create_task(_wait_disconnect())

    done, pending = await asyncio.wait(
        {stream_task, disconnect_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if disconnect_task in done:
        stream_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stream_task
        return

    # KIS 스트림이 먼저 종료된 경우
    disconnect_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await disconnect_task
    try:
        await stream_task
    except KISError as exc:
        await websocket.send_json(
            {
                "error": {
                    "status_code": exc.status_code,
                    "code": exc.code,
                    "message": exc.message,
                }
            }
        )
        await websocket.close()


@router.get("/{code}/overview", response_model=StockOverviewResponse)
async def get_stock_overview(
    code: str = Path(..., pattern=r"^[A-Za-z0-9]{6}$", description="종목코드 (6자리)"),
):
    """
    종목 상단 카드용 현재가 요약 정보.
    """
    cache_key = f"overview:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        data = await client.request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",  # KIS: 주식현재가 시세
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            },
        )
        _ensure_kis_ok(data)
        overview = transform_overview(data, code)
        cache.set(cache_key, overview, ttl_seconds=3)
        return overview
    except KISError as exc:
        _raise_kis_http_error(exc)


@router.get("/{code}/series", response_model=StockSeriesResponse)
async def get_stock_series(
    query: StockSeriesQuery = Depends(),
    code: str = Path(..., pattern=r"^[A-Za-z0-9]{6}$", description="종목코드 (6자리)"),
):
    """
    기간별 시세 (1d/1w/1m).
    """
    range_label = (query.range or "").lower()

    if range_label == "1d":
        cache_key = f"series:{code}:{range_label}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        now_kst = datetime.now(tz=KST)
        fid_input_hour_1 = now_kst.strftime("%H%M%S")

        try:
            data = await client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                tr_id="FHKST03010200",  # KIS: 주식당일분봉조회
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_HOUR_1": fid_input_hour_1,
                    "FID_PW_DATA_INCU_YN": "Y",
                    "FID_ETC_CLS_CODE": "",
                },
            )
            _ensure_kis_ok(data)
            series = transform_series_time(data, code, range_label, interval_minutes=5)
            cache.set(cache_key, series, ttl_seconds=15)
            return series
        except KISError as exc:
            _raise_kis_http_error(exc)

    if range_label in {"1w", "1m"}:
        now_kst = datetime.now(tz=KST).date()
        if query.to_date:
            to_date = query.to_date
        else:
            to_date = now_kst.strftime("%Y%m%d")

        if query.from_date:
            from_date = query.from_date
        else:
            days = 7 if range_label == "1w" else 30
            from_date = (now_kst - timedelta(days=days)).strftime("%Y%m%d")

        if from_date > to_date:
            raise HTTPException(status_code=400, detail="from_date must be <= to_date")

        cache_key = f"series:{code}:{range_label}:{from_date}:{to_date}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            data = await client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                tr_id="FHKST03010100",  # KIS: 국내주식기간별시세(일/주/월/년)
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": from_date,
                    "FID_INPUT_DATE_2": to_date,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            _ensure_kis_ok(data)
            series = transform_series_daily(data, code, range_label)
            cache.set(cache_key, series, ttl_seconds=120)
            return series
        except KISError as exc:
            _raise_kis_http_error(exc)

    raise HTTPException(status_code=400, detail="Unsupported range")
