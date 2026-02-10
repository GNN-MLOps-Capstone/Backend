"""
KIS API 응답을 프론트 표준 포맷으로 변환
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from zoneinfo import ZoneInfo
from datetime import timezone, timedelta


try:
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    # 시스템 tzdata가 없을 때의 폴백
    KST = timezone(timedelta(hours=9))

# 매핑 테이블로 필드 변경을 한 곳에서 관리
OVERVIEW_FIELD_MAP = {
    "last_price": "stck_prpr",
    "change": "prdy_vrss",
    "change_sign": "prdy_vrss_sign",
    "change_rate": "prdy_ctrt",
    "open": "stck_oprc",
    "high": "stck_hgpr",
    "low": "stck_lwpr",
    "volume": "acml_vol",
    "trading_value": "acml_tr_pbmn",
    "name": "hts_kor_isnm",
}

TIME_POINT_FIELD_MAP = {
    "date": "stck_bsop_date",
    "time": "stck_cntg_hour",
    "open": "stck_oprc",
    "high": "stck_hgpr",
    "low": "stck_lwpr",
    "close": "stck_prpr",
    "volume": "cntg_vol",
}

DAILY_POINT_FIELD_MAP = {
    "date": "stck_bsop_date",
    "open": "stck_oprc",
    "high": "stck_hgpr",
    "low": "stck_lwpr",
    "close": "stck_clpr",
    "volume": "acml_vol",
}

SIGN_POSITIVE = {"1", "2"}  # 상승/양수
SIGN_NEGATIVE = {"4", "5"}  # 하락/음수


def _parse_int(value: Any) -> Optional[int]:
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


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _apply_sign(value: Any, sign: Any) -> Optional[float]:
    """
    KIS 부호 코드를 필요한 값에 적용합니다.
    """
    num = _parse_float(value)
    if num is None:
        return None
    if num < 0:
        return num
    sign_text = str(sign) if sign is not None else ""
    if sign_text in SIGN_NEGATIVE:
        return -num
    return num


def _to_epoch_ms(date_str: str, time_str: Optional[str] = None) -> Optional[int]:
    if not date_str:
        return None
    time_text = (time_str or "000000").strip()
    if len(time_text) == 4:
        time_text = f"{time_text}00"
    if len(time_text) == 5:
        time_text = f"0{time_text}"
    if len(time_text) != 6:
        return None
    try:
        dt = datetime.strptime(f"{date_str}{time_text}", "%Y%m%d%H%M%S")
        dt = dt.replace(tzinfo=KST)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _resample_points(points: Iterable[dict[str, Any]], interval_minutes: int) -> list[dict[str, Any]]:
    interval_ms = interval_minutes * 60 * 1000
    sorted_points = sorted(points, key=lambda p: p["t"])
    buckets: list[dict[str, Any]] = []
    current_bucket = None
    for p in sorted_points:
        bucket_ts = p["t"] - (p["t"] % interval_ms)
        if current_bucket is None or current_bucket["t"] != bucket_ts:
            current_bucket = {
                "t": bucket_ts,
                "o": p["o"],
                "h": p["h"],
                "l": p["l"],
                "c": p["c"],
                "v": p["v"],
            }
            buckets.append(current_bucket)
            continue
        current_bucket["h"] = max(current_bucket["h"], p["h"])
        current_bucket["l"] = min(current_bucket["l"], p["l"])
        current_bucket["c"] = p["c"]
        current_bucket["v"] += p["v"]
    return buckets


def transform_overview(data: dict[str, Any], code: str) -> dict[str, Any]:
    output = data.get("output") or {}
    change_sign = output.get(OVERVIEW_FIELD_MAP["change_sign"])
    overview = {
        "code": code,
        "name": output.get(OVERVIEW_FIELD_MAP["name"]),
        "last_price": _parse_int(output.get(OVERVIEW_FIELD_MAP["last_price"])),
        "change": _apply_sign(output.get(OVERVIEW_FIELD_MAP["change"]), change_sign),
        "change_rate": _apply_sign(output.get(OVERVIEW_FIELD_MAP["change_rate"]), change_sign),
        "open": _parse_int(output.get(OVERVIEW_FIELD_MAP["open"])),
        "high": _parse_int(output.get(OVERVIEW_FIELD_MAP["high"])),
        "low": _parse_int(output.get(OVERVIEW_FIELD_MAP["low"])),
        "volume": _parse_int(output.get(OVERVIEW_FIELD_MAP["volume"])),
        "trading_value": _parse_int(output.get(OVERVIEW_FIELD_MAP["trading_value"])),
        "updated_at": datetime.now(tz=KST).isoformat(),
    }
    return overview


def transform_series_time(
    data: dict[str, Any],
    code: str,
    range_label: str,
    *,
    interval_minutes: int = 5,
) -> dict[str, Any]:
    output2 = data.get("output2") or []
    if not isinstance(output2, list):
        output2 = []

    raw_points: list[dict[str, Any]] = []
    for row in output2:
        date_str = row.get(TIME_POINT_FIELD_MAP["date"])
        time_str = row.get(TIME_POINT_FIELD_MAP["time"])
        t = _to_epoch_ms(date_str, time_str)
        o = _parse_int(row.get(TIME_POINT_FIELD_MAP["open"]))
        h = _parse_int(row.get(TIME_POINT_FIELD_MAP["high"]))
        l = _parse_int(row.get(TIME_POINT_FIELD_MAP["low"]))
        c = _parse_int(row.get(TIME_POINT_FIELD_MAP["close"]))
        v = _parse_int(row.get(TIME_POINT_FIELD_MAP["volume"])) or 0
        if t is None or o is None or h is None or l is None or c is None:
            continue
        raw_points.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})

    points = _resample_points(raw_points, interval_minutes) if interval_minutes > 1 else raw_points
    points = sorted(points, key=lambda p: p["t"])

    return {
        "code": code,
        "range": range_label,
        "tz": "Asia/Seoul",
        "currency": "KRW",
        "points": points,
        "meta": {"source": "KIS", "interval": f"{interval_minutes}m"},
    }


def transform_series_daily(data: dict[str, Any], code: str, range_label: str) -> dict[str, Any]:
    output2 = data.get("output2") or []
    if not isinstance(output2, list):
        output2 = []

    points: list[dict[str, Any]] = []
    for row in output2:
        date_str = row.get(DAILY_POINT_FIELD_MAP["date"])
        t = _to_epoch_ms(date_str, "000000")
        o = _parse_int(row.get(DAILY_POINT_FIELD_MAP["open"]))
        h = _parse_int(row.get(DAILY_POINT_FIELD_MAP["high"]))
        l = _parse_int(row.get(DAILY_POINT_FIELD_MAP["low"]))
        c = _parse_int(row.get(DAILY_POINT_FIELD_MAP["close"]))
        v = _parse_int(row.get(DAILY_POINT_FIELD_MAP["volume"])) or 0
        if t is None or o is None or h is None or l is None or c is None:
            continue
        points.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})

    points = sorted(points, key=lambda p: p["t"])

    return {
        "code": code,
        "range": range_label,
        "tz": "Asia/Seoul",
        "currency": "KRW",
        "points": points,
        "meta": {"source": "KIS", "interval": "1d"},
    }
