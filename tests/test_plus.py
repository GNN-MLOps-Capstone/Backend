# tests/test_notifications_and_news.py
"""
읽지 않은 알림 개수 API 및 종목 최신 뉴스 API 단위 테스트
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.routers.notifications import get_unread_notifications_count
from app.routers.stocks import get_stock_latest_news_endpoint


# =============================================================================
# 공통 헬퍼
# =============================================================================

def _make_user(google_id: str = "google-user-123"):
    user = MagicMock()
    user.google_id = google_id
    return user


def _make_news(
    title: str = "삼성전자 호실적 발표",
    pub_date: datetime | None = datetime(2025, 4, 10),
    news_company_name: str = "한국경제",
    sentiment: float = 0.8,
    is_up: bool = True,
) -> dict:
    return {
        "title": title,
        "pub_date": pub_date,
        "news_company_name": news_company_name,
        "sentiment": sentiment,
        "isUp": is_up,
    }


# =============================================================================
# get_unread_notifications_count 테스트
# =============================================================================

class TestGetUnreadNotificationsCount:

    async def test_읽지않은_알림이_있을_때_개수_반환(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 5
        db.execute.return_value = result_mock

        user = _make_user("google-user-123")
        response = await get_unread_notifications_count(current_user=user, db=db)

        assert response == {"unread_count": 5}

    async def test_읽지않은_알림이_없을_때_0_반환(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 0
        db.execute.return_value = result_mock

        user = _make_user()
        response = await get_unread_notifications_count(current_user=user, db=db)

        assert response == {"unread_count": 0}

    async def test_DB_쿼리가_정확히_한번_실행됨(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 3
        db.execute.return_value = result_mock

        user = _make_user()
        await get_unread_notifications_count(current_user=user, db=db)

        db.execute.assert_called_once()

    async def test_다른_유저의_알림_개수는_독립적(self):
        """google_id가 다른 유저는 각각 독립적으로 쿼리 실행"""
        db = AsyncMock()

        result_a = MagicMock()
        result_a.scalar.return_value = 2
        result_b = MagicMock()
        result_b.scalar.return_value = 7

        db.execute.side_effect = [result_a, result_b]

        user_a = _make_user("google-user-aaa")
        user_b = _make_user("google-user-bbb")

        response_a = await get_unread_notifications_count(current_user=user_a, db=db)
        response_b = await get_unread_notifications_count(current_user=user_b, db=db)

        assert response_a == {"unread_count": 2}
        assert response_b == {"unread_count": 7}

    async def test_반환값에_unread_count_키가_존재(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 1
        db.execute.return_value = result_mock

        user = _make_user()
        response = await get_unread_notifications_count(current_user=user, db=db)

        assert "unread_count" in response

    async def test_알림_개수가_큰_값도_정상_반환(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 9999
        db.execute.return_value = result_mock

        user = _make_user()
        response = await get_unread_notifications_count(current_user=user, db=db)

        assert response == {"unread_count": 9999}


# =============================================================================
# get_stock_latest_news_endpoint 테스트
# =============================================================================

class TestGetStockLatestNewsEndpoint:

    async def test_stock_id로_뉴스_3건_반환(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()  # 종목 존재

        db.execute.return_value = stock_mock

        news_list = [
            _make_news("뉴스1", datetime(2025, 4, 10), "한경", 0.8, True),
            _make_news("뉴스2", datetime(2025, 4, 9), "연합", 0.2, False),
            _make_news("뉴스3", datetime(2025, 4, 8), "매경", -0.3, False),
        ]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id="005930",
                stock_name=None,
                _="google-user-123",
            )

        assert len(result) == 3

    async def test_stock_id와_stock_name_모두_없으면_400(self):
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_stock_latest_news_endpoint(
                db=db,
                stock_id=None,
                stock_name=None,
                _="google-user-123",
            )

        assert exc_info.value.status_code == 400

    async def test_존재하지_않는_stock_id는_404(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = None  # 종목 없음

        db.execute.return_value = stock_mock

        with pytest.raises(HTTPException) as exc_info:
            await get_stock_latest_news_endpoint(
                db=db,
                stock_id="999999",
                stock_name=None,
                _="google-user-123",
            )

        assert exc_info.value.status_code == 404

    async def test_뉴스가_없으면_404(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()

        db.execute.return_value = stock_mock

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = []  # 뉴스 없음
            with pytest.raises(HTTPException) as exc_info:
                await get_stock_latest_news_endpoint(
                    db=db,
                    stock_id="005930",
                    stock_name=None,
                    _="google-user-123",
                )

        assert exc_info.value.status_code == 404

    async def test_날짜_포맷이_YYYY_MM_DD_형식(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()
        db.execute.return_value = stock_mock

        news_list = [_make_news(pub_date=datetime(2025, 4, 10))]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id="005930",
                stock_name=None,
                _="google-user-123",
            )

        # source 형식: "(2025.04.10, 한국경제)"
        assert "2025.04.10" in result[0]["source"]

    async def test_pub_date_없으면_날짜불명_표시(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()
        db.execute.return_value = stock_mock

        news_list = [_make_news(pub_date=None)]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id="005930",
                stock_name=None,
                _="google-user-123",
            )

        assert "날짜 불명" in result[0]["source"]

    async def test_반환_필드_구조_확인(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()
        db.execute.return_value = stock_mock

        news_list = [_make_news()]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id="005930",
                stock_name=None,
                _="google-user-123",
            )

        item = result[0]
        assert "isUp" in item
        assert "title" in item
        assert "source" in item
        assert "sentiment" in item

    async def test_stock_name으로_조회시_stock_id_검증_생략(self):
        """stock_name만 넘기면 종목코드 존재 확인 쿼리를 실행하지 않음"""
        db = AsyncMock()

        news_list = [_make_news()]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id=None,
                stock_name="삼성전자",
                _="google-user-123",
            )

        # db.execute가 호출되지 않아야 함 (stock_id 검증 쿼리 없음)
        db.execute.assert_not_called()
        assert len(result) == 1

    async def test_sentiment_값이_그대로_전달됨(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()
        db.execute.return_value = stock_mock

        news_list = [_make_news(sentiment=-0.75)]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id="005930",
                stock_name=None,
                _="google-user-123",
            )

        assert result[0]["sentiment"] == -0.75

    async def test_isUp_값이_그대로_전달됨(self):
        db = AsyncMock()
        stock_mock = MagicMock()
        stock_mock.scalars.return_value.first.return_value = MagicMock()
        db.execute.return_value = stock_mock

        news_list = [_make_news(is_up=False)]

        with patch(
            "app.routers.stocks.get_latest_stock_news",
            new_callable=AsyncMock,
        ) as mock_news:
            mock_news.return_value = news_list
            result = await get_stock_latest_news_endpoint(
                db=db,
                stock_id="005930",
                stock_name=None,
                _="google-user-123",
            )

        assert result[0]["isUp"] is False