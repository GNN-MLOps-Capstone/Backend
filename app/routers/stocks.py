"""
KIS Open API를 사용하는 주식 라우터
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, WebSocket, WebSocketDisconnect, Query, Request
import re
import asyncio
import contextlib
import logging
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.kis.cache import TTLCache
from app.kis.client import KISClient
from app.kis.errors import KISError
from app.kis.transformers import transform_overview, transform_series_time, transform_series_daily, KST
from app.kis.ws_client import KISWSClient
from app.schemas import StockOverviewResponse, StockSeriesResponse, StockSeriesQuery, AITrendResponse
from app.models import Stock, FilteredNews, NewsStockMapping
from app.database import get_db


router = APIRouter(
    prefix="/api/stocks",
    tags=["stocks"],
)

settings = get_settings()
client = KISClient(settings)
ws_client = KISWSClient(settings)
cache = TTLCache()
logger = logging.getLogger(__name__)
_INTRADAY_PAGE_INTERVAL_SECONDS = float(settings.kis_intraday_page_interval_seconds)
_INTRADAY_RATE_LIMIT_RETRY_COUNT = int(settings.kis_intraday_rate_limit_retry_count)
_INTRADAY_RATE_LIMIT_BACKOFF_SECONDS = float(settings.kis_intraday_rate_limit_backoff_seconds)


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


def _is_kis_transient_error(exc: KISError) -> bool:
    status_code = int(exc.status_code or 0)
    return status_code in (408, 429) or status_code >= 500


def _is_kis_rate_limit_error(exc: KISError) -> bool:
    return str(exc.code or "").upper() == "EGW00201"


async def _sleep_intraday_page_interval() -> None:
    if _INTRADAY_PAGE_INTERVAL_SECONDS > 0:
        await asyncio.sleep(_INTRADAY_PAGE_INTERVAL_SECONDS)


_ALNUM6_RE = re.compile(r"^[A-Za-z0-9]{6}$")


def _normalize_hhmmss(value: str | None) -> str | None:
    """4-digit HHMM은 HHMM00으로 보정하고, 5-digit은 모호해 거부하며 HHMMSS만 허용한다."""
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 4:
        text = f"{text}00"
    if len(text) == 5:
        return None
    if len(text) != 6 or not text.isdigit():
        return None
    hour = int(text[:2])
    minute = int(text[2:4])
    second = int(text[4:6])
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return text


def _clamp_intraday_cursor(value: str | None) -> str | None:
    """분봉 조회 기준 시각을 정규장 범위(09:00~15:30) 안으로 제한한다."""
    normalized = _normalize_hhmmss(value)
    if normalized is None:
        return None
    if normalized < "090000":
        return "090000"
    if normalized > "153000":
        return "153000"
    return normalized


def _normalize_yyyymmdd(value: object | None) -> str | None:
    if value is None:
        return None
    text = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if len(text) != 8:
        return None
    return text


def _coerce_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _series_row_key(row: dict) -> tuple[str, str] | None:
    date_text = _normalize_yyyymmdd(row.get("stck_bsop_date")) or _normalize_yyyymmdd(row.get("bsop_date"))
    time_text = _normalize_hhmmss(row.get("stck_cntg_hour"))
    if date_text is None or time_text is None:
        return None
    return (date_text, time_text)


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


def _hhmmss_to_seconds(value: str | None) -> int | None:
    """HHMMSS 문자열을 자정 기준 초 단위로 변환한다."""
    normalized = _normalize_hhmmss(value)
    if normalized is None:
        return None
    hour = int(normalized[:2])
    minute = int(normalized[2:4])
    second = int(normalized[4:6])
    return (hour * 3600) + (minute * 60) + second


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


def _extract_intraday_session_context(data: dict) -> tuple[str | None, str | None]:
    """KIS 분봉 응답에서 현재 세션의 거래일과 최신 체결시각을 추출한다."""
    session_date: str | None = None
    session_time: str | None = None

    output1 = data.get("output1") or {}
    if isinstance(output1, dict):
        session_date = _normalize_yyyymmdd(output1.get("stck_bsop_date"))
        session_time = _clamp_intraday_cursor(output1.get("stck_cntg_hour"))

    output2 = data.get("output2") or []
    if isinstance(output2, list):
        latest_key: tuple[str, str] | None = None
        for row in output2:
            if not isinstance(row, dict):
                continue
            key = _series_row_key(row)
            if key is None:
                continue
            if latest_key is None or key > latest_key:
                latest_key = key
        if latest_key is not None:
            session_date = session_date or latest_key[0]
            session_time = session_time or _clamp_intraday_cursor(latest_key[1])

    return session_date, session_time


def _should_restart_intraday_from_session_time(requested_cursor: str, session_time: str | None) -> bool:
    """로컬 기준 시각이 크게 뒤처졌을 때 KIS 세션 시각으로 재시작할지 판단한다."""
    requested_seconds = _hhmmss_to_seconds(requested_cursor)
    session_seconds = _hhmmss_to_seconds(session_time)
    if requested_seconds is None or session_seconds is None:
        return False
    if session_seconds <= requested_seconds:
        return False
    # 로컬 PC 시계가 크게 뒤처진 경우에만 KIS 기준 체결시각으로 재시작한다.
    return (session_seconds - requested_seconds) >= 30 * 60


def _series_bypass_client_id(request: Request) -> str:
    user_id = (request.headers.get("x-user-id") or "").strip()
    if user_id:
        return f"user:{user_id[:64]}"
    forwarded_for = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded_for:
        return f"ip:{forwarded_for}"
    if request.client and request.client.host:
        return f"ip:{request.client.host}"
    return "ip:unknown"


async def _resolve_series_bypass_cache(
    request: Request,
    code: str,
    range_label: str,
    cache_key: str,
    bypass_requested: bool,
) -> bool:
    if not bypass_requested:
        return False

    cooldown_seconds = float(settings.series_cache_bypass_cooldown_seconds)
    if cooldown_seconds <= 0:
        return True

    client_id = _series_bypass_client_id(request)
    bypass_key = f"bypass:{client_id}:{code}:{range_label}"
    last_honored_at = await cache.get(bypass_key)
    if last_honored_at is not None:
        logger.info(
            "series bypass throttled; using cached response (client_id=%s code=%s range=%s cache_key=%s cooldown=%.1fs)",
            client_id,
            code,
            range_label,
            cache_key,
            cooldown_seconds,
        )
        return False

    return True


async def _record_series_bypass_cooldown(request: Request, code: str, range_label: str) -> None:
    cooldown_seconds = float(settings.series_cache_bypass_cooldown_seconds)
    if cooldown_seconds <= 0:
        return
    client_id = _series_bypass_client_id(request)
    bypass_key = f"bypass:{client_id}:{code}:{range_label}"
    honored_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    await cache.set(bypass_key, honored_at, ttl_seconds=cooldown_seconds)


async def _fetch_intraday_page(code: str, cursor: str) -> dict:
    """
    단일 분봉 페이지를 조회한다.
    KIS 응답(rt_cd) 오류가 간헐적으로 발생하는 경우를 대비해 짧게 재시도한다.
    """
    transient_retry_count = 0
    rate_limit_retry_count = 0
    max_transient_retries = 2

    while True:
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
                retries=0,
            )
            _ensure_kis_ok(data)
            return data
        except KISError as exc:
            if _is_kis_rate_limit_error(exc):
                if rate_limit_retry_count < _INTRADAY_RATE_LIMIT_RETRY_COUNT:
                    rate_limit_retry_count += 1
                    backoff_seconds = _INTRADAY_RATE_LIMIT_BACKOFF_SECONDS * rate_limit_retry_count
                    if backoff_seconds > 0:
                        await asyncio.sleep(backoff_seconds)
                    continue
                raise

            if _is_kis_transient_error(exc) and transient_retry_count < max_transient_retries:
                transient_retry_count += 1
                await asyncio.sleep(0.15 * transient_retry_count)
                continue
            raise


async def _fetch_intraday_full_session(code: str) -> dict:
    """
    주식당일분봉조회는 1회 호출당 최대 30건이므로, 기준시간을 뒤로 이동시키며
    여러 번 조회해 당일 장 전체 구간(개장~현재/종가)을 수집한다.
    """
    cursor = _minute_cursor_for_now()
    merged_rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    first_payload: dict | None = None
    session_date: str | None = None
    restarted_from_session_time = False
    # 09:00~15:30(약 390분) 기준 30건 페이지면 13회 내외
    max_calls = 20

    for _ in range(max_calls):
        try:
            data = await _fetch_intraday_page(code, cursor)
        except KISError as exc:
            # 첫 호출이 성공했으면, 이후 페이지 실패는 당일 수집 종료로 간주한다.
            if first_payload is not None:
                if _is_kis_transient_error(exc):
                    next_cursor = _previous_minute_cursor(cursor)
                    if next_cursor != cursor:
                        logger.warning(
                            (
                                "intraday page transient failure; skipping cursor "
                                "(code=%s cursor=%s next_cursor=%s status=%s kis_code=%s message=%s)"
                            ),
                            code,
                            cursor,
                            next_cursor,
                            exc.status_code,
                            exc.code,
                            exc.message,
                        )
                        cursor = next_cursor
                        await _sleep_intraday_page_interval()
                        continue
                logger.warning(
                    (
                        "intraday pagination stopped; returning partial data "
                        "(code=%s cursor=%s status=%s kis_code=%s reason=%s)"
                    ),
                    code,
                    cursor,
                    exc.status_code,
                    exc.code,
                    exc.message,
                )
                break
            raise
        if first_payload is None:
            response_session_date, response_session_time = _extract_intraday_session_context(data)
            if (
                not restarted_from_session_time
                and _should_restart_intraday_from_session_time(cursor, response_session_time)
                and response_session_time is not None
                and response_session_time != cursor
            ):
                logger.info(
                    (
                        "intraday pagination restarted from KIS session time "
                        "(code=%s initial_cursor=%s session_time=%s session_date=%s)"
                    ),
                    code,
                    cursor,
                    response_session_time,
                    response_session_date,
                )
                cursor = response_session_time
                restarted_from_session_time = True
                await _sleep_intraday_page_interval()
                continue

            first_payload = data
            session_date = response_session_date

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
            if session_date is None:
                session_date = date_text
            if page_oldest_time is None or time_text < page_oldest_time:
                page_oldest_time = time_text
            if session_date is not None and date_text != session_date:
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
            await _sleep_intraday_page_interval()
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
        await _sleep_intraday_page_interval()

    if first_payload is None:
        return {"output2": []}

    merged = dict(first_payload)
    merged["output2"] = merged_rows
    return merged


async def _fetch_time_overtime_conclusion(code: str) -> dict:
    data = await client.request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion",
        tr_id="FHPST02310000",
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_HOUR_CLS_CODE": "1",
        },
        retries=2,
    )
    _ensure_kis_ok(data)
    return data


async def _fetch_daily_overtime_price(code: str) -> dict:
    data = await client.request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice",
        tr_id="FHPST02320000",
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        },
        retries=2,
    )
    _ensure_kis_ok(data)
    return data


async def _fetch_overtime_price(code: str) -> dict:
    data = await client.request(
        "GET",
        "/uapi/domestic-stock/v1/quotations/inquire-overtime-price",
        tr_id="FHPST02300000",
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        },
        retries=2,
    )
    _ensure_kis_ok(data)
    return data


def _normalize_overtime_rows(code: str, rows: list[dict], fallback_date: str | None = None) -> list[dict]:
    """시간외 체결 응답을 시계열 병합용 공통 row 형식으로 정규화한다."""
    normalized_fallback_date = _normalize_yyyymmdd(fallback_date) or datetime.now(tz=KST).strftime("%Y%m%d")
    normalized_rows: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        time_text = _normalize_hhmmss(row.get("stck_cntg_hour"))
        if time_text is None:
            continue
        date_text = (
            _normalize_yyyymmdd(row.get("stck_bsop_date"))
            or _normalize_yyyymmdd(row.get("bsop_date"))
            or normalized_fallback_date
        )
        price = _coerce_int(row.get("stck_prpr"))
        if price is None:
            price = _coerce_int(row.get("ovtm_untp_prpr"))
        if price is None or price <= 0:
            continue
        volume = _coerce_int(row.get("cntg_vol"))
        if volume is None:
            volume = _coerce_int(row.get("ovtm_untp_vol"))
        normalized_rows.append(
            {
                "stck_bsop_date": date_text,
                "stck_cntg_hour": time_text,
                "stck_oprc": price,
                "stck_hgpr": price,
                "stck_lwpr": price,
                "stck_prpr": price,
                "cntg_vol": max(volume or 0, 0),
            }
        )

    logger.info(
        "normalized overtime rows for %s: input=%d normalized=%d",
        code,
        len(rows),
        len(normalized_rows),
    )
    return normalized_rows


def _build_daily_overtime_anchor_rows(code: str, daily_payload: dict) -> list[dict]:
    output2 = daily_payload.get("output2") or []
    if not isinstance(output2, list) or not output2:
        return []

    latest_row: dict | None = None
    latest_date: str | None = None
    for row in output2:
        if not isinstance(row, dict):
            continue
        date_text = _normalize_yyyymmdd(row.get("stck_bsop_date"))
        if date_text is None:
            continue
        if latest_date is None or date_text > latest_date:
            latest_date = date_text
            latest_row = row

    if latest_row is None or latest_date is None:
        return []

    price = _coerce_int(latest_row.get("ovtm_untp_prpr"))
    if price is None:
        price = _coerce_int(latest_row.get("stck_clpr"))
    if price is None or price <= 0:
        return []

    volume = _coerce_int(latest_row.get("ovtm_untp_vol")) or 0
    rows = [
        {
            "stck_bsop_date": latest_date,
            "stck_cntg_hour": "180000",
            "stck_oprc": price,
            "stck_hgpr": price,
            "stck_lwpr": price,
            "stck_prpr": price,
            "cntg_vol": max(volume, 0),
        }
    ]
    logger.info("daily overtime anchor rows for %s: rows=%d date=%s", code, len(rows), latest_date)
    return rows


def _build_overtime_price_anchor_row(
    code: str,
    price_payload: dict,
    fallback_date: str | None = None,
) -> dict | None:
    """시간외 현재가 응답에서 병합용 단일 앵커 row를 생성한다."""
    output = price_payload.get("output") or {}
    if not isinstance(output, dict):
        return None

    price = _coerce_int(output.get("ovtm_untp_prpr"))
    if price is None or price <= 0:
        return None

    volume = _coerce_int(output.get("ovtm_untp_vol")) or 0
    date_text = (
        _normalize_yyyymmdd(output.get("stck_bsop_date"))
        or _normalize_yyyymmdd(output.get("bsop_date"))
        or _normalize_yyyymmdd(fallback_date)
        or datetime.now(tz=KST).strftime("%Y%m%d")
    )
    row = {
        "stck_bsop_date": date_text,
        "stck_cntg_hour": "180000",
        "stck_oprc": price,
        "stck_hgpr": price,
        "stck_lwpr": price,
        "stck_prpr": price,
        "cntg_vol": max(volume, 0),
    }
    logger.info("overtime price anchor row for %s: date=%s", code, date_text)
    return row


def _build_overtime_fill_rows(
    code: str,
    *,
    regular_rows: list[dict],
    overtime_daily_rows: list[dict],
    overtime_price_row: dict | None,
) -> list[dict]:
    anchor_date: str | None = None
    anchor_price: int | None = None

    if overtime_price_row is not None:
        anchor_date = _normalize_yyyymmdd(overtime_price_row.get("stck_bsop_date"))
        anchor_price = _coerce_int(overtime_price_row.get("stck_prpr"))

    if (anchor_date is None or anchor_price is None or anchor_price <= 0) and overtime_daily_rows:
        daily_anchor = overtime_daily_rows[-1]
        anchor_date = _normalize_yyyymmdd(daily_anchor.get("stck_bsop_date"))
        anchor_price = _coerce_int(daily_anchor.get("stck_prpr"))

    if anchor_date is None or anchor_price is None or anchor_price <= 0:
        latest_regular_key: tuple[str, str] | None = None
        latest_regular_row: dict | None = None
        for row in regular_rows:
            if not isinstance(row, dict):
                continue
            key = _series_row_key(row)
            if key is None:
                continue
            if latest_regular_key is None or key > latest_regular_key:
                latest_regular_key = key
                latest_regular_row = row
        if latest_regular_key is not None and latest_regular_row is not None:
            anchor_date = latest_regular_key[0]
            anchor_price = _coerce_int(latest_regular_row.get("stck_prpr"))

    if anchor_date is None or anchor_price is None or anchor_price <= 0:
        return []

    try:
        start_dt = datetime.strptime(f"{anchor_date}153500", "%Y%m%d%H%M%S")
        end_dt = datetime.strptime(f"{anchor_date}180000", "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise KISError(
            f"invalid anchor_date for overtime fill rows: {anchor_date}",
            status_code=502,
        ) from exc

    fill_rows: list[dict] = []
    cursor_dt = start_dt
    while cursor_dt <= end_dt:
        fill_rows.append(
            {
                "stck_bsop_date": anchor_date,
                "stck_cntg_hour": cursor_dt.strftime("%H%M%S"),
                "stck_oprc": anchor_price,
                "stck_hgpr": anchor_price,
                "stck_lwpr": anchor_price,
                "stck_prpr": anchor_price,
                "cntg_vol": 0,
            }
        )
        cursor_dt += timedelta(minutes=5)

    logger.info(
        "built overtime fill rows for %s: date=%s price=%d count=%d",
        code,
        anchor_date,
        anchor_price,
        len(fill_rows),
    )
    return fill_rows


def _merge_series_rows(regular_rows: list[dict], overtime_rows: list[dict]) -> list[dict]:
    merged_by_key: dict[tuple[str, str], dict] = {}

    for row in regular_rows:
        if not isinstance(row, dict):
            continue
        key = _series_row_key(row)
        if key is None:
            continue
        merged = dict(row)
        merged["stck_bsop_date"] = key[0]
        merged["stck_cntg_hour"] = key[1]
        merged_by_key[key] = merged

    for row in overtime_rows:
        if not isinstance(row, dict):
            continue
        key = _series_row_key(row)
        if key is None or key in merged_by_key:
            continue
        merged = dict(row)
        merged["stck_bsop_date"] = key[0]
        merged["stck_cntg_hour"] = key[1]
        merged_by_key[key] = merged

    return [merged_by_key[key] for key in sorted(merged_by_key.keys())]


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
    # _ts는 문서 비노출(hidden) 캐시 바이패스 트리거이며, 실제 반영은 쿨다운으로 제한한다.
    bypass_requested = "_ts" in request.query_params

    if range_label == "1d":
        cache_key = f"series:{code}:{range_label}"
        bypass_cache = await _resolve_series_bypass_cache(
            request=request,
            code=code,
            range_label=range_label,
            cache_key=cache_key,
            bypass_requested=bypass_requested,
        )
        if not bypass_cache:
            cached = await cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            intraday_data = await _fetch_intraday_full_session(code)
            failed_sources: list[str] = []
            intraday_session_date, _ = _extract_intraday_session_context(intraday_data)

            overtime_time_data: dict = {"output2": []}
            overtime_daily_data: dict = {"output2": []}
            overtime_price_data: dict = {"output": {}}
            overtime_results = await asyncio.gather(
                _fetch_time_overtime_conclusion(code),
                _fetch_daily_overtime_price(code),
                _fetch_overtime_price(code),
                return_exceptions=True,
            )
            for source_name, result in zip(
                ("overtime_time", "overtime_daily", "overtime_price"),
                overtime_results,
                strict=True,
            ):
                if isinstance(result, KISError):
                    failed_sources.append(source_name)
                    exc = result
                    logger.warning(
                        (
                            "optional overtime source failed (source=%s code=%s "
                            "status=%s kis_code=%s message=%s)"
                        ),
                        source_name,
                        code,
                        exc.status_code,
                        exc.code,
                        exc.message,
                    )
                    continue
                if isinstance(result, Exception):
                    raise result
                if source_name == "overtime_time":
                    overtime_time_data = result
                elif source_name == "overtime_daily":
                    overtime_daily_data = result
                else:
                    overtime_price_data = result

            regular_rows = intraday_data.get("output2") or []
            if not isinstance(regular_rows, list):
                regular_rows = []
            overtime_time_rows_raw = overtime_time_data.get("output2") or []
            if not isinstance(overtime_time_rows_raw, list):
                overtime_time_rows_raw = []
            overtime_time_rows = _normalize_overtime_rows(
                code,
                overtime_time_rows_raw,
                fallback_date=intraday_session_date,
            )
            overtime_daily_rows = _build_daily_overtime_anchor_rows(code, overtime_daily_data)
            overtime_price_row = _build_overtime_price_anchor_row(
                code,
                overtime_price_data,
                fallback_date=intraday_session_date,
            )
            overtime_fill_rows: list[dict] = []
            if "overtime_time" in failed_sources:
                overtime_fill_rows = _build_overtime_fill_rows(
                    code,
                    regular_rows=regular_rows,
                    overtime_daily_rows=overtime_daily_rows,
                    overtime_price_row=overtime_price_row,
                )

            overtime_rows: list[dict] = []
            overtime_rows.extend(overtime_time_rows)
            overtime_rows.extend(overtime_fill_rows)
            overtime_rows.extend(overtime_daily_rows)
            if overtime_price_row is not None:
                overtime_rows.append(overtime_price_row)

            merged_rows = _merge_series_rows(regular_rows, overtime_rows)
            logger.info(
                (
                    "series rows merged for %s: regular_count=%d overtime_time_count=%d "
                    "overtime_fill_count=%d overtime_daily_count=%d overtime_price_count=%d merged_count=%d "
                    "degraded=%s failed_sources=%s"
                ),
                code,
                len(regular_rows),
                len(overtime_time_rows),
                len(overtime_fill_rows),
                len(overtime_daily_rows),
                1 if overtime_price_row is not None else 0,
                len(merged_rows),
                bool(failed_sources),
                failed_sources,
            )

            merged_data = dict(intraday_data)
            merged_data["output2"] = merged_rows
            series = transform_series_time(merged_data, code, range_label, interval_minutes=5)
            points = series.get("points") or []
            has_valid_point = any(int(p.get("c") or 0) > 0 for p in points if isinstance(p, dict))
            if not has_valid_point:
                # 1d 분봉이 비거나 0으로만 구성된 경우 최근 유효 일봉 1개로 보정
                latest = None
                try:
                    latest = await _fetch_latest_daily_point(code)
                except KISError as exc:
                    logger.warning("series daily fallback failed for %s: %s", code, exc.message)

                if latest is not None:
                    latest_t = latest.get("t")
                    if latest_t is None:
                        logger.warning("series daily fallback skipped for %s: missing timestamp", code)
                    else:
                        try:
                            date_dt = datetime.fromtimestamp(int(latest_t) / 1000, tz=KST)
                            anchor = date_dt.replace(hour=15, minute=30, second=0, microsecond=0)
                            anchor_ms = int(anchor.timestamp() * 1000)
                        except (TypeError, ValueError, OverflowError, OSError):
                            logger.warning(
                                "series daily fallback skipped for %s: invalid timestamp t=%r",
                                code,
                                latest_t,
                            )
                        else:
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
            if bypass_cache:
                await _record_series_bypass_cooldown(request, code, range_label)
            else:
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
        bypass_cache = await _resolve_series_bypass_cache(
            request=request,
            code=code,
            range_label=range_label,
            cache_key=cache_key,
            bypass_requested=bypass_requested,
        )
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
            if bypass_cache:
                await _record_series_bypass_cooldown(request, code, range_label)
            else:
                await cache.set(cache_key, series, ttl_seconds=120)
            return series
        except KISError as exc:
            _raise_kis_http_error(exc)

    raise HTTPException(status_code=400, detail="Unsupported range")

async def get_ai_trends(db: AsyncSession, top_n: int = 3) -> list[dict]:
    """오늘의 AI 트렌드: 이슈 지수 기반 Top N 종목 반환"""
    now = datetime.now(timezone.utc)
    recent_vol_stats = []
    search_days = 1  # 볼륨/감성 윈도우 공유

    # 1. 뉴스 데이터가 있는 기간 동적 탐색 (볼륨 + 감성 윈도우 동시 확장)
    for search_days in range(1, 8):
        past_days_vol = now - timedelta(days=search_days)
        query_vol = (
            select(
                func.coalesce(Stock.stock_name, Stock.stock_id).label("stock_name"),
                Stock.stock_id,
                func.count(FilteredNews.news_id).label('recent_news_count')
            )
            .select_from(Stock)
            .join(NewsStockMapping, Stock.stock_id == NewsStockMapping.stock_id)
            .join(FilteredNews, NewsStockMapping.news_id == FilteredNews.news_id)
            .where(
                FilteredNews.created_at >= past_days_vol,
                FilteredNews.created_at <= now
            )
            .group_by(Stock.stock_name, Stock.stock_id)
        )

        result_vol = await db.execute(query_vol)
        recent_vol_stats = result_vol.all()
        if len(recent_vol_stats) >= top_n:
            break

    if not recent_vol_stats:
        return []

    # 2. 감성 점수 계산 (볼륨과 동일한 search_days 윈도우 사용)
    issue_stock_ids = [stat.stock_id for stat in recent_vol_stats]
    past_days_sent = now - timedelta(days=search_days)  # 볼륨과 같은 윈도우
    sentiment_score_expr = case(
        (FilteredNews.sentiment == '긍정', 1.0),
        (FilteredNews.sentiment == '부정', -1.0),
        else_=0.0
    )

    query_sent = (
        select(
            Stock.stock_id,
            func.avg(sentiment_score_expr).label('avg_sentiment')
        )
        .select_from(Stock)
        .join(NewsStockMapping, Stock.stock_id == NewsStockMapping.stock_id)
        .join(FilteredNews, NewsStockMapping.news_id == FilteredNews.news_id)
        .where(
            Stock.stock_id.in_(issue_stock_ids),
            FilteredNews.created_at >= past_days_sent,
            FilteredNews.created_at <= now
        )
        .group_by(Stock.stock_id)
    )
    result_sent = await db.execute(query_sent)
    sentiment_dict = {stat.stock_id: stat.avg_sentiment for stat in result_sent.all()}

    # 3. 점수 계산 및 정규화
    counts = [stat.recent_news_count for stat in recent_vol_stats]
    max_count, min_count = max(counts), min(counts)

    temp_results = []
    for stat in recent_vol_stats:
        raw_sent = sentiment_dict.get(stat.stock_id, 0.0)
        norm_vol = (stat.recent_news_count - min_count) / (max_count - min_count) if max_count != min_count else 0.5

        # 이슈 지수: (|감성| * 0.7) + (뉴스량 * 0.3)
        score = (abs(raw_sent) * 0.7) + (norm_vol * 0.3)

        temp_results.append({
            "code": stat.stock_id,
            "name": stat.stock_name,
            "avg_sentiment": raw_sent,
            "news_count": stat.recent_news_count,
            "score": round(score * 100),
            "issue_index": score,
        })

    # 4. 내림차순 정렬 후 순위(rank) 부여
    top_issues = sorted(temp_results, key=lambda x: x['issue_index'], reverse=True)[:top_n]

    return [
        {
            "rank": i + 1,
            "code": item["code"],
            "name": item["name"],
            "avg_sentiment": item["avg_sentiment"],
            "news_count": item["news_count"],
            "score": item["score"],
        }
        for i, item in enumerate(top_issues)
    ]

@router.get("/trends", response_model=list[AITrendResponse])
async def read_ai_trends(
    db: AsyncSession = Depends(get_db),
    top_n: int = Query(3, ge=1, le=20)
):
    """
    오늘의 AI 트렌드 종목 조회
    - 이슈 지수 = (|감성| * 0.7) + (뉴스량 * 0.3)
    - 날씨 = 주가 등락률 점수 + 감성 점수
    - 내림차순 정렬 후 상위 N개 반환
    """
    try:
        trends = await get_ai_trends(db, top_n=top_n)
 
        if not trends:
            return []
 
        async def _fetch_overview_safe(code: str) -> dict | None:
            try:
                return await get_stock_overview(code)
            except Exception as e:
                logger.warning("overview fetch failed for %s: %s", code, e)
                return None
 
        semaphore = asyncio.Semaphore(5)

        async def _fetch_overview_bounded(code: str) -> dict | None:
            async with semaphore:
                return await _fetch_overview_safe(code)

        overviews = await asyncio.gather(
            *[_fetch_overview_bounded(t["code"]) for t in trends]
        )
 
        results = []
        for trend, overview in zip(trends, overviews):
            change_rate = overview.get("change_rate") if overview else None
            avg_sentiment = trend.get("avg_sentiment")
            results.append({
                "rank": trend["rank"],
                "code": trend["code"],
                "name": trend["name"],
                "score": trend["score"],
                "weather": get_weather(change_rate, avg_sentiment),
                "last_price": overview.get("last_price") if overview else None,
                "change_rate": change_rate,
                "news_count": trend["news_count"],
                "avg_sentiment": round(avg_sentiment, 4) if avg_sentiment is not None else None,
            })
 
        return results
 
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

async def get_stock_weather(
    db: AsyncSession,
    *,
    stock_id: str | None = None,
    stock_name: str | None = None,
) -> str:
    """
    종목코드(stock_id) 또는 종목명(stock_name)으로 날씨 코드 반환.
 
    - 감성 윈도우: 가장 최근 16:00 ~ 현재 (16시 기준 슬라이딩)
      ex) 오후 3시 → 전날 16:00 ~ 현재 / 오후 4시 1분 → 오늘 16:00 ~ 현재
    - 등락률: KIS 현재가 overview의 change_rate (전일 대비 실시간)
    """
    if stock_id is None and stock_name is None:
        raise ValueError("stock_id 또는 stock_name 중 하나는 필수입니다.")
 
    # 1. 종목 존재 여부 확인 / 종목명 해석
    if stock_id is not None:
        exists = await db.execute(
            select(Stock.stock_id).where(Stock.stock_id == stock_id)
        )
        if exists.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
    else:
        stock_ids = (
            await db.execute(
                select(Stock.stock_id).where(Stock.stock_name == stock_name).limit(2)
            )
        ).scalars().all()
        if not stock_ids:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
        if len(stock_ids) > 1:
            raise HTTPException(
                status_code=400,
                detail="동일한 종목명이 여러 개 존재합니다. stock_id를 사용해주세요.",
            )
        stock_id = stock_ids[0]
 
    # 2. 감성 집계 (가장 최근 16:00 ~ 현재)
    now_kst = datetime.now(tz=KST)
    today_cutoff = now_kst.replace(hour=16, minute=0, second=0, microsecond=0)
    window_start = today_cutoff if now_kst >= today_cutoff else today_cutoff - timedelta(days=1)
 
    sentiment_score_expr = case(
        (FilteredNews.sentiment == "긍정", 1.0),
        (FilteredNews.sentiment == "부정", -1.0),
        else_=0.0,
    )
    result = await db.execute(
        select(func.avg(sentiment_score_expr))
        .select_from(FilteredNews)
        .join(NewsStockMapping, FilteredNews.news_id == NewsStockMapping.news_id)
        .where(
            NewsStockMapping.stock_id == stock_id,
            FilteredNews.created_at >= window_start,
            FilteredNews.created_at <= now_kst,
        )
    )
    avg_sentiment: float | None = result.scalar_one_or_none()
 
    # 3. 등락률 조회 (캐시 우선)
    change_rate: float | None = None
    try:
        overview = await cache.get(f"overview:{stock_id}")
        if overview is None:
            data = await client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                tr_id="FHKST01010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_id,
                },
            )
            _ensure_kis_ok(data)
            overview = transform_overview(data, stock_id)
            await cache.set(f"overview:{stock_id}", overview, ttl_seconds=3)
        change_rate = overview.get("change_rate")
    except Exception as e:
        logger.warning("get_stock_weather: overview fetch failed for %s: %s", stock_id, e)
 
    return get_weather(change_rate, avg_sentiment)
 
    
def get_weather(change_rate: float | None, avg_sentiment: float | None) -> str:
    """
    주가 등락률 + 감성 평균으로 날씨 코드 반환.

    주가 점수: -5% 이하=-2, -4%~-1%=-1, -1%~+1%=0, +1%~+5%=+1, +5% 초과=+2
    감성 점수: 부정(avg<0)=-1, 중립(None or 0)=0, 긍정(avg>0)=+1
    합산:      <= -2=THUNDERSTORM, -1=RAINY, 0=CLOUDY, +1=PARTLY_CLOUDY, >= +2=SUNNY
    """
    # 주가 점수
    if change_rate is None:
        price_score = 0
    elif change_rate <= -5.0:
        price_score = -2
    elif change_rate <= -1.0:
        price_score = -1
    elif change_rate < 1.0:
        price_score = 0
    elif change_rate < 5.0:
        price_score = 1
    else:
        price_score = 2

    # 감성 점수 (뉴스 없으면 None → 0점 중립)
    if avg_sentiment is None or avg_sentiment == 0.0:
        sentiment_score = 0
    elif avg_sentiment > 0.0:
        sentiment_score = 1
    else:
        sentiment_score = -1

    total = price_score + sentiment_score

    if total <= -2:
        return "THUNDERSTORM"
    if total == -1:
        return "RAINY"
    if total == 0:
        return "CLOUDY"
    if total == 1:
        return "PARTLY_CLOUDY"
    return "SUNNY"

@router.get("/weather", response_model=dict)
async def get_stock_weather_endpoint(
    db: AsyncSession = Depends(get_db),
    stock_id: str | None = Query(None, description="종목코드 (6자리)"),
    stock_name: str | None = Query(None, description="종목명"),
):
    """
    종목코드 또는 종목명으로 날씨 코드 반환.
    - stock_id: 종목코드 (예: 005930)
    - stock_name: 종목명 (예: 삼성전자)
    """
    if stock_id is None and stock_name is None:
        raise HTTPException(status_code=400, detail="stock_id 또는 stock_name 중 하나는 필수입니다.")

    weather = await get_stock_weather(db, stock_id=stock_id, stock_name=stock_name)
    return {"weather": weather}