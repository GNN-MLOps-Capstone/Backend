# tests/test_models.py
"""
4/3~4 — 유저 인증 및 관심종목 데이터 모델(DB/Schema) 검증
"""

import pytest
import pytest_asyncio
from datetime import date, datetime, time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import User, UserSettings, Watchlist, Stock, Notification, InteractionEvent, InteractionEventType


# =============================================================================
# 공통 픽스처
# =============================================================================

@pytest_asyncio.fixture
async def sample_user(db_session):
    """테스트용 기본 유저"""
    user = User(
        google_id="google-test-001",
        email="test@example.com",
        nickname="테스터",
        img_url="https://example.com/img.jpg",
    )
    db_session.add(user)
    await db_session.flush()  # id 자동 발급
    return user


@pytest_asyncio.fixture
async def sample_stock(db_session):
    """테스트용 종목"""
    stock = Stock(
        stock_id="005930",
        stock_name="삼성전자",
        industry="반도체",
        summary_text="세계적인 반도체 기업",
    )
    db_session.add(stock)
    await db_session.flush()
    return stock


# =============================================================================
# User 모델 테스트
# =============================================================================

class TestUserModel:

    async def test_유저_생성_성공(self, db_session, sample_user):
        """기본 유저 생성 및 DB 저장 확인"""
        result = await db_session.execute(
            select(User).where(User.google_id == "google-test-001")
        )
        user = result.scalar_one_or_none()

        assert user is not None
        assert user.email == "test@example.com"
        assert user.nickname == "테스터"
        assert user.id is not None  # PK 자동 발급 확인

    async def test_유저_created_at_자동설정(self, db_session, sample_user):
        """created_at 서버 기본값 확인"""
        assert sample_user.created_at is not None

    async def test_유저_last_login_자동설정(self, db_session, sample_user):
        """last_login 서버 기본값 확인 — 가입 시 자동으로 현재 시각이 입력됨"""
        result = await db_session.execute(
            select(User).where(User.id == sample_user.id)
        )
        user = result.scalar_one()
        assert user.last_login is not None
 
    async def test_유저_last_login_갱신(self, db_session, sample_user):
        """last_login 직접 갱신 후 반영 확인"""
        new_login_time = datetime(2026, 4, 22, 9, 0, 0)
        sample_user.last_login = new_login_time
        await db_session.flush()
 
        result = await db_session.execute(
            select(User).where(User.id == sample_user.id)
        )
        user = result.scalar_one()
        assert user.last_login.replace(tzinfo=None) == new_login_time
        
    async def test_유저_role_nullable(self, db_session):
        """role 컬럼은 null 허용"""
        user = User(
            google_id="google-role-test",
            email="role@test.com",
            nickname="역할없음",
        )
        db_session.add(user)
        await db_session.flush()
        assert user.role is None
 
    async def test_유저_role_저장(self, db_session):
        """role 값이 정상 저장되는지 확인"""
        user = User(
            google_id="google-admin-001",
            email="admin@test.com",
            nickname="관리자",
            role="admin",
        )
        db_session.add(user)
        await db_session.flush()
        assert user.role == "admin"

    async def test_google_id_unique_제약(self, db_session, sample_user):
        """같은 google_id로 유저 중복 생성 시 IntegrityError"""
        duplicate = User(
            google_id="google-test-001",  # 동일한 google_id
            email="other@example.com",
            nickname="다른유저",
        )
        db_session.add(duplicate)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_google_id_필수(self, db_session):
        """google_id 없으면 IntegrityError"""
        user = User(email="no-google@example.com", nickname="구글없음")
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_email_필수(self, db_session):
        """email 없으면 IntegrityError"""
        user = User(google_id="google-no-email-001")
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_onesignal_id_nullable(self, db_session):
        """onesignal_id는 null 허용"""
        user = User(
            google_id="google-test-002",
            email="test2@example.com",
            onesignal_id=None,
        )
        db_session.add(user)
        await db_session.flush()
        assert user.onesignal_id is None

    async def test_repr(self, sample_user):
        """__repr__ 형식 확인"""
        assert "test@example.com" in repr(sample_user)


# =============================================================================
# UserSettings 모델 테스트
# =============================================================================

