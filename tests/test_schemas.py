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