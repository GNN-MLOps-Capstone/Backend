"""
==============================================================================
유저 API 라우터 (users.py)
==============================================================================

이 파일은 유저 관련 API 엔드포인트를 정의합니다.

테이블 구조:
    - users: 유저 데이터

API 엔드포인트:
    POST /api/users/login  -> 소설 로그인(신규 가입시 유저 정보 저장)
    GET /api/users/profile/{google_id}    -> 유저 정보 조회
    GET /api/users/settings/{google_id}    -> 설정 정보 조회
    PATCH /api/users/settings/{google_id}    -> 설정 정보 변경
    DELETE /api/users/{google_id}    -> 회원 탈퇴

==============================================================================
"""

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from google.oauth2 import id_token
from google.auth.transport import requests
from jose import jwt, JWTError

from app.database import get_db
from app.models import User, UserSettings
from app.schemas import (
    UserLoginRequest,
    DevLoginRequest,
    AuthResponse,
    UserUpdateRequest,
    UserResponse,
    SettingResponse,
)
from app.config import get_settings

router = APIRouter(
    prefix="/api/users",
    tags=["user"],
)

settings = get_settings()
security = HTTPBearer()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    
    # JWT 토큰 생성
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def decode_access_token(token: str) -> str:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="자격 증명을 확인할 수 없습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        google_id: str | None = payload.get("sub")
        if not google_id:
            raise credentials_exception
        return google_id
    except JWTError as exc:
        raise credentials_exception from exc


async def verify_google_login_token(login_token: str) -> dict:
    try:
        token_info = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            login_token,
            requests.Request(),
            settings.google_client_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 Google ID 토큰입니다.",
        ) from exc

    issuer = token_info.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="신뢰할 수 없는 Google 토큰 발급자입니다.",
        )
    if not token_info.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google 토큰에 사용자 식별자(sub)가 없습니다.",
        )
    if token_info.get("email_verified") is False:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google 이메일 인증이 확인되지 않았습니다.",
        )

    return token_info

async def get_current_user(
    token_obj: HTTPAuthorizationCredentials = Depends(security), 
    db: AsyncSession = Depends(get_db)
):
    token = token_obj.credentials
    google_id = decode_access_token(token)
    
    # 해독된 google_id를 가진 유저가 진짜 DB에 있는지 확인합니다.
    query = select(User).options(selectinload(User.settings)).where(User.google_id == google_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="자격 증명을 확인할 수 없습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    return user


async def _upsert_user_for_login(
    *,
    db: AsyncSession,
    google_id: str,
    email: str,
    nickname: str,
    img_url: str | None,
    onesignal_id: str | None,
) -> User:
    query = select(User).where(User.google_id == google_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            google_id=google_id,
            email=email,
            nickname=nickname,
            img_url=img_url,
            onesignal_id=onesignal_id,
        )
        user.settings = UserSettings()
        db.add(user)
        try:
            await db.commit()
            await db.refresh(user)
            return user
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(User).options(selectinload(User.settings)).where(User.google_id == google_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise

            user.email = email
            user.nickname = nickname
            user.img_url = img_url
            if onesignal_id is not None:
                user.onesignal_id = onesignal_id
            if user.settings is None:
                user.settings = UserSettings()

            await db.commit()
            await db.refresh(user)
            return user

    user.email = email
    user.nickname = nickname
    user.img_url = img_url
    if onesignal_id is not None:
        user.onesignal_id = onesignal_id

    await db.commit()
    await db.refresh(user)
    return user

# =============================================================================
# 신규 회원 API
# =============================================================================
#
# URL: POST /api/users/login
# 용도: 신규회원시 db에 유저 정보 저장
#
@router.post("/login", response_model=AuthResponse)
async def login(req: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    token_info = await verify_google_login_token(req.id_token)

    google_id = token_info.get("sub")
    email = token_info.get("email")
    nickname = token_info.get("name") or req.nickname
    img_url = token_info.get("picture") or req.img_url
    onesignal_id = req.onesignal_id

    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google 토큰에 이메일 정보가 없습니다.",
        )
    if not nickname:
        nickname = email.split("@")[0]

    user = await _upsert_user_for_login(
        db=db,
        google_id=google_id,
        email=email,
        nickname=nickname,
        img_url=img_url,
        onesignal_id=onesignal_id,
    )

    # Access Token 발급
    access_token = create_access_token(data={"sub": user.google_id})

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "user": user
    }


@router.post("/dev-login", response_model=AuthResponse, include_in_schema=False)
async def dev_login(req: DevLoginRequest, db: AsyncSession = Depends(get_db)):
    if not settings.debug or not settings.dev_bypass_login:
        raise HTTPException(status_code=404, detail="Not found")

    nickname = req.nickname or req.email.split("@")[0]
    user = await _upsert_user_for_login(
        db=db,
        google_id=req.google_id,
        email=req.email,
        nickname=nickname,
        img_url=req.img_url,
        onesignal_id=req.onesignal_id,
    )
    access_token = create_access_token(data={"sub": user.google_id})

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "user": user,
    }


@router.get("/google-login-config", include_in_schema=False)
async def get_google_login_config():
    client_id = settings.google_client_id.strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="GOOGLE_CLIENT_ID is not configured")
    return {"client_id": client_id}


# =============================================================================
# 유저 정보 조회 API
# =============================================================================
#
# URL: GET /api/users/profile
# 용도: Flutter 앱의 프로필 카드
#
@router.get("/profile", response_model=UserResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    return current_user

# =============================================================================
# 설정 정보 조회 API
# =============================================================================
#
# URL: GET /api/users/settings
# 용도: Flutter 앱의 설정
#
@router.get("/settings", response_model=SettingResponse)
async def get_user_settings(current_user: User = Depends(get_current_user)):
    return current_user.settings
    
# =============================================================================
# 설정 정보 변경 API
# =============================================================================
#
# URL: PATCH /api/users/settings
# 용도: Flutter 앱의 설정변경시 db에 저장
#
@router.patch("/settings", response_model=SettingResponse)
async def update_settings(
    request: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):

    update_data = request.model_dump(exclude_unset=True)

    settings_obj = current_user.settings

    # 현재 로그인한 유저(current_user) 정보를 수정
    for key, value in update_data.items():
        if hasattr(settings_obj, key):
            setattr(settings_obj, key, value)

    try:
        await db.commit()
        await db.refresh(settings_obj)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"저장 중 오류가 발생했습니다: {str(e)}")
    
    return settings_obj

# =============================================================================
# 회원 탈퇴 API
# =============================================================================
#
# URL: DELETE /api/users
# 용도: 회원 탈퇴 후 db에 유저 정보 삭제
#
@router.delete("", status_code=204)
async def delete_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await db.delete(current_user)
    await db.commit()
    return None
