# tests/test_schemas.py
"""
4/3~4 — 유저 인증 및 관심종목 Pydantic 스키마 검증
"""

import pytest
from datetime import datetime, time
from pydantic import ValidationError

from app.schemas import (
    UserLoginRequest,
    DevLoginRequest,
    UserResponse,
    UserUpdateRequest,
    AuthResponse,
    SettingResponse,
    WatchlistAddRequest,
    NotificationCreateRequest,
    NotificationResponse,
    NotificationReadRequest,
    NotificationCountResponse,
    StockSeriesQuery,
    StockNewsResponse,
    AITrendResponse,
    StockWeatherResponse,
    InteractionEventIn,
    InteractionEventType,
)

# =============================================================================
# UserLoginRequest 스키마 테스트
# =============================================================================

class TestUserLoginRequest:

    def test_정상_요청(self):
        data = UserLoginRequest(id_token="valid.google.token")
        assert data.id_token == "valid.google.token"
        assert data.nickname is None
        assert data.onesignal_id is None

    def test_전체_필드(self):
        data = UserLoginRequest(
            id_token="token",
            nickname="홍길동",
            img_url="https://example.com/photo.jpg",
            onesignal_id="onesignal-abc123",
        )
        assert data.nickname == "홍길동"
        assert data.onesignal_id == "onesignal-abc123"

    def test_id_token_필수(self):
        with pytest.raises(ValidationError):
            UserLoginRequest()

    def test_onesignal_id_최대길이(self):
        """255자 초과 시 ValidationError"""
        with pytest.raises(ValidationError):
            UserLoginRequest(id_token="token", onesignal_id="x" * 256)

    def test_onesignal_id_255자_허용(self):
        data = UserLoginRequest(id_token="token", onesignal_id="x" * 255)
        assert len(data.onesignal_id) == 255


# =============================================================================
# DevLoginRequest 스키마 테스트
# =============================================================================

class TestDevLoginRequest:

    def test_정상_요청(self):
        data = DevLoginRequest(google_id="dev-001", email="dev@test.com")
        assert data.google_id == "dev-001"

    def test_google_id_빈문자열_불가(self):
        with pytest.raises(ValidationError):
            DevLoginRequest(google_id="", email="dev@test.com")

    def test_google_id_최대길이_초과(self):
        with pytest.raises(ValidationError):
            DevLoginRequest(google_id="x" * 256, email="dev@test.com")

    def test_email_필수(self):
        with pytest.raises(ValidationError):
            DevLoginRequest(google_id="dev-001")

    def test_email_최소길이(self):
        """2자 이하 email은 불가"""
        with pytest.raises(ValidationError):
            DevLoginRequest(google_id="dev-001", email="ab")

    def test_optional_필드(self):
        data = DevLoginRequest(
            google_id="dev-001",
            email="dev@test.com",
            nickname="개발자",
            onesignal_id="signal-id",
        )
        assert data.nickname == "개발자"


# =============================================================================
# UserResponse 스키마 테스트
# =============================================================================

class TestUserResponse:

    def test_정상_응답(self):
        data = UserResponse(
            id=1,
            google_id="google-001",
            email="user@test.com",
            nickname="유저",
        )
        assert data.id == 1
        assert data.img_url is None

    def test_from_attributes_호환(self):
        """ORM 객체에서 변환 가능한지 확인 (from_attributes=True)"""
        class FakeUser:
            id = 10
            google_id = "google-orm-test"
            email = "orm@test.com"
            nickname = "ORM유저"
            img_url = None

        data = UserResponse.model_validate(FakeUser())
        assert data.id == 10
        assert data.google_id == "google-orm-test"

    def test_필수필드_누락(self):
        with pytest.raises(ValidationError):
            UserResponse(id=1, google_id="google-001")  # email, nickname 누락


# =============================================================================
# UserUpdateRequest 스키마 테스트
# =============================================================================

class TestUserUpdateRequest:

    def test_모두_optional(self):
        """모든 필드가 optional — 빈 요청도 유효"""
        data = UserUpdateRequest()
        assert data.push is None
        assert data.dnd_start is None

    def test_부분_업데이트(self):
        data = UserUpdateRequest(push=False, night_push_prohibit=True)
        assert data.push is False
        assert data.risk_only is None  # 나머지는 None

    def test_dnd_시간_파싱(self):
        data = UserUpdateRequest(dnd_start=time(22, 30), dnd_finish=time(6, 0))
        assert data.dnd_start == time(22, 30)
        assert data.dnd_finish == time(6, 0)

    def test_잘못된_bool_타입(self):
        with pytest.raises(ValidationError):
            UserUpdateRequest(push="not-a-bool-string-invalid")


