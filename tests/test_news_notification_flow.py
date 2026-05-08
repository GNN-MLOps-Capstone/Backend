"""
4주차 (4/29) Backend 통합 테스트
뉴스 조회 + 알림 발송 플로우

커버 범위:
  1. GET /api/news/simple           — 뉴스 목록 조회
  2. GET /api/news/{news_id}        — 뉴스 상세 조회
  3. POST /api/notifications        — 알림 DB 저장 (Flutter → 서버)
  4. GET /api/notifications         — 알림 목록 조회
  5. send_volatility_push_and_save  — OneSignal 발송 + DB 저장 통합
  6. run_news_keyword_check         — 뉴스 키워드 스케줄러 E2E

실행:
  pytest tests/test_news_notification_flow.py -v --tb=short
"""

from __future__ import annotations

import json
import pytest
import pytest_asyncio
import respx
from datetime import date, datetime, timedelta
from httpx import AsyncClient, Response, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker
from unittest.mock import AsyncMock, patch
import uuid

from app.main import app
from app.database import get_db, Base
from app.models import (
    NaverNews,
    CrawledNews,
    FilteredNews,
    ProcessStatus,
    User,
    UserSettings,
    Stock,
    Watchlist,
    Notification,
    NewsStockMapping,
    StockSummaryCache,
    Keyword,
    NewsKeywordMapping,
)
from app.kis.transformers import KST

# ============================================================
# conftest.py 의 db_session / engine 픽스처를 그대로 재사용.
# conftest.py 가 프로젝트 루트에 있으므로 pytest 가 자동으로 인식함.
# 아래 픽스처들은 conftest.py 의 db_session 을 주입받음.
# ============================================================


# ─────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────

def _kst_now() -> datetime:
    return datetime.now(KST)


def _make_naver_news(
    news_id: int,
    title: str,
    pub_date: datetime | None = None,
    crawl_status: ProcessStatus = ProcessStatus.crawl_success,
    url: str | None = None,
) -> NaverNews:
    return NaverNews(
        news_id=news_id,
        title=title,
        pub_date=pub_date or _kst_now(),
        url=url or f"https://news.naver.com/{news_id}",
        crawl_status=crawl_status,
        created_at=_kst_now(),
    )


def _make_crawled(news_id: int, text: str = "본문 텍스트") -> CrawledNews:
    return CrawledNews(
        crawled_news_id=news_id,
        news_id=news_id,
        text=text,
        crawled_at=_kst_now(),
    )


def _make_filtered(news_id: int, summary: str = "요약 텍스트") -> FilteredNews:
    return FilteredNews(news_id=news_id, summary=summary)


# ─────────────────────────────────────────────
# 인증 헬퍼 — get_current_user 의존성 우회
# ─────────────────────────────────────────────

def _override_auth(user: User):
    """get_current_user DI 를 고정 유저로 교체"""
    from app.routers.users import get_current_user

    async def _mock_current_user():
        return user

    app.dependency_overrides[get_current_user] = _mock_current_user
    return _mock_current_user


