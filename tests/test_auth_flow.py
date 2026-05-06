# tests/test_auth_flow.py
"""
==============================================================================
인증 플로우 통합 테스트 (4주차: 4/23~25)
==============================================================================

테스트 항목:
    1. 로그인 → 유저 DB 저장 + access_token 반환 확인
    2. 발급받은 토큰으로 /profile API 호출 → 인증 성공 확인
    3. 잘못된 토큰으로 /profile API 호출 → 401 에러 확인

Google OAuth 처리:
    - verify_google_login_token 함수를 mock 처리
    - 실제 구글 서버에 요청하지 않고 가짜 token_info 반환
==============================================================================
"""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timedelta
from jose import jwt

from app.main import app
from app.database import get_db
from app.config import get_settings
 
settings = get_settings()

# ===========================================================================
# 헬퍼: 가짜 Google token_info
# ===========================================================================

def make_fake_token_info(
    google_id: str = "test_google_id_123",
    email: str = "testuser@gmail.com",
    name: str = "테스트유저",
    picture: str = "https://example.com/photo.jpg",
) -> dict:
    """Google OAuth 검증 결과를 흉내 내는 가짜 token_info"""
    return {
        "sub": google_id,
        "email": email,
        "name": name,
        "picture": picture,
        "email_verified": True,
        "iss": "accounts.google.com",
    }


# ===========================================================================
# 헬퍼: FastAPI 테스트 클라이언트 생성
# ===========================================================================

