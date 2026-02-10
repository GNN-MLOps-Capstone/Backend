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

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from google.oauth2 import id_token
from google.auth.transport import requests
from jose import jwt, JWTError
from datetime import datetime, timedelta

from app.database import get_db
from app.models import User
from app.schemas import UserLoginRequest, AuthResponse, UserUpdateRequest, UserResponse, SettingResponse
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

async def get_current_user(
    token_obj: HTTPAuthorizationCredentials = Depends(security), 
    db: AsyncSession = Depends(get_db)
):
    # 인증 실패 시 뱉을 에러 미리 정의
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="자격 증명을 확인할 수 없습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 토큰 추출
    token = token_obj.credentials

    try:
        # 토큰 해독 (디코드)
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        google_id: str = payload.get("sub")
        
        if google_id is None:
            raise credentials_exception
            
    except JWTError:
        # 서명이 안 맞거나 유효기간이 지났을 때
        raise credentials_exception
    
    # 해독된 google_id를 가진 유저가 진짜 DB에 있는지 확인합니다.
    query = select(User).where(User.google_id == google_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user is None:
        # 토큰은 멀쩡한데 DB에 유저가 없는 경우 (탈퇴 등)
        raise credentials_exception
        
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

    google_id = req.google_id
    email = req.email
    nickname = req.nickname
    img_url = req.img_url

    # DB 조회 및 저장
    query = select(User).where(User.google_id == google_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        # 새로운 로그인
        user = User(
            google_id=google_id,
            email=email,
            nickname=nickname,         
            img_url=img_url
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    
    else:
        # 기존 회원
        user.nickname = nickname
        user.img_url = img_url
        await db.commit()
        await db.refresh(user)

    # Access Token 발급
    access_token = create_access_token(data={"sub": user.google_id})

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "user": user
    }


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
async def get_settings(current_user: User = Depends(get_current_user)):
    return current_user
    
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

    # update_data = request.dict(exclude_unset=True) (Pydantic v1)
    update_data = request.model_dump(exclude_unset=True) # (Pydantic v2)

    # 현재 로그인한 유저(current_user) 정보를 수정
    for key, value in update_data.items():
        setattr(current_user, key, value)

    await db.commit()
    await db.refresh(current_user)
    
    return current_user

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