from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import get_settings
from app.kis.cache import TTLCache
from app.kis.client import KISClient
from app.kis.errors import KISError
from app.kis.transformers import KST, transform_overview, transform_series_daily


settings = get_settings()
client = KISClient(settings)
cache = TTLCache()
logger = logging.getLogger(__name__)
_KIS_REQUEST_TIMEOUT = 8.0
_OVERVIEW_CACHE_TTL_SECONDS = 15.0
_OVERVIEW_MAX_CONCURRENCY = 5
_overview_semaphore = asyncio.Semaphore(_OVERVIEW_MAX_CONCURRENCY)
_overview_inflight: dict[str, asyncio.Task[dict]] = {}
_overview_inflight_lock = asyncio.Lock()


async def shutdown_stock_service_resources() -> None:
    await client.aclose()


def ensure_kis_ok(data: dict) -> None:
    rt_cd = data.get("rt_cd")
    if rt_cd is not None and str(rt_cd) != "0":
        raise KISError(
            data.get("msg1") or "KIS API error",
            status_code=200,
            code=data.get("msg_cd"),
        )


async def fetch_latest_daily_point(code: str, lookback_days: int = 20) -> dict | None:
    now_kst = datetime.now(tz=KST).date()
    from_date = (now_kst - timedelta(days=lookback_days)).strftime("%Y%m%d")
    to_date = now_kst.strftime("%Y%m%d")
    try:
        data = await asyncio.wait_for(
            client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                tr_id="FHKST03010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": from_date,
                    "FID_INPUT_DATE_2": to_date,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            ),
            timeout=_KIS_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise KISError(
            f"daily point request timed out after {_KIS_REQUEST_TIMEOUT}s",
            status_code=504,
        ) from exc
    ensure_kis_ok(data)
    daily = transform_series_daily(data, code, "1d-fallback")
    points = daily.get("points") or []
    if not isinstance(points, list) or not points:
        return None
    for point in reversed(points):
        close_price = int(point.get("c") or 0)
        if close_price > 0:
            return point
    return None


async def fetch_stock_overview(code: str) -> dict:
    cache_key = f"overview:{code}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    async with _overview_inflight_lock:
        task = _overview_inflight.get(code)
        if task is None:
            task = asyncio.create_task(_request_stock_overview_from_kis(code))
            task.add_done_callback(
                lambda done_task, stock_code=code: asyncio.create_task(
                    _clear_overview_inflight(stock_code, done_task)
                )
            )
            _overview_inflight[code] = task

    return await asyncio.shield(task)


async def _request_stock_overview_from_kis(code: str) -> dict:
    cache_key = f"overview:{code}"
    async with _overview_semaphore:
        overview = await _load_stock_overview_from_kis(code)
        await cache.set(cache_key, overview, ttl_seconds=_OVERVIEW_CACHE_TTL_SECONDS)
        return overview


async def _load_stock_overview_from_kis(code: str) -> dict:
    try:
        data = await asyncio.wait_for(
            client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                },
                retries=3,
            ),
            timeout=_KIS_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise KISError(
            f"overview request timed out after {_KIS_REQUEST_TIMEOUT}s",
            status_code=504,
        ) from exc

    ensure_kis_ok(data)
    overview = transform_overview(data, code)
    if (overview.get("last_price") or 0) <= 0:
        # 일부 종목(우선주/비유동 종목)에서 현재가가 0으로 내려오면 최근 유효 일봉으로 보정한다.
        try:
            latest = await fetch_latest_daily_point(code)
        except KISError:
            latest = None
        if latest is not None:
            overview["last_price"] = int(latest.get("c") or 0)
            overview["open"] = int(latest.get("o") or 0)
            overview["high"] = int(latest.get("h") or 0)
            overview["low"] = int(latest.get("l") or 0)
            overview["volume"] = int(latest.get("v") or 0)
    return overview


async def _clear_overview_inflight(code: str, task: asyncio.Task[dict]) -> None:
    async with _overview_inflight_lock:
        if _overview_inflight.get(code) is task:
            _overview_inflight.pop(code, None)