class TestUserSettingsModel:

    @pytest_asyncio.fixture
    async def sample_settings(self, db_session, sample_user):
        settings = UserSettings(user_id=sample_user.id)
        db_session.add(settings)
        await db_session.flush()
        return settings

    async def test_기본값_확인(self, db_session, sample_settings):
        """push, risk_only 등 기본값 검증"""
        assert sample_settings.push is True
        assert sample_settings.risk_only is True
        assert sample_settings.positive_only is False
        assert sample_settings.interest_only is False
        assert sample_settings.night_push_prohibit is False

    async def test_dnd_시간_기본값(self, db_session, sample_settings):
        """야간 방해금지 시간 기본값"""
        assert sample_settings.dnd_start == time(23, 0, 0)
        assert sample_settings.dnd_finish == time(7, 0, 0)

    async def test_설정값_변경(self, db_session, sample_settings):
        """설정값 업데이트 반영 확인"""
        sample_settings.push = False
        sample_settings.night_push_prohibit = True
        await db_session.flush()

        result = await db_session.execute(
            select(UserSettings).where(UserSettings.user_id == sample_settings.user_id)
        )
        updated = result.scalar_one()
        assert updated.push is False
        assert updated.night_push_prohibit is True

    async def test_user_id_unique_제약(self, db_session, sample_user):
        """한 유저에게 설정 2개 생성 시 IntegrityError"""
        s1 = UserSettings(user_id=sample_user.id)
        s2 = UserSettings(user_id=sample_user.id)
        db_session.add(s1)
        db_session.add(s2)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# =============================================================================
# Watchlist 모델 테스트
# =============================================================================