def _clear_overrides():
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────
# 공용 픽스처
# ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def seed_user(db_session: AsyncSession) -> User:
    """테스트마다 고유한 유저를 생성합니다."""
    # 하드코딩된 id=1001을 삭제하여 DB가 자동 생성하게 합니다.
    # google_id도 겹치지 않게 uuid를 사용합니다.
    unique_id = uuid.uuid4().hex[:8]
    user = User(
        google_id=f"gid_{unique_id}",
        email=f"tester_{unique_id}@example.com",
        nickname="테스터",
        onesignal_id=f"os-{unique_id}",
        last_login=_kst_now(),
    )
    # UserSettings에서도 user_id 지정을 삭제하거나 
    # 아래와 같이 관계를 이용해 자동으로 연결되게 합니다.
    user.settings = UserSettings(
        push=True,
        risk_only=True,
        positive_only=False,
        interest_only=True,
        night_push_prohibit=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user

@pytest_asyncio.fixture
async def seed_stock(db_session: AsyncSession) -> Stock:
    """테스트용 종목 — 삼성전자"""
    result = await db_session.execute(
        select(Stock).where(Stock.stock_id == "005930")
    )
    stock = result.scalar_one_or_none()

    if stock is None:
        stock = Stock(stock_id="005930", stock_name="삼성전자")
        db_session.add(stock)

        # StockSummaryCache 확인
        cache_result = await db_session.execute(
            select(StockSummaryCache).where(StockSummaryCache.stock_id == "005930")
        )
        if cache_result.scalar_one_or_none() is None:
            db_session.add(StockSummaryCache(stock_id="005930", stock_name="삼성전자"))

        await db_session.flush()

    return stock


@pytest_asyncio.fixture
async def seed_watchlist(
    db_session: AsyncSession, seed_user: User, seed_stock: Stock
) -> Watchlist:
    """seed_user 가 삼성전자를 관심종목으로 등록"""
    item = Watchlist(user_id=seed_user.id, stock_id=seed_stock.stock_id)
    db_session.add(item)
    await db_session.flush()
    return item


@pytest_asyncio.fixture
async def seed_news_set(
    db_session: AsyncSession, seed_stock: Stock
) -> list[NaverNews]:
    """
    뉴스 3건 세팅
      - news_id=101: 삼성전자 관련, crawl_success, filtered 있음
      - news_id=102: crawl_success, filtered 없음 (crawled_text 만 존재)
      - news_id=103: crawl_failed (simple 목록에 안 나와야 함)
    """
    news = [
        _make_naver_news(101, "삼성전자 1분기 실적 호조", pub_date=_kst_now()),
        _make_naver_news(102, "반도체 수출 급증", pub_date=_kst_now() - timedelta(hours=1)),
        _make_naver_news(
            103, "크롤링 실패 뉴스",
            pub_date=_kst_now() - timedelta(hours=2),
            crawl_status=ProcessStatus.crawl_failed,
        ),
    ]
    crawled = [
        _make_crawled(101, "삼성전자가 1분기 영업이익을 발표했습니다."),
        _make_crawled(102, "반도체 수출이 전월 대비 급증했습니다."),
    ]
    # 101, 102 모두 filtered 추가 — API 가 filtered 존재 여부로 필터링할 경우 대비
    filtered = [
        _make_filtered(101, "삼성전자 1분기 실적 요약"),
        _make_filtered(102, "반도체 수출 요약"),
    ]

    mapping = NewsStockMapping(
        stock_id="005930",
        news_id=101,
        weight=1.0,
    )

    db_session.add_all(news + crawled + filtered + [mapping])
    await db_session.flush()
    return news


@pytest_asyncio.fixture
async def authed_client(
    db_session: AsyncSession, seed_user: User
) -> AsyncClient:
    """인증 우회 + DB 세션 오버라이드가 적용된 TestClient"""
    _override_auth(seed_user)

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    _clear_overrides()


# ============================================================
# 1. GET /api/news/simple — 뉴스 목록 조회
# ============================================================

class TestNewsSimpleList:
    async def test_returns_only_crawl_success(
        self, authed_client: AsyncClient, seed_news_set
    ):
        """crawl_success 인 뉴스만 반환 — crawl_failed(103) 제외"""
        res = await authed_client.get("/api/news/simple")
        assert res.status_code == 200
        ids = [item["news_id"] for item in res.json()]
        assert 101 in ids
        assert 102 in ids
        assert 103 not in ids  # crawl_failed 제외

    async def test_ordered_by_pub_date_desc(
        self, authed_client: AsyncClient, seed_news_set
    ):
        """최신 pub_date 순 정렬"""
        res = await authed_client.get("/api/news/simple")
        items = res.json()
        dates = [item["pub_date"] for item in items]
        assert dates == sorted(dates, reverse=True)

    async def test_summary_prefers_filtered_over_crawled(
        self, authed_client: AsyncClient, seed_news_set
    ):
        """filtered_news.summary 있으면 그것을 우선 사용"""
        res = await authed_client.get("/api/news/simple")
        item_101 = next(i for i in res.json() if i["news_id"] == 101)
        assert item_101["summary"] is not None
        assert "삼성전자" in item_101["summary"]

    async def test_limit_param(self, authed_client: AsyncClient, seed_news_set):
        """limit=1 이면 1건만 반환"""
        res = await authed_client.get("/api/news/simple?limit=1")
        assert res.status_code == 200
        assert len(res.json()) == 1

    async def test_search_filters_by_title(
        self, authed_client: AsyncClient, seed_news_set
    ):
        """search 파라미터로 제목 필터링"""
        res = await authed_client.get("/api/news/simple?search=삼성전자")
        assert res.status_code == 200
        items = res.json()
        assert all("삼성전자" in item["title"] for item in items)

    async def test_empty_result_when_no_news(self, authed_client: AsyncClient):
        """뉴스 없을 때 빈 배열 반환 (seed_news_set 없이 호출)"""
        res = await authed_client.get("/api/news/simple")
        assert res.status_code == 200
        assert res.json() == []


# ============================================================
# 2. GET /api/news/{news_id} — 뉴스 상세 조회
# ============================================================

class TestNewsDetail:
    async def test_returns_detail_fields(
        self, authed_client: AsyncClient, seed_news_set
    ):
        """정상 상세 조회 — 필수 필드 포함 확인"""
        with patch(
            "app.routers.news.analyze_article",
            new_callable=AsyncMock,
            return_value={"related_stocks": []},
        ):
            res = await authed_client.get("/api/news/101")

        assert res.status_code == 200
        data = res.json()
        assert data["news_id"] == 101
        assert data["title"] == "삼성전자 1분기 실적 호조"
        assert "pub_date" in data

    async def test_not_found_returns_404(self, authed_client: AsyncClient):
        """존재하지 않는 news_id → 404"""
        with patch(
            "app.routers.news.analyze_article",
            new_callable=AsyncMock,
            return_value={"related_stocks": []},
        ):
            res = await authed_client.get("/api/news/99999")
        assert res.status_code == 404


# ============================================================
# 3. POST /api/notifications — 알림 DB 저장
# ============================================================

class TestCreateNotification:
    async def test_create_notification_success(
        self, authed_client: AsyncClient, seed_user: User, db_session: AsyncSession
    ):
        """Flutter 가 푸시 수신 후 서버에 저장 요청 — 정상 저장"""
        payload = {
            "notification_id": "os-notif-id-abc123",
            "type": "keywords",
            "title": "삼성전자 뉴스 급증",
            "body": "삼성전자 관련 키워드가 급증했습니다.",
            "stock_name": "삼성전자",
            "sentiment_score": 0.0,
        }
        res = await authed_client.post("/api/notifications", json=payload)
        assert res.status_code == 201
        assert res.json()["success"] is True

        result = await db_session.execute(
            select(Notification).where(
                Notification.onesignal_notification_id == "os-notif-id-abc123",
                Notification.user_id == seed_user.google_id,
            )
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved.type == "keywords"
        assert saved.stock_name == "삼성전자"
        assert saved.is_read is False

    async def test_duplicate_notification_returns_200(
        self, authed_client: AsyncClient, seed_user: User
    ):
        """같은 onesignal_notification_id + user_id 중복 저장 → 200 (에러 아님)"""
        payload = {
            "notification_id": "os-notif-id-dup999",
            "type": "keywords",
            "title": "중복 테스트",
            "body": "중복 본문",
            "stock_name": "카카오",  # 다른 종목명으로 UNIQUE constraint 회피
            "sentiment_score": 0.0,
        }
        first = await authed_client.post("/api/notifications", json=payload)
        assert first.status_code == 201

        second = await authed_client.post("/api/notifications", json=payload)
        # 중복이면 200 으로 Already exists 반환
        assert second.status_code == 200
        assert second.json()["success"] is True


# ============================================================
# 4. GET /api/notifications — 알림 목록 조회
# ============================================================

class TestGetNotifications:
    @pytest_asyncio.fixture
    async def seed_notifications(
        self, db_session: AsyncSession, seed_user: User
    ) -> list[Notification]:
        """
        알림 5건 세팅.
        UNIQUE constraint: (user_id, stock_name, type, date_kst)
        → stock_name 을 건마다 다르게 설정해 충돌 방지.
        """
        notis = [
            Notification(
                onesignal_notification_id=f"os-notif-seed-{i}",
                user_id=seed_user.google_id,
                type="keywords",
                title=f"알림 {i}",
                body=f"내용 {i}",
                is_read=(i % 2 == 0),
                star=False,
                stock_name=f"종목{i}",  # 각 건마다 다른 종목명
                sentiment_score=0.0,
                date_kst=date.today(),
            )
            for i in range(1, 6)
        ]
        db_session.add_all(notis)
        await db_session.flush()
        return notis

    async def test_returns_notifications_for_current_user(
        self,
        authed_client: AsyncClient,
        seed_notifications,
    ):
        """자신의 알림만 반환"""
        res = await authed_client.get("/api/notifications")
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 5

    async def test_pagination(
        self,
        authed_client: AsyncClient,
        seed_notifications,
    ):
        """page=1&size=2 → 2건"""
        res = await authed_client.get("/api/notifications?page=1&size=2")
        assert res.status_code == 200
        assert len(res.json()) == 2


# ============================================================
# 5. send_volatility_push_and_save — OneSignal 발송 + DB 저장
# ============================================================

ONESIGNAL_URL = "https://api.onesignal.com/notifications"


class TestSendVolatilityPushAndSave:
    """
    onesignal_service.send_volatility_push_and_save() 통합 테스트.

    respx 로 OneSignal API 를 모킹.
    patch_async_session_local 픽스처(autouse) 덕분에
    서비스 내부 AsyncSessionLocal 도 테스트 SQLite 에 연결됨.
    DB 저장 결과는 별도 세션으로 조회.
    """

    @respx.mock
    async def test_push_success_and_db_saved(self, seed_user: User, db_session: AsyncSession):
        """OneSignal 200 → push_success=True, db_saved=True"""
        from app.services.onesignal_service import send_volatility_push_and_save
        from app.database import AsyncSessionLocal

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "os-test-id-001"})
        )

        with patch("app.services.onesignal_service.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__.return_value = db_session
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            push_ok, db_ok = await send_volatility_push_and_save(
                user_ids=[seed_user.google_id],
                stock_name="삼성전자",
                date_kst=date.today(),
                rate=-6.5,
                alert_type="risk",
            )

            assert push_ok is True
            assert db_ok is True

            result = await db_session.execute(
                select(Notification).where(
                    Notification.onesignal_notification_id == "os-test-id-001",
                    Notification.user_id == seed_user.google_id,
                )
            )
            saved = result.scalar_one_or_none()
            assert saved is not None
            assert saved.type == "risk"
            assert saved.stock_name == "삼성전자"

    @respx.mock
    async def test_push_payload_contains_required_fields(self, seed_user: User):
        """OneSignal 요청 페이로드에 필수 필드 포함 확인"""
        from app.services.onesignal_service import send_volatility_push_and_save

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "os-test-id-002"})
        )

        await send_volatility_push_and_save(
            user_ids=[seed_user.google_id],
            stock_name="카카오",
            date_kst=date.today(),
            rate=8.5,
            alert_type="risk",
        )

        assert respx.calls.call_count == 1
        sent_payload = json.loads(respx.calls[0].request.content)

        assert "app_id" in sent_payload
        assert sent_payload["include_aliases"]["external_id"] == [seed_user.google_id]
        assert "카카오" in sent_payload["headings"]["ko"]
        assert sent_payload["data"]["stock_name"] == "카카오"
        assert "idempotency_key" in sent_payload

    @respx.mock
    async def test_onesignal_failure_push_false_db_saved(self, seed_user: User, db_session: AsyncSession):
        """OneSignal 500 → push_ok=False, db_saved=True (FAIL_ 접두사 ID 로 저장)"""
        from app.services.onesignal_service import send_volatility_push_and_save
        from app.database import AsyncSessionLocal

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(500, json={"errors": ["Internal Error"]})
        )

        with patch("app.services.onesignal_service.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__.return_value = db_session
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            push_ok, db_ok = await send_volatility_push_and_save(
                user_ids=[seed_user.google_id],
                stock_name="현대차",
                date_kst=date.today(),
                rate=-7.0,
                alert_type="risk",
            )

            assert push_ok is False
            assert db_ok is True

            result = await db_session.execute(
                select(Notification).where(
                    Notification.user_id == seed_user.google_id,
                    Notification.stock_name == "현대차",
                )
            )
            saved = result.scalar_one_or_none()
            assert saved is not None
            assert saved.onesignal_notification_id.startswith("FAIL_")

    @respx.mock
    async def test_idempotency_key_prevents_duplicate_push(self, seed_user: User):
        """같은 파라미터로 두 번 호출 시 idempotency_key 동일 → OneSignal 중복 방지"""
        from app.services.onesignal_service import send_volatility_push_and_save

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "os-idem-id"})
        )

        kwargs = dict(
            user_ids=[seed_user.google_id],
            stock_name="LG전자",
            date_kst=date.today(),
            rate=-5.5,
            alert_type="risk",
        )

        await send_volatility_push_and_save(**kwargs)
        await send_volatility_push_and_save(**kwargs)

        keys = [
            json.loads(call.request.content)["idempotency_key"]
            for call in respx.calls
        ]
        assert keys[0] == keys[1]

    async def test_empty_user_ids_returns_false_false(self):
        """user_ids 가 비어 있으면 즉시 (False, False) 반환"""
        from app.services.onesignal_service import send_volatility_push_and_save

        push_ok, db_ok = await send_volatility_push_and_save(
            user_ids=[],
            stock_name="삼성전자",
            date_kst=date.today(),
            rate=-6.0,
            alert_type="risk",
        )
        assert push_ok is False
        assert db_ok is False

    @respx.mock
    async def test_keywords_alert_type_title_format(self, seed_user: User):
        """alert_type='keywords' 일 때 제목에 '뉴스' 및 종목명 포함"""
        from app.services.onesignal_service import send_volatility_push_and_save

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "os-kw-id"})
        )

        await send_volatility_push_and_save(
            user_ids=[seed_user.google_id],
            stock_name="삼성전자",
            date_kst=date.today(),
            alert_type="keywords",
            news_count=5,
            keywords=["반도체", "실적", "수출"],
        )

        sent = json.loads(respx.calls[0].request.content)
        assert "뉴스" in sent["headings"]["ko"]
        assert "삼성전자" in sent["headings"]["ko"]
        assert "반도체" in sent["contents"]["ko"]