# =============================================================================
# SettingResponse 스키마 테스트
# =============================================================================

class TestSettingResponse:

    def test_정상_응답(self):
        data = SettingResponse(
            push=True,
            risk_only=True,
            positive_only=False,
            interest_only=False,
            night_push_prohibit=False,
            dnd_start=time(23, 0),
            dnd_finish=time(7, 0),
        )
        assert data.push is True
        assert data.dnd_start == time(23, 0)

    def test_from_attributes_호환(self):
        class FakeSettings:
            push = True
            risk_only = False
            positive_only = True
            interest_only = False
            night_push_prohibit = True
            dnd_start = time(22, 0)
            dnd_finish = time(6, 0)

        data = SettingResponse.model_validate(FakeSettings())
        assert data.risk_only is False
        assert data.dnd_finish == time(6, 0)


# =============================================================================
# WatchlistAddRequest 스키마 테스트
# =============================================================================

class TestWatchlistAddRequest:

    def test_정상_요청(self):
        data = WatchlistAddRequest(code="005930")
        assert data.code == "005930"

    def test_code_필수(self):
        with pytest.raises(ValidationError):
            WatchlistAddRequest()

    def test_다양한_종목코드(self):
        for code in ["000660", "035420", "207940"]:
            data = WatchlistAddRequest(code=code)
            assert data.code == code


# =============================================================================
# NotificationCreateRequest 스키마 테스트
# =============================================================================
 
class TestNotificationCreateRequest:
 
    def test_정상_요청(self):
        data = NotificationCreateRequest(
            notification_id="notif-001",
            type="RISK",
            title="급락 경보",
        )
        assert data.notification_id == "notif-001"
        assert data.body is None
        assert data.sentiment_score is None
 
    def test_전체_필드(self):
        data = NotificationCreateRequest(
            notification_id="notif-002",
            type="NEWS",
            title="삼성전자 뉴스",
            body="삼성전자가 어닝서프라이즈를 기록했습니다.",
            stock_name="삼성전자",
            sentiment_score=0.85,
        )
        assert data.stock_name == "삼성전자"
        assert data.sentiment_score == 0.85
 
    def test_notification_id_필수(self):
        with pytest.raises(ValidationError):
            NotificationCreateRequest(type="RISK", title="제목")
 
    def test_notification_id_빈문자열_불가(self):
        with pytest.raises(ValidationError):
            NotificationCreateRequest(notification_id="", type="RISK", title="제목")
 
    def test_notification_id_최대길이_초과(self):
        with pytest.raises(ValidationError):
            NotificationCreateRequest(notification_id="x" * 256, type="RISK", title="제목")
 
    def test_notification_id_255자_허용(self):
        data = NotificationCreateRequest(
            notification_id="x" * 255,
            type="RISK",
            title="제목",
        )
        assert len(data.notification_id) == 255
 
    def test_type_필수(self):
        with pytest.raises(ValidationError):
            NotificationCreateRequest(notification_id="notif-001", title="제목")
 
    def test_title_필수(self):
        with pytest.raises(ValidationError):
            NotificationCreateRequest(notification_id="notif-001", type="RISK")
 
 
# =============================================================================
# NotificationResponse 스키마 테스트
# =============================================================================
 
