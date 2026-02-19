"""
KIS Open API를 사용하는 주식 라우터
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, WebSocket, WebSocketDisconnect, Query, Request
import re
import asyncio
import contextlib
import logging

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
logger = logging.getLogger(__name__)


async def shutdown_stocks_resources() -> None:
    await client.aclose()


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


def _normalize_hhmmss(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 4:
        text = f"{text}00"
    if len(text) == 5:
        text = f"0{text}"
    if len(text) != 6 or not text.isdigit():
        return None
    return text


def _minute_cursor_for_now() -> str:
    """
    KIS 주식당일분봉조회 기준시간:
    - 장 종료 이후에는 15:30으로 고정해 미래시간 보정값(종가 반복) 유입 방지
    - 장 시작 전에는 09:00으로 고정
    """
    now_hhmmss = datetime.now(tz=KST).strftime("%H%M%S")
    if now_hhmmss > "153000":
        return "153000"
    if now_hhmmss < "090000":
        return "090000"
    return now_hhmmss


def _previous_minute_cursor(hhmmss: str) -> str:
    """
    HHMMSS 커서를 이전 1분으로 이동한다.
    장 시작(09:00) 이전으로는 내려가지 않도록 고정한다.
    """
    normalized = _normalize_hhmmss(hhmmss)
    if normalized is None or normalized <= "090000":
        return "090000"
    try:
        prev = datetime.strptime(normalized, "%H%M%S") - timedelta(minutes=1)
        prev_text = prev.strftime("%H%M%S")
    except ValueError:
        return "090000"
    if prev_text < "090000":
        return "090000"
    return prev_text


async def _fetch_intraday_page(code: str, cursor: str) -> dict:
    """
    단일 분봉 페이지를 조회한다.
    KIS 응답(rt_cd) 오류가 간헐적으로 발생하는 경우를 대비해 짧게 재시도한다.
    """
    max_attempts = 3
    last_exc: KISError | None = None
    for attempt in range(max_attempts):
        try:
            data = await client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                tr_id="FHKST03010200",  # KIS: 주식당일분봉조회
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_HOUR_1": cursor,
                    "FID_PW_DATA_INCU_YN": "Y",
                    "FID_ETC_CLS_CODE": "",
                },
                retries=1,
            )
            _ensure_kis_ok(data)
            return data
        except KISError as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.15 * (attempt + 1))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return {"output2": []}


async def _fetch_intraday_full_session(code: str) -> dict:
    """
    주식당일분봉조회는 1회 호출당 최대 30건이므로, 기준시간을 뒤로 이동시키며
    여러 번 조회해 당일 장 전체 구간(개장~현재/종가)을 수집한다.
    """
    cursor = _minute_cursor_for_now()
    today_kst = datetime.now(tz=KST).strftime("%Y%m%d")
    merged_rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    first_payload: dict | None = None
    # 09:00~15:30(약 390분) 기준 30건 페이지면 13회 내외
    max_calls = 20

    for _ in range(max_calls):
        try:
            data = await _fetch_intraday_page(code, cursor)
        except KISError as exc:
            # 첫 호출이 성공했으면, 이후 페이지 실패는 당일 수집 종료로 간주한다.
            if first_payload is not None:
                logger.info(
                    "intraday pagination stopped for %s at cursor=%s: %s",
                    code,
                    cursor,
                    exc.message,
                )
                break
            raise
        if first_payload is None:
            first_payload = data

        output2 = data.get("output2") or []
        if not isinstance(output2, list) or not output2:
            break

        oldest_time: str | None = None
        page_oldest_time: str | None = None
        crossed_prev_day = False
        new_count = 0
        for row in output2:
            date_text = str(row.get("stck_bsop_date") or "")
            time_text = _normalize_hhmmss(row.get("stck_cntg_hour"))
            if not date_text or not time_text:
                continue
            if page_oldest_time is None or time_text < page_oldest_time:
                page_oldest_time = time_text
            if date_text != today_kst:
                crossed_prev_day = True
                continue
            key = (date_text, time_text)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged_rows.append(row)
            new_count += 1
            if oldest_time is None or time_text < oldest_time:
                oldest_time = time_text

        if page_oldest_time is None:
            break
        if new_count == 0:
            next_cursor = _previous_minute_cursor(page_oldest_time)
            if next_cursor == cursor:
                break
            cursor = next_cursor
            await asyncio.sleep(0.05)
            continue
        if oldest_time is None:
            break
        if oldest_time <= "090000":
            break
        if crossed_prev_day:
            break

        next_cursor = _previous_minute_cursor(oldest_time)
        if next_cursor == cursor:
            break
        cursor = next_cursor
        await asyncio.sleep(0.05)

    if first_payload is None:
        return {"output2": []}

    merged = dict(first_payload)
    merged["output2"] = merged_rows
    return merged


async def _fetch_latest_daily_point(code: str, lookback_days: int = 20) -> dict | None:
    now_kst = datetime.now(tz=KST).date()
    from_date = (now_kst - timedelta(days=lookback_days)).strftime("%Y%m%d")
    to_date = now_kst.strftime("%Y%m%d")
    data = await client.request(
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
    )
    _ensure_kis_ok(data)
    daily = transform_series_daily(data, code, "1d-fallback")
    points = daily.get("points") or []
    if not isinstance(points, list) or not points:
        return None
    for p in reversed(points):
        c = int(p.get("c") or 0)
        if c > 0:
            return p
    return None


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
        except (WebSocketDisconnect, RuntimeError):
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
    cached = await cache.get(cache_key)
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
        if (overview.get("last_price") or 0) <= 0:
            # 일부 종목(우선주/비유동 종목)에서 현재가가 0으로 내려올 때 최근 유효 일봉으로 보정
            try:
                latest = await _fetch_latest_daily_point(code)
            except KISError:
                latest = None
            if latest is not None:
                overview["last_price"] = int(latest.get("c") or 0)
                overview["open"] = int(latest.get("o") or 0)
                overview["high"] = int(latest.get("h") or 0)
                overview["low"] = int(latest.get("l") or 0)
                overview["volume"] = int(latest.get("v") or 0)
        await cache.set(cache_key, overview, ttl_seconds=3)
        return overview
    except KISError as exc:
        _raise_kis_http_error(exc)


@router.get("/{code}/series", response_model=StockSeriesResponse)
async def get_stock_series(
    request: Request,
    query: StockSeriesQuery = Depends(),
    code: str = Path(..., pattern=r"^[A-Za-z0-9]{6}$", description="종목코드 (6자리)"),
):
    """
    기간별 시세 (1d/1w/1m).
    """
    range_label = (query.range or "").lower()
    bypass_cache = "_ts" in request.query_params

    if range_label == "1d":
        cache_key = f"series:{code}:{range_label}"
        if not bypass_cache:
            cached = await cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            data = await _fetch_intraday_full_session(code)
            series = transform_series_time(data, code, range_label, interval_minutes=5)
            points = series.get("points") or []
            has_valid_point = any(int(p.get("c") or 0) > 0 for p in points if isinstance(p, dict))
            if not has_valid_point:
                # 1d 분봉이 비거나 0으로만 구성된 경우 최근 유효 일봉 1개로 보정
                latest = await _fetch_latest_daily_point(code)
                if latest is not None:
                    date_dt = datetime.fromtimestamp(int(latest["t"]) / 1000, tz=KST)
                    anchor = date_dt.replace(hour=15, minute=30, second=0, microsecond=0)
                    anchor_ms = int(anchor.timestamp() * 1000)
                    c = int(latest.get("c") or 0)
                    series["points"] = [
                        {
                            "t": anchor_ms,
                            "o": int(latest.get("o") or c),
                            "h": int(latest.get("h") or c),
                            "l": int(latest.get("l") or c),
                            "c": c,
                            "v": int(latest.get("v") or 0),
                        }
                    ]
            if not bypass_cache:
                await cache.set(cache_key, series, ttl_seconds=15)
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
        if not bypass_cache:
            cached = await cache.get(cache_key)
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
            if not bypass_cache:
                await cache.set(cache_key, series, ttl_seconds=120)
            return series
        except KISError as exc:
            _raise_kis_http_error(exc)

    raise HTTPException(status_code=400, detail="Unsupported range")