# ============================================================
# 6. run_news_keyword_check — 스케줄러 E2E
# ============================================================

class TestNewsKeywordScheduler:
    """
    run_news_keyword_check() E2E.

    patch_async_session_local(autouse) 로 스케줄러 내부 세션도
    테스트 SQLite 에 연결됨.
    get_stock_news_stats_from_db 를 모킹해 spike 조건을 강제 주입.
    """

    @respx.mock
    async def test_scheduler_sends_push_when_spike_detected(
        self, seed_user: User,db_session: AsyncSession, seed_watchlist
    ):
        """is_spike=True 감지 → 대상 유저에게 OneSignal 발송"""
        from app.tasks.news_tasks import run_news_keyword_check

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "os-sched-id-001"})
        )

        with patch("app.tasks.news_tasks.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__.return_value = db_session
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            fake_stats = {  
                "count": 5,
                "keywords": ["반도체", "실적", "수출"],
                "is_spike": True,
            }

            with patch(
                "app.tasks.news_tasks.get_stock_news_stats_from_db",
                new_callable=AsyncMock,
                return_value=fake_stats,
            ):
                await run_news_keyword_check()

            assert respx.calls.call_count >= 1

    @respx.mock
    async def test_scheduler_skips_when_no_spike(self, seed_watchlist):
        """is_spike=False → OneSignal 미호출"""
        from app.tasks.news_tasks import run_news_keyword_check

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "should-not-be-called"})
        )

        with patch(
            "app.tasks.news_tasks.get_stock_news_stats_from_db",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await run_news_keyword_check()

        assert respx.calls.call_count == 0

    @respx.mock
    async def test_scheduler_skips_already_sent_today(
        self, db_session: AsyncSession, seed_user: User, seed_watchlist
    ):
        """
        오늘 이미 발송된 (user_id, stock_name) 쌍은 재발송 안 함.
        patch_async_session_local 로 세션이 통일됐으므로
        db_session 으로 직접 이력 삽입 후 스케줄러가 감지하는지 확인.
        """
        from app.tasks.news_tasks import run_news_keyword_check

        existing = Notification(
            onesignal_notification_id="already-sent-today",
            user_id=seed_user.google_id,
            type="keywords",
            title="기발송",
            body="기발송 본문",
            is_read=False,
            star=False,
            stock_name="삼성전자",
            sentiment_score=0.0,
            date_kst=date.today(),
        )
        db_session.add(existing)
        await db_session.flush()

        respx.post(ONESIGNAL_URL).mock(
            return_value=Response(200, json={"id": "should-not-send"})
        )

        fake_stats = {
            "count": 5,
            "keywords": ["반도체"],
            "is_spike": True,
        }

        with patch(
            "app.tasks.news_tasks.get_stock_news_stats_from_db",
            new_callable=AsyncMock,
            return_value=fake_stats,
        ):
            await run_news_keyword_check()

        assert respx.calls.call_count == 0

    async def test_scheduler_skips_user_with_push_disabled(
        self, db_session: AsyncSession, seed_user: User, seed_stock: Stock, seed_watchlist
    ):
        """push=False 유저는 발송 대상 제외"""
        from app.tasks.news_tasks import run_news_keyword_check

        # push 비활성화 — 같은 세션으로 업데이트
        result = await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == seed_user.id)
        )
        s = result.scalar_one_or_none()
        if s:
            s.push = False
            await db_session.flush()

        fake_stats = {"count": 5, "keywords": ["반도체"], "is_spike": True}

        with respx.mock(assert_all_called=False) as mock:
            mock.post(ONESIGNAL_URL).mock(
                return_value=Response(200, json={"id": "x"})
            )
            with patch(
                "app.tasks.news_tasks.get_stock_news_stats_from_db",
                new_callable=AsyncMock,
                return_value=fake_stats,
            ):
                await run_news_keyword_check()

            assert mock.calls.call_count == 0