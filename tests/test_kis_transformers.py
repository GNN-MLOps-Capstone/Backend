"""
4/11 — KIS 시세 데이터 변환 및 가공 기능 단위 테스트
"""

from __future__ import annotations

from datetime import datetime

from app.kis.transformers import KST, transform_overview, transform_series_daily, transform_series_time


def _epoch_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, second, tzinfo=KST).timestamp() * 1000)


# =============================================================================
# 시세 개요 변환 테스트
# =============================================================================

class TestTransformOverview:
    def test_현재가_개요_숫자와_부호_변환(self):
        data = {
            "output": {
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "75,000",
                "prdy_vrss": "1,200",
                "prdy_vrss_sign": "5",
                "prdy_ctrt": "1.58",
                "stck_oprc": "74,000",
                "stck_hgpr": "76,000",
                "stck_lwpr": "73,500",
                "acml_vol": "1,234,567",
                "acml_tr_pbmn": "12,345,678",
            }
        }

        overview = transform_overview(data, "005930")

        assert overview["code"] == "005930"
        assert overview["name"] == "삼성전자"
        assert overview["last_price"] == 75000
        assert overview["change"] == -1200.0
        assert overview["change_rate"] == -1.58
        assert overview["open"] == 74000
        assert overview["high"] == 76000
        assert overview["low"] == 73500
        assert overview["volume"] == 1234567
        assert overview["trading_value"] == 12345678
        assert isinstance(overview["updated_at"], str)


# =============================================================================
# 분봉 시계열 변환 테스트
# =============================================================================

class TestTransformSeriesTime:
    def test_분봉_리샘플링과_정렬(self):
        data = {
            "output2": [
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "0920",
                    "stck_oprc": "106",
                    "stck_hgpr": "108",
                    "stck_lwpr": "105",
                    "stck_prpr": "107",
                    "cntg_vol": "3",
                },
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "091701",
                    "stck_oprc": "101",
                    "stck_hgpr": "106",
                    "stck_lwpr": "100",
                    "stck_prpr": "104",
                    "cntg_vol": "10",
                },
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "091501",
                    "stck_oprc": "100",
                    "stck_hgpr": "105",
                    "stck_lwpr": "99",
                    "stck_prpr": "101",
                    "cntg_vol": "5",
                },
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "091959",
                    "stck_oprc": "104",
                    "stck_hgpr": "107",
                    "stck_lwpr": "103",
                    "stck_prpr": "106",
                    "cntg_vol": "7",
                },
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "092501",
                    "stck_oprc": "107",
                    "stck_hgpr": "109",
                    "stck_lwpr": "106",
                    "stck_prpr": "",
                    "cntg_vol": "2",
                },
            ]
        }

        series = transform_series_time(data, "005930", "1d", interval_minutes=5)

        assert series["code"] == "005930"
        assert series["meta"]["interval"] == "5m"
        assert series["points"] == [
            {
                "t": _epoch_ms(2026, 4, 7, 9, 15),
                "o": 100,
                "h": 107,
                "l": 99,
                "c": 106,
                "v": 22,
            },
            {
                "t": _epoch_ms(2026, 4, 7, 9, 20),
                "o": 106,
                "h": 108,
                "l": 105,
                "c": 107,
                "v": 3,
            },
        ]

    def test_interval_1분은_원본_포인트_유지(self):
        data = {
            "output2": [
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "090200",
                    "stck_oprc": "102",
                    "stck_hgpr": "103",
                    "stck_lwpr": "101",
                    "stck_prpr": "102",
                    "cntg_vol": "8",
                },
                {
                    "stck_bsop_date": "20260407",
                    "stck_cntg_hour": "090100",
                    "stck_oprc": "100",
                    "stck_hgpr": "102",
                    "stck_lwpr": "99",
                    "stck_prpr": "101",
                    "cntg_vol": "5",
                },
            ]
        }

        series = transform_series_time(data, "005930", "1d", interval_minutes=1)

        assert series["points"] == [
            {
                "t": _epoch_ms(2026, 4, 7, 9, 1),
                "o": 100,
                "h": 102,
                "l": 99,
                "c": 101,
                "v": 5,
            },
            {
                "t": _epoch_ms(2026, 4, 7, 9, 2),
                "o": 102,
                "h": 103,
                "l": 101,
                "c": 102,
                "v": 8,
            },
        ]


# =============================================================================
# 일봉 시계열 변환 테스트
# =============================================================================

class TestTransformSeriesDaily:
    def test_일봉_정렬과_유효행만_변환(self):
        data = {
            "output2": [
                {
                    "stck_bsop_date": "20260409",
                    "stck_oprc": "72000",
                    "stck_hgpr": "72500",
                    "stck_lwpr": "71500",
                    "stck_clpr": "abc",
                    "acml_vol": "20000",
                },
                {
                    "stck_bsop_date": "20260407",
                    "stck_oprc": "70000",
                    "stck_hgpr": "71000",
                    "stck_lwpr": "69500",
                    "stck_clpr": "70500",
                    "acml_vol": "15000",
                },
                {
                    "stck_bsop_date": "20260408",
                    "stck_oprc": "70500",
                    "stck_hgpr": "71500",
                    "stck_lwpr": "70000",
                    "stck_clpr": "71200",
                    "acml_vol": "18000",
                },
            ]
        }

        series = transform_series_daily(data, "005930", "1m")

        assert series["meta"]["interval"] == "1d"
        assert series["points"] == [
            {
                "t": _epoch_ms(2026, 4, 7),
                "o": 70000,
                "h": 71000,
                "l": 69500,
                "c": 70500,
                "v": 15000,
            },
            {
                "t": _epoch_ms(2026, 4, 8),
                "o": 70500,
                "h": 71500,
                "l": 70000,
                "c": 71200,
                "v": 18000,
            },
        ]
