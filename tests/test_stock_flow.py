# tests/test_stock_flow.py
"""
==============================================================================
4주차 2번째 백엔드 통합 테스트 (4/26~28)
주식 시세 조회 + 관심종목 CRUD 플로우
테마 키워드 · 연관 종목 추천 실DB 연동
==============================================================================

test_auth_flow.py 패턴 그대로 사용:
  - conftest.py의 db_session fixture 재사용
  - make_client() 헬퍼 재사용
  - verify_google_login_token mock 패턴 재사용
  - KIS API (kis_service, client)는 mock 처리
==============================================================================
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert

from app.main import app
from app.database import get_db
from app.models import Stock, Watchlist, StockSummaryCache, NewsStockMapping, FilteredNews, Keyword, NewsKeywordMapping, User


# =============================================================================
# 헬퍼: test_auth_flow.py와 동일한 패턴
# =============================================================================

def make_client(db_session: AsyncSession) -> AsyncClient:
    app.dependency_overrides[get_db] = lambda: db_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def make_fake_token_info(
    google_id: str = "stock_test_google_id",
    email: str = "stocktester@gmail.com",
    name: str = "주식테스터",
) -> dict:
    return {
        "sub": google_id,
        "email": email,
        "name": name,
        "picture": "https://example.com/photo.jpg",
        "email_verified": True,
        "iss": "accounts.google.com",
    }


async def get_access_token(client: AsyncClient, google_id: str = "stock_test_google_id") -> str:
    """로그인 후 access_token 반환 (공통 헬퍼)"""
    fake_token_info = make_fake_token_info(google_id=google_id)
    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        res = await client.post("/api/users/login", json={"id_token": "fake_token"})
    assert res.status_code == 200, f"로그인 실패: {res.text}"
    return res.json()["access_token"]


# KIS 시세 mock 반환값 (kis_service.get_stock_price / get_multiple_prices)
MOCK_PRICE_005930 = {"price": 75000, "change_rate": 1.5}
MOCK_PRICE_000660 = {"price": 185000, "change_rate": -0.8}
MOCK_PRICE_035420 = {"price": 210000, "change_rate": 0.3}

MOCK_MULTIPLE_PRICES = {
    "005930": MOCK_PRICE_005930,
    "000660": MOCK_PRICE_000660,
    "035420": MOCK_PRICE_035420,
}

# KIS overview mock (stocks router용)
MOCK_OVERVIEW = {
    "code": "005930",
    "name": "삼성전자",
    "last_price": 75000,
    "change": 1100,
    "change_rate": 1.5,
    "open": 74000,
    "high": 75500,
    "low": 73800,
    "volume": 12000000,
    "trading_value": 900000000000,
    "updated_at": "2026-04-26T10:00:00+09:00",
}


# =============================================================================
# Fixture: 기본 종목 데이터 삽입
# =============================================================================

async def seed_stocks(db_session: AsyncSession) -> None:
    """테스트용 종목 3개 삽입 (없으면 무시)"""
    stocks = [
        Stock(stock_id="005930", stock_name="삼성전자", industry="반도체"),
        Stock(stock_id="000660", stock_name="SK하이닉스", industry="반도체"),
        Stock(stock_id="035420", stock_name="NAVER", industry="IT서비스"),
    ]
    for s in stocks:
        existing = await db_session.get(Stock, s.stock_id)
        if not existing:
            db_session.add(s)
    await db_session.flush()


async def seed_news_data(db_session: AsyncSession) -> None:
    """연관 종목·테마 키워드 테스트용 뉴스 데이터 삽입"""
    await seed_stocks(db_session)

    # StockSummaryCache (NewsStockMapping의 FK 대상)
    for stock_id, stock_name in [("005930", "삼성전자"), ("000660", "SK하이닉스")]:
        existing = await db_session.get(StockSummaryCache, stock_id)
        if not existing:
            db_session.add(StockSummaryCache(
                stock_id=stock_id,
                stock_name=stock_name,
                summary_text=f"{stock_name} 관련 최신 뉴스 요약입니다.",
                latest_news_id=1,
            ))
    await db_session.flush()

    # FilteredNews
    for news_id, sentiment in [(1, "긍정"), (2, "부정"), (3, "중립")]:
        existing = await db_session.get(FilteredNews, news_id)
        if not existing:
            db_session.add(FilteredNews(
                news_id=news_id,
                summary=f"테스트 뉴스 {news_id} 요약",
                sentiment=sentiment,
            ))
    await db_session.flush()

    # NewsStockMapping: 뉴스1,2 → 삼성전자 + SK하이닉스 동시 매핑 (연관종목 생성)
    mappings = [
        NewsStockMapping(stock_id="005930", news_id=1),
        NewsStockMapping(stock_id="005930", news_id=2),
        NewsStockMapping(stock_id="000660", news_id=1),  # 뉴스1 공유 → 연관
        NewsStockMapping(stock_id="000660", news_id=2),  # 뉴스2 공유 → 연관
    ]
    for m in mappings:
        db_session.add(m)
    await db_session.flush()

    # Keyword + NewsKeywordMapping (테마 키워드용)
    kw = Keyword(keyword_id=1, word="반도체")
    existing_kw = await db_session.get(Keyword, 1)
    if not existing_kw:
        db_session.add(kw)
    await db_session.flush()

    existing_nkm = await db_session.get(NewsKeywordMapping, 1)
    if not existing_nkm:
        db_session.add(NewsKeywordMapping(
            mapping_id=1,
            news_id=1,
            keyword_id=1,
        ))
    await db_session.flush()


# =============================================================================
# 1. 주식 시세 조회 (/{code}/overview)
# =============================================================================

class TestStockOverview:

    @pytest.mark.asyncio
    async def test_overview_success(self, db_session: AsyncSession):
        """정상 종목 overview 조회"""
        await seed_stocks(db_session)

        with patch("app.routers.stocks.cache") as mock_cache, \
             patch("app.routers.stocks.client") as mock_kis_client:

            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()
            mock_kis_client.request = AsyncMock(return_value={
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "75000",
                    "prdy_vrss": "1100",
                    "prdy_ctrt": "1.5",
                    "stck_oprc": "74000",
                    "stck_hgpr": "75500",
                    "stck_lwpr": "73800",
                    "acml_vol": "12000000",
                },
            })

            async with make_client(db_session) as client:
                token = await get_access_token(client)
                res = await client.get(
                    "/api/stocks/005930/overview",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert res.status_code == 200, res.text
        data = res.json()
        assert data["code"] == "005930"
        assert "last_price" in data
        assert "change_rate" in data

    @pytest.mark.asyncio
    async def test_overview_unauthorized(self, db_session: AsyncSession):
        """인증 없이 overview 조회 → 403"""
        async with make_client(db_session) as client:
            res = await client.get("/api/stocks/005930/overview")
        assert res.status_code == 403

    @pytest.mark.asyncio
    async def test_overview_invalid_token(self, db_session: AsyncSession):
        """잘못된 토큰으로 overview 조회 → 401"""
        async with make_client(db_session) as client:
            res = await client.get(
                "/api/stocks/005930/overview",
                headers={"Authorization": "Bearer invalid_token_xyz"},
            )
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_overview_cached(self, db_session: AsyncSession):
        """캐시 히트 시 KIS API 미호출"""
        with patch("app.services.stock_service.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=MOCK_OVERVIEW)

            async with make_client(db_session) as client:
                token = await get_access_token(client)
                res = await client.get(
                    "/api/stocks/005930/overview",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert res.status_code == 200
        assert res.json()["last_price"] == 75000


# =============================================================================
# 2. 관심종목 CRUD (/api/watchlist)
# =============================================================================

class TestWatchlistCRUD:

    @pytest.mark.asyncio
    async def test_get_watchlist_empty(self, db_session: AsyncSession):
        """빈 관심종목 → 빈 리스트"""
        with patch("app.routers.watchlist.kis_service") as mock_kis:
            mock_kis.get_multiple_prices = AsyncMock(return_value={})

            async with make_client(db_session) as client:
                token = await get_access_token(client, google_id="empty_watchlist_user")
                res = await client.get(
                    "/api/watchlist",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert res.status_code == 200
        assert res.json() == []

    @pytest.mark.asyncio
    async def test_add_watchlist_success(self, db_session: AsyncSession):
        """관심종목 추가 성공"""
        await seed_stocks(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="add_watchlist_user")
            res = await client.post(
                "/api/watchlist",
                json={"code": "005930"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200, res.text
        data = res.json()
        assert data["code"] == "005930"
        assert "관심종목 추가 완료" in data["message"]

    @pytest.mark.asyncio
    async def test_add_watchlist_duplicate(self, db_session: AsyncSession):
        """중복 추가 → 200 + 이미 추가된 메시지 (IntegrityError 처리)"""
        await seed_stocks(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="dup_watchlist_user")
            headers = {"Authorization": f"Bearer {token}"}

            await client.post("/api/watchlist", json={"code": "005930"}, headers=headers)
            res = await client.post("/api/watchlist", json={"code": "005930"}, headers=headers)

        assert res.status_code == 200
        assert "이미 추가" in res.json()["message"]

    @pytest.mark.asyncio
    async def test_add_watchlist_stock_not_found(self, db_session: AsyncSession):
        """존재하지 않는 종목 추가 → 404"""
        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="notfound_watchlist_user")
            res = await client.post(
                "/api/watchlist",
                json={"code": "999999"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_watchlist_success(self, db_session: AsyncSession):
        """관심종목 삭제 성공"""
        await seed_stocks(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="del_watchlist_user")
            headers = {"Authorization": f"Bearer {token}"}

            # 추가
            await client.post("/api/watchlist", json={"code": "005930"}, headers=headers)
            # 삭제
            res = await client.delete("/api/watchlist/005930", headers=headers)

        assert res.status_code == 200, res.text
        assert "삭제 완료" in res.json()["message"]

    @pytest.mark.asyncio
    async def test_delete_watchlist_not_owned(self, db_session: AsyncSession):
        """다른 유저의 관심종목 삭제 시도 → 200 (삭제 0건, 에러 없음)"""
        await seed_stocks(db_session)

        async with make_client(db_session) as client:
            # user A가 추가
            token_a = await get_access_token(client, google_id="user_a_watchlist")
            await client.post(
                "/api/watchlist", json={"code": "005930"},
                headers={"Authorization": f"Bearer {token_a}"},
            )

            # user B가 삭제 시도
            token_b = await get_access_token(client, google_id="user_b_watchlist")
            res = await client.delete(
                "/api/watchlist/005930",
                headers={"Authorization": f"Bearer {token_b}"},
            )

        # DELETE는 없어도 200 반환 (현재 구현 방식)
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_get_watchlist_with_items(self, db_session: AsyncSession):
        """관심종목 목록 조회 - 항목 있을 때 KIS 시세 포함"""
        await seed_news_data(db_session)

        with patch("app.routers.watchlist.kis_service") as mock_kis, \
             patch("app.routers.watchlist.generate_stock_summary") as mock_summary:

            mock_kis.get_multiple_prices = AsyncMock(return_value=MOCK_MULTIPLE_PRICES)
            mock_summary.return_value = "AI 요약 텍스트입니다."

            async with make_client(db_session) as client:
                token = await get_access_token(client, google_id="list_watchlist_user")
                headers = {"Authorization": f"Bearer {token}"}

                # 종목 추가
                await client.post("/api/watchlist", json={"code": "005930"}, headers=headers)
                await client.post("/api/watchlist", json={"code": "000660"}, headers=headers)

                res = await client.get("/api/watchlist", headers=headers)

        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) == 2

        codes = [item["code"] for item in items]
        assert "005930" in codes
        assert "000660" in codes

        # 응답 필드 확인 (WatchlistStockResponse)
        first = items[0]
        assert "code" in first
        assert "name" in first
        assert "weather" in first
        assert "price" in first
        assert "changeRate" in first
        assert "keyword" in first
        assert "aiSummary" in first

    @pytest.mark.asyncio
    async def test_watchlist_weather_logic(self, db_session: AsyncSession):
        """날씨 로직: changeRate >= 2.0 → SUNNY, <= -2.0 → RAINY, else → CLOUDY"""
        await seed_stocks(db_session)

        mock_prices = {
            "005930": {"price": 75000, "change_rate": 3.0},   # SUNNY
            "000660": {"price": 185000, "change_rate": -3.0},  # RAINY
            "035420": {"price": 210000, "change_rate": 0.5},   # CLOUDY
        }

        with patch("app.routers.watchlist.kis_service") as mock_kis, \
             patch("app.routers.watchlist.generate_stock_summary", return_value="요약"):
            mock_kis.get_multiple_prices = AsyncMock(return_value=mock_prices)

            async with make_client(db_session) as client:
                token = await get_access_token(client, google_id="weather_test_user")
                headers = {"Authorization": f"Bearer {token}"}

                for code in ["005930", "000660", "035420"]:
                    await client.post("/api/watchlist", json={"code": code}, headers=headers)

                res = await client.get("/api/watchlist", headers=headers)

        assert res.status_code == 200
        weather_map = {item["code"]: item["weather"] for item in res.json()}
        assert weather_map["005930"] == "SUNNY"
        assert weather_map["000660"] == "RAINY"
        assert weather_map["035420"] == "CLOUDY"


# =============================================================================
# 3. 연관 종목 추천 (/{stock_code}/related) — 실DB 연동
# =============================================================================

class TestRelatedStocks:

    @pytest.mark.asyncio
    async def test_related_stocks_success(self, db_session: AsyncSession):
        """뉴스 공유 기반 연관 종목 추천"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="related_test_user")
            res = await client.get(
                "/api/stocks/005930/related",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200, res.text
        data = res.json()
        assert "related_stocks" in data

        related_codes = [r["stock_code"] for r in data["related_stocks"]]
        # 삼성전자와 뉴스를 공유한 SK하이닉스가 포함되어야 함
        assert "000660" in related_codes
        # 자기 자신은 포함 안 됨
        assert "005930" not in related_codes

    @pytest.mark.asyncio
    async def test_related_stocks_response_fields(self, db_session: AsyncSession):
        """연관 종목 응답 필드 확인 (RelatedStockItem)"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="related_fields_user")
            res = await client.get(
                "/api/stocks/005930/related",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200
        first = res.json()["related_stocks"][0]
        assert "stock_code" in first
        assert "stock_name" in first
        assert "logo_url" in first
        # logo_url은 SVG data URI
        assert first["logo_url"].startswith("data:image/svg+xml")

    @pytest.mark.asyncio
    async def test_related_stocks_stock_not_found(self, db_session: AsyncSession):
        """존재하지 않는 종목의 연관 종목 조회 → 404"""
        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="related_404_user")
            res = await client.get(
                "/api/stocks/999999/related",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_related_stocks_no_news_data(self, db_session: AsyncSession):
        """뉴스 매핑 없는 종목 → 404 (연관 종목 없음)"""
        await seed_stocks(db_session)  # 뉴스 데이터 없이 종목만

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="related_nonews_user")
            res = await client.get(
                "/api/stocks/035420/related",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_related_stocks_limit(self, db_session: AsyncSession):
        """limit 파라미터 적용 확인"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="related_limit_user")
            res = await client.get(
                "/api/stocks/005930/related?limit=1",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200
        assert len(res.json()["related_stocks"]) <= 1


# =============================================================================
# 4. 테마 키워드 (/{stock_code}/theme-keywords) — 실DB 연동
# =============================================================================

class TestThemeKeywords:

    @pytest.mark.asyncio
    async def test_theme_keywords_success(self, db_session: AsyncSession):
        """테마 키워드 조회 성공"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="theme_kw_user")
            res = await client.get(
                "/api/stocks/005930/theme-keywords",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200, res.text
        data = res.json()
        assert data["stock_code"] == "005930"
        assert data["stock_name"] == "삼성전자"
        assert "core_keyword" in data
        assert "theme_keywords" in data
        assert len(data["theme_keywords"]) > 0

    @pytest.mark.asyncio
    async def test_theme_keywords_response_fields(self, db_session: AsyncSession):
        """테마 키워드 응답 필드 확인 (ThemeKeywordItem)"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="theme_fields_user")
            res = await client.get(
                "/api/stocks/005930/theme-keywords",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200
        kw = res.json()["theme_keywords"][0]
        assert "keyword" in kw
        assert "similarity_score" in kw
        assert "color_level" in kw
        assert kw["color_level"] in ("HIGH", "MEDIUM", "LOW", "NONE")

    @pytest.mark.asyncio
    async def test_theme_keywords_core_message(self, db_session: AsyncSession):
        """core_message 포맷 확인"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="theme_msg_user")
            res = await client.get(
                "/api/stocks/005930/theme-keywords",
                headers={"Authorization": f"Bearer {token}"},
            )

        data = res.json()
        assert "삼성전자" in data["core_message"]
        assert data["core_keyword"] in data["core_message"]

    @pytest.mark.asyncio
    async def test_theme_keywords_stock_not_found(self, db_session: AsyncSession):
        """존재하지 않는 종목 → 404"""
        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="theme_404_user")
            res = await client.get(
                "/api/stocks/999999/theme-keywords",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_theme_keywords_no_data(self, db_session: AsyncSession):
        """키워드 데이터 없는 종목 → 404"""
        await seed_stocks(db_session)  # 키워드 매핑 없이 종목만

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="theme_nodata_user")
            res = await client.get(
                "/api/stocks/035420/theme-keywords",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_theme_keywords_limit(self, db_session: AsyncSession):
        """limit 파라미터 적용"""
        await seed_news_data(db_session)

        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="theme_limit_user")
            res = await client.get(
                "/api/stocks/005930/theme-keywords?limit=1",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 200
        assert len(res.json()["theme_keywords"]) <= 1


# =============================================================================
# 5. 종목 상세 조회 (/api/stocks/{code}) — watchlist router
# =============================================================================

class TestStockDetail:

    @pytest.mark.asyncio
    async def test_stock_detail_success(self, db_session: AsyncSession):
        """종목 상세 조회 성공"""
        await seed_stocks(db_session)

        with patch("app.routers.watchlist.kis_service") as mock_kis:
            mock_kis.get_stock_price = AsyncMock(return_value=MOCK_PRICE_005930)

            async with make_client(db_session) as client:
                token = await get_access_token(client, google_id="detail_test_user")
                res = await client.get(
                    "/api/stocks/005930",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert res.status_code == 200, res.text
        data = res.json()
        assert data["code"] == "005930"
        assert data["name"] == "삼성전자"
        assert "price" in data
        assert "changeRate" in data
        assert "weather" in data

    @pytest.mark.asyncio
    async def test_stock_detail_not_found(self, db_session: AsyncSession):
        """존재하지 않는 종목 상세 조회 → 404"""
        async with make_client(db_session) as client:
            token = await get_access_token(client, google_id="detail_404_user")
            res = await client.get(
                "/api/stocks/999999",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 404


# =============================================================================
# 6. 전체 플로우 E2E
# =============================================================================

class TestStockE2EFlow:

    @pytest.mark.asyncio
    async def test_full_flow(self, db_session: AsyncSession):
        """
        전체 플로우:
        종목 상세 조회 → 관심종목 추가 → 목록 확인 →
        연관 종목 조회 → 테마 키워드 조회 → 관심종목 삭제
        """
        await seed_news_data(db_session)

        with patch("app.routers.watchlist.kis_service") as mock_kis, \
             patch("app.routers.watchlist.generate_stock_summary", return_value="AI 요약"):
            mock_kis.get_stock_price = AsyncMock(return_value=MOCK_PRICE_005930)
            mock_kis.get_multiple_prices = AsyncMock(return_value=MOCK_MULTIPLE_PRICES)

            async with make_client(db_session) as client:
                token = await get_access_token(client, google_id="e2e_flow_user")
                headers = {"Authorization": f"Bearer {token}"}

                # 1) 종목 상세 조회
                detail_res = await client.get("/api/stocks/005930", headers=headers)
                assert detail_res.status_code == 200
                assert detail_res.json()["code"] == "005930"

                # 2) 관심종목 추가
                add_res = await client.post(
                    "/api/watchlist", json={"code": "005930"}, headers=headers
                )
                assert add_res.status_code == 200
                assert "완료" in add_res.json()["message"]

                # 3) 관심종목 목록에 추가된 항목 확인
                list_res = await client.get("/api/watchlist", headers=headers)
                assert list_res.status_code == 200
                codes = [item["code"] for item in list_res.json()]
                assert "005930" in codes

                # 4) 연관 종목 조회
                related_res = await client.get("/api/stocks/005930/related", headers=headers)
                assert related_res.status_code == 200
                assert len(related_res.json()["related_stocks"]) > 0

                # 5) 테마 키워드 조회
                theme_res = await client.get(
                    "/api/stocks/005930/theme-keywords", headers=headers
                )
                assert theme_res.status_code == 200
                assert theme_res.json()["stock_code"] == "005930"

                # 6) 관심종목 삭제
                del_res = await client.delete("/api/watchlist/005930", headers=headers)
                assert del_res.status_code == 200

                # 7) 삭제 후 목록 확인
                list_after_res = await client.get("/api/watchlist", headers=headers)
                assert list_after_res.status_code == 200
                codes_after = [item["code"] for item in list_after_res.json()]
                assert "005930" not in codes_after