class TestWatchlistModel:

    async def test_관심종목_추가(self, db_session, sample_user, sample_stock):
        """관심종목 정상 추가"""
        item = Watchlist(user_id=sample_user.id, stock_id=sample_stock.stock_id)
        db_session.add(item)
        await db_session.flush()

        result = await db_session.execute(
            select(Watchlist).where(Watchlist.user_id == sample_user.id)
        )
        watchlist = result.scalars().all()
        assert len(watchlist) == 1
        assert watchlist[0].stock_id == "005930"

    async def test_관심종목_중복_제약(self, db_session, sample_user, sample_stock):
        """같은 유저+종목 중복 추가 시 IntegrityError (uq_watchlist_user_stock)"""
        item1 = Watchlist(user_id=sample_user.id, stock_id=sample_stock.stock_id)
        item2 = Watchlist(user_id=sample_user.id, stock_id=sample_stock.stock_id)
        db_session.add(item1)
        db_session.add(item2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_관심종목_created_at_자동설정(self, db_session, sample_user, sample_stock):
        """created_at 자동 설정 확인"""
        item = Watchlist(user_id=sample_user.id, stock_id=sample_stock.stock_id)
        db_session.add(item)
        await db_session.flush()
        assert item.created_at is not None

    async def test_여러_종목_추가(self, db_session, sample_user):
        """한 유저가 여러 종목 추가 가능"""
        stocks = [
            Stock(stock_id="000660", stock_name="SK하이닉스"),
            Stock(stock_id="035420", stock_name="NAVER"),
        ]
        for s in stocks:
            db_session.add(s)
        await db_session.flush()

        for s in stocks:
            db_session.add(Watchlist(user_id=sample_user.id, stock_id=s.stock_id))
        await db_session.flush()

        result = await db_session.execute(
            select(Watchlist).where(Watchlist.user_id == sample_user.id)
        )
        assert len(result.scalars().all()) == 2

    async def test_repr(self, db_session, sample_user, sample_stock):
        item = Watchlist(user_id=sample_user.id, stock_id=sample_stock.stock_id)
        db_session.add(item)
        await db_session.flush()
        assert "005930" in repr(item)

    async def test_market_cap_nullable(self, db_session):
        """market_cap은 null 허용"""
        stock = Stock(stock_id="888888", stock_name="테스트종목")
        db_session.add(stock)
        await db_session.flush()
        assert stock.market_cap is None
 
    async def test_market_cap_저장(self, db_session):
        """market_cap 값이 정상 저장되는지 확인"""
        stock = Stock(
            stock_id="005380",
            stock_name="현대차",
            market_cap=40_000_000_000_000,
        )
        db_session.add(stock)
        await db_session.flush()
        assert stock.market_cap == 40_000_000_000_000
 
    async def test_repr(self, db_session, sample_stock):
        assert "005930" in repr(sample_stock)
        assert "삼성전자" in repr(sample_stock)


# =============================================================================
# Stock 모델 테스트
# =============================================================================

class TestStockModel:

    async def test_종목_생성(self, db_session, sample_stock):
        result = await db_session.execute(
            select(Stock).where(Stock.stock_id == "005930")
        )
        stock = result.scalar_one_or_none()
        assert stock is not None
        assert stock.stock_name == "삼성전자"

    async def test_필드_nullable(self, db_session):
        """industry, summary_text는 null 허용"""
        stock = Stock(stock_id="999999")
        db_session.add(stock)
        await db_session.flush()
        assert stock.stock_name is None
        assert stock.industry is None


# =============================================================================
# Notification 모델 테스트
# =============================================================================
 
class TestNotificationModel:
 
    @pytest_asyncio.fixture
    async def sample_notification(self, db_session, sample_user):
        notif = Notification(
            onesignal_notification_id="onesignal-notif-001",
            user_id=sample_user.google_id,
            type="RISK",
            title="급락 경보",
            date_kst=date(2026, 4, 22),
        )
        db_session.add(notif)
        await db_session.flush()
        return notif
 
    async def test_알림_생성_성공(self, db_session, sample_notification):
        """기본 알림 생성 및 DB 저장 확인"""
        result = await db_session.execute(
            select(Notification).where(Notification.id == sample_notification.id)
        )
        notif = result.scalar_one_or_none()
        assert notif is not None
        assert notif.type == "RISK"
        assert notif.title == "급락 경보"
 
    async def test_is_read_기본값_False(self, db_session, sample_notification):
        """is_read 기본값이 False인지 확인"""
        assert sample_notification.is_read is False
 
    async def test_star_기본값_False(self, db_session, sample_notification):
        """star 기본값이 False인지 확인"""
        assert sample_notification.star is False
 
    async def test_created_at_자동설정(self, db_session, sample_notification):
        """created_at 자동 설정 확인"""
        assert sample_notification.created_at is not None
 
    async def test_is_read_갱신(self, db_session, sample_notification):
        """읽음 처리 후 is_read 반영 확인"""
        sample_notification.is_read = True
        await db_session.flush()
 
        result = await db_session.execute(
            select(Notification).where(Notification.id == sample_notification.id)
        )
        notif = result.scalar_one()
        assert notif.is_read is True
 
    async def test_전체_필드_저장(self, db_session, sample_user):
        """선택 필드 포함 전체 저장 확인"""
        notif = Notification(
            onesignal_notification_id="onesignal-notif-full",
            user_id=sample_user.google_id,
            type="NEWS",
            title="삼성전자 호재",
            body="삼성전자가 어닝서프라이즈를 기록했습니다.",
            stock_name="삼성전자",
            sentiment_score=0.85,
            date_kst=date(2026, 4, 22),
        )
        db_session.add(notif)
        await db_session.flush()
        assert notif.stock_name == "삼성전자"
        assert notif.sentiment_score == 0.85
        assert notif.body is not None
 
    async def test_onesignal_user_unique_제약(self, db_session, sample_user):
        """동일한 onesignal_notification_id + user_id 조합은 중복 불가"""
        n1 = Notification(
            onesignal_notification_id="onesignal-dup",
            user_id=sample_user.google_id,
            type="RISK",
            title="알림1",
            date_kst=date(2026, 4, 22),
        )
        n2 = Notification(
            onesignal_notification_id="onesignal-dup",  # 동일
            user_id=sample_user.google_id,
            type="RISK",
            title="알림2",
            date_kst=date(2026, 4, 22),
        )
        db_session.add(n1)
        db_session.add(n2)
        with pytest.raises(IntegrityError):
            await db_session.flush()
 
    async def test_daily_unique_제약(self, db_session, sample_user):
        """user_id + stock_name + type + date_kst 조합 중복 불가"""
        n1 = Notification(
            onesignal_notification_id="onesignal-daily-001",
            user_id=sample_user.google_id,
            type="RISK",
            title="급락 경보",
            stock_name="삼성전자",
            date_kst=date(2026, 4, 22),
        )
        n2 = Notification(
            onesignal_notification_id="onesignal-daily-002",
            user_id=sample_user.google_id,
            type="RISK",
            title="급락 경보 2",
            stock_name="삼성전자",  # 동일 stock_name + type + date_kst
            date_kst=date(2026, 4, 22),
        )
        db_session.add(n1)
        db_session.add(n2)
        with pytest.raises(IntegrityError):
            await db_session.flush()
 
    async def test_repr(self, db_session, sample_notification):
        assert "RISK" in repr(sample_notification)
        assert "급락 경보" in repr(sample_notification)
 
 
# =============================================================================
# InteractionEvent 모델 테스트
# =============================================================================
 
class TestInteractionEventModel:
 
    async def test_이벤트_생성_성공(self, db_session, sample_user):
        """기본 이벤트 생성 및 DB 저장 확인"""
        event = InteractionEvent(
            id=1,
            event_id="evt-001",
            user_id=sample_user.id,
            event_type=InteractionEventType.content_open,
        )
        db_session.add(event)
        await db_session.flush()
 
        result = await db_session.execute(
            select(InteractionEvent).where(InteractionEvent.event_id == "evt-001")
        )
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved.event_type == InteractionEventType.content_open
 
    async def test_event_ts_server_자동설정(self, db_session, sample_user):
        """event_ts_server 서버 기본값 자동 설정 확인"""
        event = InteractionEvent(
            id=2,
            event_id="evt-002",
            user_id=sample_user.id,
            event_type=InteractionEventType.screen_view,
        )
        db_session.add(event)
        await db_session.flush()
        assert event.event_ts_server is not None
 
    async def test_event_id_unique_제약(self, db_session, sample_user):
        """동일한 event_id 중복 저장 시 IntegrityError"""
        e1 = InteractionEvent(
            id=3,
            event_id="evt-dup",
            user_id=sample_user.id,
            event_type=InteractionEventType.scroll_depth,
        )
        e2 = InteractionEvent(
            id=4,
            event_id="evt-dup",  # 동일 event_id
            user_id=sample_user.id,
            event_type=InteractionEventType.scroll_depth,
        )
        db_session.add(e1)
        db_session.add(e2)
        with pytest.raises(IntegrityError):
            await db_session.flush()
 
    async def test_선택_필드_저장(self, db_session, sample_user):
        """선택 필드 포함 전체 저장 확인"""
        event = InteractionEvent(
            id=5,
            event_id="evt-full-001",
            user_id=sample_user.id,
            event_type=InteractionEventType.content_heartbeat,
            device_id="device-abc",
            app_session_id="app-session-xyz",
            screen_session_id="screen-001",
            content_session_id="content-001",
            news_id=12345,
            request_id="req-abc",
            position=2,
            page=1,
            scroll_depth=0.75,
            event_ts_client=datetime(2026, 4, 22, 10, 0, 0),
        )
        db_session.add(event)
        await db_session.flush()
        assert event.news_id == 12345
        assert event.scroll_depth == 0.75
        assert event.position == 2
 
    async def test_모든_event_type_저장(self, db_session, sample_user):
        """InteractionEventType 전체 값 저장 가능 확인"""
        for i, event_type in enumerate(InteractionEventType):
            event = InteractionEvent(
                id=100+i,
                event_id=f"evt-type-{i:03d}",
                user_id=sample_user.id,
                event_type=event_type,
            )
            db_session.add(event)
        await db_session.flush()
 
        result = await db_session.execute(
            select(InteractionEvent).where(
                InteractionEvent.event_id.like("evt-type-%")
            )
        )
        saved = result.scalars().all()
        assert len(saved) == len(list(InteractionEventType))
 
    async def test_optional_필드_null_허용(self, db_session, sample_user):
        """선택 필드는 모두 null 저장 가능"""
        event = InteractionEvent(
            id=6,
            event_id="evt-null-fields",
            user_id=sample_user.id,
            event_type=InteractionEventType.screen_leave,
        )
        db_session.add(event)
        await db_session.flush()
        assert event.news_id is None
        assert event.scroll_depth is None
        assert event.device_id is None
        assert event.event_ts_client is None