class TestNotificationResponse:
 
    def test_정상_응답(self):
        data = NotificationResponse(
            id=1,
            type="RISK",
            title="급락 경보",
            read=False,
            created_at=datetime(2026, 4, 22, 10, 0, 0),
        )
        assert data.id == 1
        assert data.read is False
        assert data.star is False  # 기본값 확인
 
    def test_star_기본값_False(self):
        data = NotificationResponse(
            id=2,
            type="NEWS",
            title="뉴스 알림",
            read=True,
            created_at=datetime(2026, 4, 22),
        )
        assert data.star is False
 
    def test_전체_필드(self):
        data = NotificationResponse(
            id=3,
            type="NEWS",
            title="삼성전자 호재",
            body="삼성전자 관련 긍정 뉴스입니다.",
            read=True,
            star=True,
            stock_name="삼성전자",
            sentiment_score=0.9,
            created_at=datetime(2026, 4, 22, 9, 30),
        )
        assert data.star is True
        assert data.sentiment_score == 0.9
 
    def test_from_attributes_호환(self):
        class FakeNotif:
            id = 10
            type = "RISK"
            title = "ORM 알림"
            body = None
            read = False
            star = False
            stock_name = None
            sentiment_score = None
            created_at = datetime(2026, 4, 22)
 
        data = NotificationResponse.model_validate(FakeNotif())
        assert data.id == 10
        assert data.read is False
 
    def test_id_필수(self):
        with pytest.raises(ValidationError):
            NotificationResponse(
                type="RISK",
                title="제목",
                read=False,
                created_at=datetime(2026, 4, 22),
            )
 
    def test_created_at_필수(self):
        with pytest.raises(ValidationError):
            NotificationResponse(id=1, type="RISK", title="제목", read=False)
 
 
# =============================================================================
# NotificationReadRequest 스키마 테스트
# =============================================================================
 
class TestNotificationReadRequest:
 
    def test_id_없으면_전체_읽음처리(self):
        """id가 None이면 전체 읽음 처리"""
        data = NotificationReadRequest()
        assert data.id is None
 
    def test_id_있으면_특정_알림_읽음처리(self):
        data = NotificationReadRequest(id=42)
        assert data.id == 42
 
    def test_id_명시적_None(self):
        data = NotificationReadRequest(id=None)
        assert data.id is None
 
 
# =============================================================================
# NotificationCountResponse 스키마 테스트
# =============================================================================
 
class TestNotificationCountResponse:
 
    def test_정상_응답(self):
        data = NotificationCountResponse(unread_count=5)
        assert data.unread_count == 5
 
    def test_0_반환(self):
        data = NotificationCountResponse(unread_count=0)
        assert data.unread_count == 0
 
    def test_unread_count_필수(self):
        with pytest.raises(ValidationError):
            NotificationCountResponse()
 
 
# =============================================================================
# StockSeriesQuery 스키마 테스트
# =============================================================================
 
class TestStockSeriesQuery:
 
    def test_정상_요청(self):
        data = StockSeriesQuery(range="1D")
        assert data.range == "1D"
        assert data.from_date is None
        assert data.to_date is None
 
    def test_날짜_포함_정상_요청(self):
        data = StockSeriesQuery(range="1M", from_date="20260101", to_date="20260422")
        assert data.from_date == "20260101"
        assert data.to_date == "20260422"
 
    def test_날짜_형식_오류_문자포함(self):
        with pytest.raises(ValidationError):
            StockSeriesQuery(range="1M", from_date="2026-01-01")
 
    def test_날짜_형식_오류_6자리(self):
        with pytest.raises(ValidationError):
            StockSeriesQuery(range="1M", from_date="202601")
 
    def test_날짜_형식_오류_존재하지않는_날짜(self):
        with pytest.raises(ValidationError):
            StockSeriesQuery(range="1M", from_date="20261340")  # 13월 40일
 
    def test_from_date만_있어도_유효(self):
        data = StockSeriesQuery(range="3M", from_date="20260101")
        assert data.from_date == "20260101"
        assert data.to_date is None
 
    def test_to_date만_있어도_유효(self):
        data = StockSeriesQuery(range="3M", to_date="20260422")
        assert data.to_date == "20260422"
 
 
# =============================================================================
# StockNewsResponse 스키마 테스트
# =============================================================================
 
class TestStockNewsResponse:
 
    def test_정상_응답(self):
        data = StockNewsResponse(title="삼성전자 호실적", source="(2026.04.10, 한국경제)")
        assert data.title == "삼성전자 호실적"
        assert data.isUp is None
        assert data.sentiment is None
 
    def test_전체_필드(self):
        data = StockNewsResponse(
            isUp=True,
            title="SK하이닉스 반등",
            source="(2026.04.15, 매일경제)",
            sentiment="긍정",
        )
        assert data.isUp is True
        assert data.sentiment == "긍정"
 
    def test_isUp_False(self):
        data = StockNewsResponse(
            isUp=False,
            title="NAVER 하락세",
            source="(2026.04.20, 연합뉴스)",
        )
        assert data.isUp is False
 
    def test_title_필수(self):
        with pytest.raises(ValidationError):
            StockNewsResponse(source="(2026.04.10, 한경)")
 
    def test_source_필수(self):
        with pytest.raises(ValidationError):
            StockNewsResponse(title="뉴스 제목")
 
 
