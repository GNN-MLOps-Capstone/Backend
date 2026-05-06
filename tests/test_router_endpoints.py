"""
4/16~4/22 — Backend 라우터 단위 테스트.

외부 KIS/Gemini/OneSignal/실DB 호출 없이 FastAPI 엔드포인트 계약을 검증한다.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import Integer, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def _bootstrap_test_env() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ.setdefault("SECRET_KEY", "test-secret-key")
    os.environ.setdefault("ALGORITHM", "HS256")
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    os.environ["DEBUG"] = "true"


_bootstrap_test_env()

if "apscheduler.schedulers.asyncio" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    schedulers_module = types.ModuleType("apscheduler.schedulers")
    async_scheduler_module = types.ModuleType("apscheduler.schedulers.asyncio")

    class _AsyncIOSchedulerStub:
        def add_job(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def shutdown(self, wait: bool = True):
            return None

    async_scheduler_module.AsyncIOScheduler = _AsyncIOSchedulerStub
    apscheduler_module.schedulers = schedulers_module
    schedulers_module.asyncio = async_scheduler_module
    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = schedulers_module
    sys.modules["apscheduler.schedulers.asyncio"] = async_scheduler_module

from app.database import Base, get_db
from app.main import app
from app.models import (
    CrawledNews,
    FilteredNews,
    InteractionEvent,
    Keyword,
    NaverNews,
    NewsKeywordMapping,
    NewsStockMapping,
    Notification,
    ProcessStatus,
    RecommendationServe,
    Stock,
    StockSummaryCache,
    User,
    UserSettings,
)
from app.routers import news as news_router
from app.routers import stocks as stocks_router
from app.routers import users as users_router
from app.routers import watchlist as watchlist_router
from app.services import onesignal_service


# =============================================================================
# 테스트 공통 fixture / seed helper
# =============================================================================


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function(
            "BTRIM",
            1,
            lambda value: None if value is None else str(value).strip(),
        )

    original_interaction_id_type = InteractionEvent.__table__.c.id.type
    InteractionEvent.__table__.c.id.type = Integer()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield factory

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        InteractionEvent.__table__.c.id.type = original_interaction_id_type
        await engine.dispose()


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def authenticated_user(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    monkeypatch.setattr(
        users_router,
        "verify_google_login_token",
        AsyncMock(
            return_value={
                "sub": "google-week3-user",
                "email": "week3@example.com",
                "name": "위크3",
                "picture": "https://example.com/week3.png",
                "email_verified": True,
                "iss": "accounts.google.com",
            }
        ),
    )

    response = await client.post(
        "/api/users/login",
        json={"id_token": "stub-google-token", "onesignal_id": "player-week3"},
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
        "user": body["user"],
        "token": body["access_token"],
    }


async def _seed_stock(
    session_factory: async_sessionmaker[AsyncSession],
    code: str = "005930",
    name: str = "삼성전자",
    industry: str = "반도체",
    summary: str = "테스트 요약",
) -> None:
    async with session_factory() as session:
        session.add(Stock(stock_id=code, stock_name=name, industry=industry))
        session.add(
            StockSummaryCache(
                stock_id=code,
                stock_name=name,
                summary_text=summary,
            )
        )
        await session.commit()


async def _seed_news(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    news_id: int,
    title: str,
    summary: str,
    pub_date: datetime,
    stock_id: str | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            NaverNews(
                news_id=news_id,
                title=title,
                pub_date=pub_date,
                url=f"https://news.example.com/{news_id}",
                crawl_status=ProcessStatus.crawl_success,
            )
        )
        session.add(
            CrawledNews(
                crawled_news_id=news_id,
                news_id=news_id,
                text=f"{summary} 원문",
            )
        )
        session.add(FilteredNews(news_id=news_id, summary=summary, sentiment="긍정"))
        if stock_id:
            session.add(
                NewsStockMapping(
                    stock_id=stock_id,
                    news_id=news_id,
                    weight=1.0,
                    created_at=pub_date,
                )
            )
        await session.commit()


async def _count_rows(session_factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)))


# =============================================================================
# 주식 라우터 테스트 — 시세, 연관 종목, 테마 키워드
# =============================================================================


class TestStockRouterEndpoints:
    async def test_stock_overview_uses_mocked_kis_quote(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        monkeypatch: pytest.MonkeyPatch,
    ):
        fetched_at = datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc)
        mock_fetch = AsyncMock(
            return_value={
                "code": "005930",
                "name": "삼성전자",
                "last_price": 75000,
                "change": 1200.0,
                "change_rate": 1.63,
                "open": 74000,
                "high": 75500,
                "low": 73500,
                "volume": 123456,
                "trading_value": 900000000,
                "updated_at": fetched_at,
            }
        )
        monkeypatch.setattr(stocks_router, "_fetch_stock_overview", mock_fetch)

        response = await client.get(
            "/api/stocks/005930/overview",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "005930"
        assert body["last_price"] == 75000
        assert body["change_rate"] == 1.63
        mock_fetch.assert_awaited_once_with("005930")

    async def test_related_stocks_returns_cooccurrence_recommendations(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        now = datetime.utcnow()
        async with session_factory() as session:
            for code, name in [
                ("005930", "삼성전자"),
                ("000660", "SK하이닉스"),
                ("035420", "NAVER"),
            ]:
                session.add(Stock(stock_id=code, stock_name=name, industry="테크"))
                session.add(
                    StockSummaryCache(
                        stock_id=code,
                        stock_name=name,
                        summary_text="요약",
                    )
                )
            await session.flush()
            session.add_all(
                [
                    NewsStockMapping(stock_id="005930", news_id=101, created_at=now),
                    NewsStockMapping(stock_id="000660", news_id=101, created_at=now),
                    NewsStockMapping(stock_id="005930", news_id=102, created_at=now),
                    NewsStockMapping(stock_id="000660", news_id=102, created_at=now),
                    NewsStockMapping(stock_id="035420", news_id=102, created_at=now),
                ]
            )
            await session.commit()

        response = await client.get(
            "/api/stocks/005930/related?limit=2",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 200
        related = response.json()["related_stocks"]
        assert [item["stock_code"] for item in related] == ["000660", "035420"]
        assert related[0]["stock_name"] == "SK하이닉스"
        assert related[0]["logo_url"].startswith("data:image/svg+xml;utf8,")

    async def test_related_stocks_returns_404_when_embedding_replacement_data_missing(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        # 4/16~17 계획의 "임베딩 데이터 없는 종목 404"는 현재 라우터가
        # 임베딩 대신 동일 뉴스 공등장 데이터로 연관 종목을 계산하므로,
        # 공등장 대체 데이터가 없는 종목의 404 계약으로 검증한다.
        await _seed_stock(session_factory)

        response = await client.get(
            "/api/stocks/005930/related",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 404
        assert "연관 종목" in response.json()["detail"]

    async def test_theme_keywords_returns_ranked_keywords(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        # 운영 쿼리는 PostgreSQL 캐스트를 사용한다.
        # SQLite 라우터 단위 테스트에서는 SQL 의미를 유지하면서
        # 캐스트 표기만 제거한다.
        original_text = stocks_router.text
        monkeypatch.setattr(
            stocks_router,
            "text",
            lambda sql: original_text(sql.replace("::numeric", "")),
        )

        now = datetime.utcnow()
        async with session_factory() as session:
            session.add(Stock(stock_id="005930", stock_name="삼성전자", industry="반도체"))
            session.add(StockSummaryCache(stock_id="005930", stock_name="삼성전자"))
            session.add_all(
                [
                    NaverNews(
                        news_id=201,
                        title="HBM 수요 확대",
                        pub_date=now,
                        url="https://news.example.com/theme-201",
                        crawl_status=ProcessStatus.crawl_success,
                    ),
                    NaverNews(
                        news_id=202,
                        title="AI 반도체 투자",
                        pub_date=now,
                        url="https://news.example.com/theme-202",
                        crawl_status=ProcessStatus.crawl_success,
                    ),
                ]
            )
            hbm = Keyword(word="HBM")
            ai = Keyword(word="AI")
            session.add_all([hbm, ai])
            await session.flush()
            session.add_all(
                [
                    NewsStockMapping(stock_id="005930", news_id=201, created_at=now),
                    NewsStockMapping(stock_id="005930", news_id=202, created_at=now),
                    NewsKeywordMapping(news_id=201, keyword_id=hbm.keyword_id, created_at=now),
                    NewsKeywordMapping(news_id=202, keyword_id=hbm.keyword_id, created_at=now),
                    NewsKeywordMapping(news_id=202, keyword_id=ai.keyword_id, created_at=now),
                ]
            )
            await session.commit()

        response = await client.get(
            "/api/stocks/005930/theme-keywords?limit=2",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["stock_code"] == "005930"
        assert body["core_keyword"] == "HBM"
        assert [item["keyword"] for item in body["theme_keywords"]] == ["HBM", "AI"]
        assert body["theme_keywords"][0]["similarity_score"] == 1.0
        assert body["theme_keywords"][0]["color_level"] == "HIGH"

    async def test_theme_keywords_returns_404_when_keyword_data_missing(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        original_text = stocks_router.text
        monkeypatch.setattr(
            stocks_router,
            "text",
            lambda sql: original_text(sql.replace("::numeric", "")),
        )
        await _seed_stock(session_factory)

        response = await client.get(
            "/api/stocks/005930/theme-keywords",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 404
        assert "키워드 데이터" in response.json()["detail"]


# =============================================================================
# 뉴스 라우터 테스트 — 목록 조회, 추천 응답 및 노출 로그
# =============================================================================


class TestNewsRouterEndpoints:
    async def test_news_simple_list_returns_recent_filtered_news(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        await _seed_news(
            session_factory,
            news_id=301,
            title="삼성전자 실적 &amp; 투자",
            summary="실적 개선 요약",
            pub_date=datetime(2026, 4, 18, 9, 0),
        )
        await _seed_news(
            session_factory,
            news_id=302,
            title="SK하이닉스 HBM 공급",
            summary="HBM 공급 요약",
            pub_date=datetime(2026, 4, 19, 9, 0),
        )

        response = await client.get(
            "/api/news/simple?limit=2",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["news_id"] for item in body] == [302, 301]
        assert body[1]["title"] == "삼성전자 실적 & 투자"
        assert body[0]["summary"] == "HBM 공급 요약"

    async def test_news_recommendations_returns_items_and_logs_served(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        await _seed_stock(session_factory, code="005930", name="삼성전자")
        await _seed_news(
            session_factory,
            news_id=401,
            title="삼성전자 AI 반도체 확대",
            summary="AI 반도체 투자 확대",
            pub_date=datetime(2026, 4, 19, 10, 0),
            stock_id="005930",
        )
        monkeypatch.setattr(
            news_router,
            "_fetch_change_rates_for_stock_ids",
            AsyncMock(return_value={"005930": 2.4}),
        )

        response = await client.get(
            "/api/news/recommendations?request_id=req-week3&page=1&log_served=true",
            headers=authenticated_user["headers"],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == authenticated_user["user"]["id"]
        assert body["request_id"] == "req-week3"
        assert body["source"] == "recent_news"
        assert body["served_count"] == 1
        assert body["logged"] is True
        assert body["items"][0]["news_id"] == 401
        assert body["items"][0]["path"] == "A1"
        assert body["items"][0]["stock_name"] == "삼성전자"
        assert body["items"][0]["stock_change"] == "+2.4%"
        assert await _count_rows(session_factory, RecommendationServe) == 1


# =============================================================================
# 사용자 / 관심종목 / 알림 / 인터랙션 라우터 테스트
# =============================================================================


class TestUserWatchlistNotificationInteractionEndpoints:
    async def test_user_login_creates_token_user_and_persists_onesignal_id(
        self,
        client: httpx.AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        verify_mock = AsyncMock(
            return_value={
                "sub": "google-direct-login",
                "email": "direct-login@example.com",
                "name": "직접로그인",
                "picture": "https://example.com/direct-login.png",
                "email_verified": True,
                "iss": "accounts.google.com",
            }
        )
        monkeypatch.setattr(users_router, "verify_google_login_token", verify_mock)

        response = await client.post(
            "/api/users/login",
            json={
                "id_token": "direct-google-token",
                "nickname": "fallback-name",
                "img_url": "https://example.com/fallback.png",
                "onesignal_id": "player-direct-login",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "Bearer"
        assert users_router.decode_access_token(body["access_token"]) == "google-direct-login"
        assert body["user"]["google_id"] == "google-direct-login"
        assert body["user"]["email"] == "direct-login@example.com"
        assert body["user"]["nickname"] == "직접로그인"
        assert body["user"]["img_url"] == "https://example.com/direct-login.png"
        verify_mock.assert_awaited_once_with("direct-google-token")

        async with session_factory() as session:
            user = await session.scalar(
                select(User).where(User.google_id == "google-direct-login")
            )
            assert user is not None
            assert user.onesignal_id == "player-direct-login"
            settings = await session.scalar(
                select(UserSettings).where(UserSettings.user_id == user.id)
            )
            assert settings is not None
            assert settings.push is True

    async def test_user_login_profile_and_settings_flow(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
    ):
        headers = authenticated_user["headers"]

        profile_response = await client.get("/api/users/profile", headers=headers)
        assert profile_response.status_code == 200
        assert profile_response.json()["email"] == "week3@example.com"

        settings_response = await client.get("/api/users/settings", headers=headers)
        assert settings_response.status_code == 200
        assert settings_response.json()["push"] is True

        update_response = await client.patch(
            "/api/users/settings",
            headers=headers,
            json={"push": False, "interest_only": True},
        )
        assert update_response.status_code == 200
        assert update_response.json()["push"] is False
        assert update_response.json()["interest_only"] is True

    async def test_watchlist_crud_uses_mocked_kis_prices(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        await _seed_stock(session_factory, summary="관심종목 요약")
        monkeypatch.setattr(
            watchlist_router.kis_service,
            "get_multiple_prices",
            AsyncMock(return_value={"005930": {"price": 75000, "change_rate": 2.1}}),
        )
        headers = authenticated_user["headers"]

        add_response = await client.post(
            "/api/watchlist",
            headers=headers,
            json={"code": "005930"},
        )
        assert add_response.status_code == 200
        assert add_response.json()["message"] == "관심종목 추가 완료"

        duplicate_response = await client.post(
            "/api/watchlist",
            headers=headers,
            json={"code": "005930"},
        )
        assert duplicate_response.status_code == 200
        assert duplicate_response.json()["message"] == "이미 추가된 종목입니다"

        list_response = await client.get("/api/watchlist", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json() == [
            {
                "code": "005930",
                "name": "삼성전자",
                "weather": "SUNNY",
                "price": 75000,
                "changeRate": 2.1,
                "keyword": "반도체",
                "aiSummary": "관심종목 요약",
            }
        ]

        delete_response = await client.delete("/api/watchlist/005930", headers=headers)
        assert delete_response.status_code == 200
        assert delete_response.json()["message"] == "관심종목 삭제 완료"

        empty_response = await client.get("/api/watchlist", headers=headers)
        assert empty_response.status_code == 200
        assert empty_response.json() == []

    async def test_notification_create_list_read_toggle_and_delete(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        headers = authenticated_user["headers"]

        create_response = await client.post(
            "/api/notifications",
            headers=headers,
            json={
                "notification_id": "push-week3-1",
                "type": "risk",
                "title": "삼성전자 변동 알림",
                "body": "전일 대비 상승 중입니다.",
                "stock_name": "삼성전자",
                "sentiment_score": 2.1,
            },
        )
        assert create_response.status_code == 201
        notification_id = create_response.json()["id"]

        duplicate_response = await client.post(
            "/api/notifications",
            headers=headers,
            json={
                "notification_id": "push-week3-1",
                "type": "risk",
                "title": "삼성전자 변동 알림",
                "body": "전일 대비 상승 중입니다.",
                "stock_name": "삼성전자",
                "sentiment_score": 2.1,
            },
        )
        assert duplicate_response.status_code == 200

        list_response = await client.get("/api/notifications", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json()[0]["read"] is False

        read_response = await client.patch(
            "/api/notifications/read",
            headers=headers,
            json={"id": notification_id},
        )
        assert read_response.status_code == 200
        assert read_response.json()["unread_count"] == 0

        important_response = await client.patch(
            "/api/notifications/important",
            headers=headers,
            json={"id": notification_id},
        )
        assert important_response.status_code == 200
        assert important_response.json()["star"] is True

        delete_response = await client.delete(
            f"/api/notifications/{notification_id}",
            headers=headers,
        )
        assert delete_response.status_code == 200
        assert delete_response.json() == {"success": True, "id": notification_id}
        assert await _count_rows(session_factory, Notification) == 0

    async def test_send_volatility_push_calls_onesignal_and_saves_notification(
        self,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        calls: list[dict] = []

        class FakeOneSignalResponse:
            status_code = 200

            def json(self) -> dict:
                return {"id": "onesignal-week3-id"}

        class FakeAsyncClient:
            def __init__(self, *, timeout: float):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url: str, *, json: dict, headers: dict):
                calls.append({"url": url, "json": json, "headers": headers})
                return FakeOneSignalResponse()

        monkeypatch.setattr(onesignal_service.settings, "onesignal_app_id", "app-week3")
        monkeypatch.setattr(
            onesignal_service.settings,
            "onesignal_rest_api_key",
            "rest-key-week3",
        )
        monkeypatch.setattr(onesignal_service.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(onesignal_service, "AsyncSessionLocal", session_factory)
        monkeypatch.setattr(onesignal_service, "pg_insert", sqlite_insert)

        push_sent, db_saved = await onesignal_service.send_volatility_push_and_save(
            [authenticated_user["user"]["google_id"]],
            "삼성전자",
            date(2026, 4, 21),
            rate=12.3,
            alert_type="high_risk",
        )

        assert push_sent is True
        assert db_saved is True
        assert len(calls) == 1
        call = calls[0]
        assert call["url"] == "https://api.onesignal.com/notifications"
        assert call["headers"]["Authorization"] == "Key rest-key-week3"
        assert call["json"]["app_id"] == "app-week3"
        assert call["json"]["include_aliases"] == {
            "external_id": [authenticated_user["user"]["google_id"]]
        }
        assert call["json"]["target_channel"] == "push"
        assert call["json"]["data"] == {"type": "high_risk", "stock_name": "삼성전자"}

        async with session_factory() as session:
            notification = await session.scalar(select(Notification))
            assert notification is not None
            assert notification.user_id == authenticated_user["user"]["google_id"]
            assert notification.onesignal_notification_id == "onesignal-week3-id"
            assert notification.type == "high_risk"
            assert notification.stock_name == "삼성전자"
            assert notification.sentiment_score == 12.3

    async def test_interactions_ingest_and_duplicate_detection(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        headers = authenticated_user["headers"]
        user_id = authenticated_user["user"]["id"]
        payload = {
            "events": [
                {
                    "event_id": "event-screen-1",
                    "user_id": user_id,
                    "event_type": "screen_view",
                    "screen_session_id": "screen-week3",
                    "app_session_id": "app-week3",
                },
                {
                    "event_id": "event-impression-1",
                    "user_id": user_id,
                    "event_type": "recommendation_impression",
                    "screen_session_id": "screen-week3",
                    "request_id": "req-week3",
                    "news_id": 401,
                    "position": 1,
                },
            ]
        }

        first_response = await client.post(
            "/api/interactions/events",
            headers=headers,
            json=payload,
        )
        assert first_response.status_code == 200
        assert first_response.json() == {"accepted": 2, "duplicated": 0}

        duplicate_response = await client.post(
            "/api/interactions/events",
            headers=headers,
            json=payload,
        )
        assert duplicate_response.status_code == 200
        assert duplicate_response.json() == {"accepted": 0, "duplicated": 2}
        assert await _count_rows(session_factory, InteractionEvent) == 2

    async def test_interactions_rejects_mismatched_user_id(
        self,
        client: httpx.AsyncClient,
        authenticated_user: dict,
    ):
        response = await client.post(
            "/api/interactions/events",
            headers=authenticated_user["headers"],
            json={
                "events": [
                    {
                        "event_id": "event-forbidden",
                        "user_id": authenticated_user["user"]["id"] + 1,
                        "event_type": "screen_view",
                        "screen_session_id": "screen-week3",
                    }
                ]
            },
        )

        assert response.status_code == 403
        assert "does not match" in response.json()["detail"]
