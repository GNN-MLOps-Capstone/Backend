# tests/test_weather.py
"""
4/10 — GNN 기반 감성 날씨 산출 및 AI 트렌드 단위 테스트
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.routers.stocks import get_weather, get_stock_weather, get_ai_trends


# =============================================================================
# get_weather() 테스트 — 순수 함수, 모킹 불필요
# =============================================================================

class TestGetWeather:

    # --- 주가만 있을 때 (감성 None) ---

    def test_주가_급락_감성없음_THUNDERSTORM(self):
        assert get_weather(-6.0, None) == "THUNDERSTORM"

    def test_주가_5퍼센트_감성없음_THUNDERSTORM(self):
        assert get_weather(-5.0, None) == "THUNDERSTORM"

    def test_주가_소폭하락_감성없음_RAINY(self):
        assert get_weather(-2.0, None) == "RAINY"

    def test_주가_1퍼센트_감성없음_RAINY(self):
        assert get_weather(-1.0, None) == "RAINY"

    def test_주가_보합_감성없음_CLOUDY(self):
        assert get_weather(0.0, None) == "CLOUDY"

    def test_주가_소폭상승_감성없음_PARTLY_CLOUDY(self):
        assert get_weather(2.0, None) == "PARTLY_CLOUDY"

    def test_주가_5퍼센트상승_감성없음_SUNNY(self):
        assert get_weather(5.0, None) == "SUNNY"

    def test_주가_급등_감성없음_SUNNY(self):
        assert get_weather(10.0, None) == "SUNNY"

    def test_주가_None_감성없음_CLOUDY(self):
        assert get_weather(None, None) == "CLOUDY"

    # --- 감성 영향 ---

    def test_주가_보합_감성긍정_PARTLY_CLOUDY(self):
        # price=0, sentiment=+1 → total=1
        assert get_weather(0.0, 0.5) == "PARTLY_CLOUDY"

    def test_주가_보합_감성부정_RAINY(self):
        # price=0, sentiment=-1 → total=-1
        assert get_weather(0.0, -0.5) == "RAINY"

    def test_주가_소폭상승_감성긍정_SUNNY(self):
        # price=+1, sentiment=+1 → total=2
        assert get_weather(2.0, 0.8) == "SUNNY"

    def test_주가_소폭하락_감성부정_THUNDERSTORM(self):
        # price=-1, sentiment=-1 → total=-2
        assert get_weather(-2.0, -0.8) == "THUNDERSTORM"

    def test_주가_급락_감성긍정_RAINY(self):
        # price=-2, sentiment=+1 → total=-1
        assert get_weather(-6.0, 0.9) == "RAINY"

    def test_주가_급등_감성부정_PARTLY_CLOUDY(self):
        # price=+2, sentiment=-1 → total=1
        assert get_weather(10.0, -0.9) == "PARTLY_CLOUDY"

    def test_감성_0_중립처리(self):
        # avg_sentiment=0.0 → sentiment_score=0
        assert get_weather(0.0, 0.0) == "CLOUDY"

    # --- 경계값 ---

    def test_주가_경계값_minus1(self):
        # -1.0 이하 → price_score=-1
        assert get_weather(-1.0, None) == "RAINY"

    def test_주가_경계값_plus1(self):
        # 1.0 이상 ~ 5.0 미만 → price_score=+1
        assert get_weather(1.0, None) == "PARTLY_CLOUDY"

    def test_주가_경계값_minus5(self):
        # -5.0 이하 → price_score=-2
        assert get_weather(-5.0, None) == "THUNDERSTORM"

    def test_주가_경계값_plus5(self):
        # 5.0 이상 → price_score=+2
        assert get_weather(5.0, None) == "SUNNY"

    def test_주가_0_99_보합(self):
        # 0.99 → price_score=0
        assert get_weather(0.99, None) == "CLOUDY"

    def test_주가_minus0_99_보합(self):
        # -0.99 → price_score=0 (>-1.0)
        assert get_weather(-0.99, None) == "CLOUDY"


# =============================================================================
# get_stock_weather() 테스트 — DB + _fetch_stock_overview 모킹
# =============================================================================

class TestGetStockWeather:

    def _make_db(self, stock_exists=True, multi_stock=False, avg_sentiment=None):
        """DB 세션 모킹 헬퍼"""
        db = AsyncMock()

        # stock 존재 여부 쿼리 결과
        stock_result = MagicMock()
        stock_result.scalar_one_or_none.return_value = "005930" if stock_exists else None

        # 감성 집계 쿼리 결과
        sentiment_result = MagicMock()
        sentiment_result.scalar_one_or_none.return_value = avg_sentiment

        # 종목명 조회 결과 (stock_name으로 조회할 때)
        stock_ids_result = MagicMock()
        if multi_stock:
            stock_ids_result.scalars.return_value.all.return_value = ["005930", "000660"]
        elif stock_exists:
            stock_ids_result.scalars.return_value.all.return_value = ["005930"]
        else:
            stock_ids_result.scalars.return_value.all.return_value = []

        db.execute.side_effect = [stock_result, sentiment_result]
        return db

    async def test_stock_id로_CLOUDY_반환(self):
        db = AsyncMock()
        # 1번째 execute: 종목 존재 확인
        exist_mock = MagicMock()
        exist_mock.scalar_one_or_none.return_value = "005930"
        # 2번째 execute: 감성 집계
        sentiment_mock = MagicMock()
        sentiment_mock.scalar_one_or_none.return_value = None

        db.execute.side_effect = [exist_mock, sentiment_mock]

        with patch("app.routers.stocks._fetch_stock_overview", new_callable=AsyncMock) as mock_overview:
            mock_overview.return_value = {"change_rate": 0.0}
            result = await get_stock_weather(db, stock_id="005930")

        assert result == "CLOUDY"

    async def test_stock_id로_SUNNY_반환(self):
        db = AsyncMock()
        exist_mock = MagicMock()
        exist_mock.scalar_one_or_none.return_value = "005930"
        sentiment_mock = MagicMock()
        sentiment_mock.scalar_one_or_none.return_value = 0.8  # 긍정

        db.execute.side_effect = [exist_mock, sentiment_mock]

        with patch("app.routers.stocks._fetch_stock_overview", new_callable=AsyncMock) as mock_overview:
            mock_overview.return_value = {"change_rate": 6.0}  # 급등
            result = await get_stock_weather(db, stock_id="005930")

        assert result == "SUNNY"

    async def test_종목_없으면_404(self):
        db = AsyncMock()
        exist_mock = MagicMock()
        exist_mock.scalar_one_or_none.return_value = None  # 종목 없음

        db.execute.side_effect = [exist_mock]

        with pytest.raises(HTTPException) as exc_info:
            await get_stock_weather(db, stock_id="999999")

        assert exc_info.value.status_code == 404

    async def test_stock_id_stock_name_둘다_없으면_ValueError(self):
        db = AsyncMock()
        with pytest.raises(ValueError):
            await get_stock_weather(db)

    async def test_stock_name으로_조회_성공(self):
        db = AsyncMock()
        # 1번째 execute: 종목명으로 stock_id 조회
        name_mock = MagicMock()
        name_mock.scalars.return_value.all.return_value = ["005930"]
        # 2번째 execute: 감성 집계
        sentiment_mock = MagicMock()
        sentiment_mock.scalar_one_or_none.return_value = None

        db.execute.side_effect = [name_mock, sentiment_mock]

        with patch("app.routers.stocks._fetch_stock_overview", new_callable=AsyncMock) as mock_overview:
            mock_overview.return_value = {"change_rate": 2.0}
            result = await get_stock_weather(db, stock_name="삼성전자")

        assert result == "PARTLY_CLOUDY"

    async def test_stock_name_중복이면_400(self):
        db = AsyncMock()
        name_mock = MagicMock()
        name_mock.scalars.return_value.all.return_value = ["005930", "000660"]  # 중복

        db.execute.side_effect = [name_mock]

        with pytest.raises(HTTPException) as exc_info:
            await get_stock_weather(db, stock_name="삼성")

        assert exc_info.value.status_code == 400

    async def test_change_rate_없으면_감성만으로_날씨(self):
        """KIS에서 change_rate가 None이면 감성만으로 날씨 결정"""
        db = AsyncMock()
        exist_mock = MagicMock()
        exist_mock.scalar_one_or_none.return_value = "005930"
        sentiment_mock = MagicMock()
        sentiment_mock.scalar_one_or_none.return_value = 0.9  # 긍정

        db.execute.side_effect = [exist_mock, sentiment_mock]

        with patch("app.routers.stocks._fetch_stock_overview", new_callable=AsyncMock) as mock_overview:
            mock_overview.return_value = {"change_rate": None}
            result = await get_stock_weather(db, stock_id="005930")

        # price_score=0, sentiment_score=+1 → PARTLY_CLOUDY
        assert result == "PARTLY_CLOUDY"


# =============================================================================
# get_ai_trends() 테스트 — DB 모킹
# =============================================================================

class TestGetAiTrends:

    def _make_vol_row(self, stock_id, stock_name, count):
        row = MagicMock()
        row.stock_id = stock_id
        row.stock_name = stock_name
        row.recent_news_count = count
        return row

    def _make_sent_row(self, stock_id, avg_sentiment):
        row = MagicMock()
        row.stock_id = stock_id
        row.avg_sentiment = avg_sentiment
        return row

    async def test_뉴스_없으면_빈_리스트(self):
        db = AsyncMock()
        vol_result = MagicMock()
        vol_result.all.return_value = []
        db.execute.return_value = vol_result

        result = await get_ai_trends(db, top_n=3)
        assert result == []

    async def test_top3_반환(self):
        db = AsyncMock()

        vol_rows = [
            self._make_vol_row("005930", "삼성전자", 10),
            self._make_vol_row("000660", "SK하이닉스", 7),
            self._make_vol_row("035420", "NAVER", 4),
        ]
        sent_rows = [
            self._make_sent_row("005930", 0.8),
            self._make_sent_row("000660", -0.3),
            self._make_sent_row("035420", 0.1),
        ]

        vol_result = MagicMock()
        vol_result.all.return_value = vol_rows
        sent_result = MagicMock()
        sent_result.all.return_value = sent_rows

        db.execute.side_effect = [vol_result, sent_result]

        result = await get_ai_trends(db, top_n=3)

        assert len(result) == 3
        assert result[0]["rank"] == 1
        assert result[0]["code"] == "005930"  # 감성 가장 강함 → 1위

    async def test_rank_순서_정확(self):
        db = AsyncMock()

        vol_rows = [
            self._make_vol_row("A00001", "종목A", 5),
            self._make_vol_row("B00002", "종목B", 5),
        ]
        sent_rows = [
            self._make_sent_row("A00001", 0.9),   # score 높음
            self._make_sent_row("B00002", 0.1),   # score 낮음
        ]

        vol_result = MagicMock()
        vol_result.all.return_value = vol_rows
        sent_result = MagicMock()
        sent_result.all.return_value = sent_rows

        db.execute.side_effect = [vol_result, sent_result]

        result = await get_ai_trends(db, top_n=2)

        assert result[0]["code"] == "A00001"
        assert result[1]["code"] == "B00002"

    async def test_top_n_개수_제한(self):
        db = AsyncMock()

        vol_rows = [self._make_vol_row(f"{i:06d}", f"종목{i}", i + 1) for i in range(5)]
        sent_rows = [self._make_sent_row(f"{i:06d}", 0.1 * i) for i in range(5)]

        vol_result = MagicMock()
        vol_result.all.return_value = vol_rows
        sent_result = MagicMock()
        sent_result.all.return_value = sent_rows

        db.execute.side_effect = [vol_result, sent_result]

        result = await get_ai_trends(db, top_n=2)
        assert len(result) == 2

    async def test_감성데이터_없는_종목_0점처리(self):
        """감성 데이터가 없는 종목은 avg_sentiment=0으로 처리"""
        db = AsyncMock()

        vol_rows = [self._make_vol_row("005930", "삼성전자", 5)]
        sent_rows = []  # 감성 데이터 없음

        vol_result = MagicMock()
        vol_result.all.return_value = vol_rows
        sent_result = MagicMock()
        sent_result.all.return_value = sent_rows

        db.execute.side_effect = [vol_result, sent_result]

        result = await get_ai_trends(db, top_n=1)

        assert len(result) == 1
        assert result[0]["avg_sentiment"] == 0.0

    async def test_반환_필드_구조(self):
        """반환 딕셔너리에 필요한 필드가 모두 있는지 확인"""
        db = AsyncMock()

        vol_rows = [self._make_vol_row("005930", "삼성전자", 3)]
        sent_rows = [self._make_sent_row("005930", 0.5)]

        vol_result = MagicMock()
        vol_result.all.return_value = vol_rows
        sent_result = MagicMock()
        sent_result.all.return_value = sent_rows

        db.execute.side_effect = [vol_result, sent_result]

        result = await get_ai_trends(db, top_n=1)

        item = result[0]
        assert "rank" in item
        assert "code" in item
        assert "name" in item
        assert "avg_sentiment" in item
        assert "news_count" in item
        assert "score" in item