# =============================================================================
# AITrendResponse 스키마 테스트
# =============================================================================
 
class TestAITrendResponse:
 
    def test_정상_응답(self):
        data = AITrendResponse(
            rank=1,
            code="005930",
            name="삼성전자",
            weather="SUNNY",
            score=95,
            news_count=10,
            avg_sentiment=0.8,
        )
        assert data.rank == 1
        assert data.weather == "SUNNY"
        assert data.last_price is None
 
    def test_전체_필드(self):
        data = AITrendResponse(
            rank=2,
            code="000660",
            name="SK하이닉스",
            weather="PARTLY_CLOUDY",
            score=70,
            last_price=180000,
            change_rate=1.23,
            news_count=7,
            avg_sentiment=0.4,
        )
        assert data.last_price == 180000
        assert data.change_rate == 1.23
 
    def test_weather_유효한_enum값(self):
        for weather in ["THUNDERSTORM", "RAINY", "CLOUDY", "PARTLY_CLOUDY", "SUNNY"]:
            data = AITrendResponse(
                rank=1, code="005930", name="삼성전자",
                weather=weather, score=50, news_count=3, avg_sentiment=0.0,
            )
            assert data.weather == weather
 
    def test_weather_유효하지않은_값(self):
        with pytest.raises(ValidationError):
            AITrendResponse(
                rank=1, code="005930", name="삼성전자",
                weather="WINDY", score=50, news_count=3, avg_sentiment=0.0,
            )
 
    def test_필수_필드_누락(self):
        with pytest.raises(ValidationError):
            AITrendResponse(rank=1, code="005930")  # name, weather 등 누락
 
 
# =============================================================================
# StockWeatherResponse 스키마 테스트
# =============================================================================
 
class TestStockWeatherResponse:
 
    def test_정상_응답(self):
        data = StockWeatherResponse(weather="CLOUDY")
        assert data.weather == "CLOUDY"
 
    def test_모든_weather_enum값(self):
        for weather in ["THUNDERSTORM", "RAINY", "CLOUDY", "PARTLY_CLOUDY", "SUNNY"]:
            data = StockWeatherResponse(weather=weather)
            assert data.weather == weather
 
    def test_유효하지않은_weather(self):
        with pytest.raises(ValidationError):
            StockWeatherResponse(weather="FOG")
 
    def test_weather_필수(self):
        with pytest.raises(ValidationError):
            StockWeatherResponse()
 
 
# =============================================================================
# InteractionEventIn 스키마 테스트
# =============================================================================
 
class TestInteractionEventIn:
 
    def _base(self, **kwargs):
        defaults = dict(
            event_id="evt-001",
            user_id=1,
            event_type=InteractionEventType.screen_view,
        )
        return {**defaults, **kwargs}
 
    def test_정상_요청(self):
        data = InteractionEventIn(**self._base())
        assert data.event_id == "evt-001"
        assert data.user_id == 1
        assert data.news_id is None
 
    def test_전체_필드(self):
        data = InteractionEventIn(**self._base(
            device_id="device-abc",
            app_session_id="app-session-xyz",
            screen_session_id="screen-001",
            content_session_id="content-001",
            news_id=100,
            request_id="req-abc123",
            position=3,
            page=1,
            scroll_depth=0.75,
            event_ts_client=datetime(2026, 4, 22, 10, 0, 0),
        ))
        assert data.news_id == 100
        assert data.scroll_depth == 0.75
 
    def test_event_id_빈문자열_불가(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(**self._base(event_id=""))
 
    def test_event_id_최대길이_초과(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(**self._base(event_id="x" * 65))
 
    def test_user_id_0이하_불가(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(**self._base(user_id=0))
 
    def test_user_id_음수_불가(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(**self._base(user_id=-1))
 
    def test_device_id_최대길이_초과(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(**self._base(device_id="x" * 256))
 
    def test_request_id_최대길이_초과(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(**self._base(request_id="x" * 129))
 
    def test_event_type_필수(self):
        with pytest.raises(ValidationError):
            InteractionEventIn(event_id="evt-001", user_id=1)