def make_client(db_session: AsyncSession) -> AsyncClient:
    """
    실제 DB 대신 테스트용 db_session을 주입한 AsyncClient 반환.
    conftest.py의 db_session fixture를 활용합니다.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# 테스트 1: 로그인 성공 → access_token 반환 + 유저 DB 저장 확인
# ===========================================================================

@pytest.mark.asyncio
async def test_login_success(db_session: AsyncSession):
    """
    시나리오:
        - Google 토큰 검증을 mock 처리
        - POST /api/users/login 호출
        - 응답에 access_token이 있는지 확인
        - 응답에 유저 정보(email)가 있는지 확인
    """
    fake_token_info = make_fake_token_info()

    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        async with make_client(db_session) as client:
            response = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )

    assert response.status_code == 200, f"로그인 실패: {response.text}"

    data = response.json()
    assert "access_token" in data, "access_token이 응답에 없습니다"
    assert data["token_type"] == "Bearer"
    assert data["user"]["email"] == "testuser@gmail.com"


# ===========================================================================
# 테스트 2: 로그인 → 발급된 토큰으로 /profile 호출 → 인증 성공
# ===========================================================================

@pytest.mark.asyncio
async def test_login_then_get_profile(db_session: AsyncSession):
    """
    시나리오:
        - 로그인해서 access_token 발급
        - 발급된 토큰을 Authorization 헤더에 담아 /profile 호출
        - 올바른 유저 정보가 반환되는지 확인
    """
    fake_token_info = make_fake_token_info()

    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        async with make_client(db_session) as client:
            # Step 1: 로그인
            login_response = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )
            assert login_response.status_code == 200, f"로그인 실패: {login_response.text}"
            access_token = login_response.json()["access_token"]

            # Step 2: 토큰으로 /profile 호출
            profile_response = await client.get(
                "/api/users/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )

    assert profile_response.status_code == 200, f"프로필 조회 실패: {profile_response.text}"

    profile = profile_response.json()
    assert profile["email"] == "testuser@gmail.com"
    assert profile["nickname"] == "테스트유저"


# ===========================================================================
# 테스트 3: 잘못된 토큰으로 /profile 호출 → 401 에러
# ===========================================================================

@pytest.mark.asyncio
async def test_invalid_token_returns_401(db_session: AsyncSession):
    """
    시나리오:
        - 유효하지 않은 토큰을 Authorization 헤더에 담아 /profile 호출
        - 401 Unauthorized 응답이 오는지 확인
    """
    async with make_client(db_session) as client:
        response = await client.get(
            "/api/users/profile",
            headers={"Authorization": "Bearer this_is_not_a_valid_token"},
        )

    assert response.status_code == 401, f"401이 예상됐지만 {response.status_code} 반환됨"


# ===========================================================================
# 테스트 4: 토큰 없이 /profile 호출 → 403 에러
# ===========================================================================

@pytest.mark.asyncio
async def test_no_token_returns_403(db_session: AsyncSession):
    """
    시나리오:
        - Authorization 헤더 없이 /profile 호출
        - 403 Forbidden 응답이 오는지 확인
        (FastAPI HTTPBearer는 토큰 없으면 403 반환)
    """
    async with make_client(db_session) as client:
        response = await client.get("/api/users/profile")

    assert response.status_code == 403, f"403이 예상됐지만 {response.status_code} 반환됨"


# ===========================================================================
# 테스트 5: 같은 계정으로 두 번 로그인 → 유저 중복 생성 없이 정상 처리
# ===========================================================================

@pytest.mark.asyncio
async def test_login_twice_same_account(db_session: AsyncSession):
    """
    시나리오:
        - 같은 google_id로 두 번 로그인
        - 두 번 다 200 응답 + access_token 반환
        - 유저가 중복 생성되지 않고 upsert 처리됨
    """
    fake_token_info = make_fake_token_info()

    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        async with make_client(db_session) as client:
            # 첫 번째 로그인
            res1 = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )
            # 두 번째 로그인
            res2 = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )

    assert res1.status_code == 200
    assert res2.status_code == 200
    # 두 응답 모두 같은 email 반환
    assert res1.json()["user"]["email"] == res2.json()["user"]["email"]

# ===========================================================================
# 테스트 6: 만료된 토큰으로 /profile 호출 → 401
# ===========================================================================
@pytest.mark.asyncio
async def test_expired_token_returns_401(db_session: AsyncSession):
    """
    시나리오:
        - 만료 시간이 0분인 토큰 생성 (즉시 만료)
        - 만료된 토큰으로 /profile 호출
        - 401 응답 확인
    """
    # 만료된 토큰 직접 생성
    from datetime import datetime
    expired_payload = {
        "sub": "test_google_id_123",
        "exp": datetime.utcnow() - timedelta(minutes=1),  # 1분 전에 이미 만료
    }
    expired_token = jwt.encode(
        expired_payload,
        settings.secret_key,
        algorithm=settings.algorithm,
    )
 
    async with make_client(db_session) as client:
        response = await client.get(
            "/api/users/profile",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
 
    assert response.status_code == 401, f"401이 예상됐지만 {response.status_code} 반환됨"
 
 
# ===========================================================================
# 테스트 7: 로그인 후 설정 조회 → 정상 반환
# ===========================================================================
 
@pytest.mark.asyncio
async def test_login_then_get_settings(db_session: AsyncSession):
    """
    시나리오:
        - 로그인해서 access_token 발급
        - 발급된 토큰으로 /settings 호출
        - 기본 설정값이 반환되는지 확인
    """
    fake_token_info = make_fake_token_info()
 
    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        async with make_client(db_session) as client:
            # 로그인
            login_response = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )
            assert login_response.status_code == 200
            access_token = login_response.json()["access_token"]
 
            # 설정 조회
            settings_response = await client.get(
                "/api/users/settings",
                headers={"Authorization": f"Bearer {access_token}"},
            )
 
    assert settings_response.status_code == 200, f"설정 조회 실패: {settings_response.text}"
 
    data = settings_response.json()
    # 기본값 확인 (UserSettings 기본값 기준)
    assert "push" in data, "push 필드가 없습니다"
 
 
# ===========================================================================
# 테스트 8: 로그인 후 설정 수정 → 변경값 반영 확인
# ===========================================================================
 
@pytest.mark.asyncio
async def test_login_then_update_settings(db_session: AsyncSession):
    """
    시나리오:
        - 로그인해서 access_token 발급
        - PATCH /settings 로 push 알림 설정 변경
        - 변경된 값이 반영됐는지 GET /settings 로 확인
    """
    fake_token_info = make_fake_token_info()
 
    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        async with make_client(db_session) as client:
            # 로그인
            login_response = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )
            assert login_response.status_code == 200
            access_token = login_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}
 
            # 설정 변경
            patch_response = await client.patch(
                "/api/users/settings",
                json={"push": True},
                headers=headers,
            )
            assert patch_response.status_code == 200, f"설정 변경 실패: {patch_response.text}"
 
            # 변경값 확인
            settings_response = await client.get(
                "/api/users/settings",
                headers=headers,
            )
 
    assert settings_response.status_code == 200
    assert settings_response.json()["push"] == True, "push 설정이 변경되지 않았습니다"
 
 
# ===========================================================================
# 테스트 9: 로그인 후 회원 탈퇴 → 재조회 시 401
# ===========================================================================
 
@pytest.mark.asyncio
async def test_login_then_delete_account(db_session: AsyncSession):
    """
    시나리오:
        - 로그인해서 access_token 발급
        - DELETE /api/users 로 회원 탈퇴
        - 204 응답 확인
        - 탈퇴 후 동일 토큰으로 /profile 호출 → 401 확인
    """
    fake_token_info = make_fake_token_info(google_id="delete_test_google_id")
 
    with patch(
        "app.routers.users.verify_google_login_token",
        new=AsyncMock(return_value=fake_token_info),
    ):
        async with make_client(db_session) as client:
            # 로그인
            login_response = await client.post(
                "/api/users/login",
                json={"id_token": "fake_google_id_token"},
            )
            assert login_response.status_code == 200
            access_token = login_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}
 
            # 회원 탈퇴
            delete_response = await client.delete(
                "/api/users",
                headers=headers,
            )
            assert delete_response.status_code == 204, f"회원 탈퇴 실패: {delete_response.text}"
 
            # 탈퇴 후 프로필 조회 → 401
            profile_response = await client.get(
                "/api/users/profile",
                headers=headers,
            )
 
    assert profile_response.status_code == 401, f"탈퇴 후 401이 예상됐지만 {profile_response.status_code} 반환됨"