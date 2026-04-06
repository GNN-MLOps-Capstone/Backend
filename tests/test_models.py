# tests/test_models.py
"""
4/3~4 — 유저 인증 및 관심종목 데이터 모델(DB/Schema) 검증
"""

import pytest
import pytest_asyncio
from datetime import time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import User, UserSettings, Watchlist, Stock


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