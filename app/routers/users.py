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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import joinedload
from typing import Optional

from app.database import get_db
from app.models import User
from app.schemas import UserLoginRequest, UserUpdateRequest, UserResponse, SettingResponse

router = APIRouter(
    prefix="/api/users",
    tags=["user"],
)

# =============================================================================
# 신규 회원 API
# =============================================================================
#
# URL: POST /api/users/login
# 용도: 신규회원시 db에 유저 정보 저장
#
@router.post("/login", response_model=UserResponse)
async def login(request: UserLoginRequest, db: AsyncSession = Depends(get_db)):

    query = select(User).where(User.google_id == request.google_id)

    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        # 신규 회원인 경우에 db에 추가
        new_user = User(
            google_id=request.google_id,
            email=request.email,
            nickname=request.nickname,         
            profile_image=request.profile_image
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user
    
    else:
        # 기존 회원의 경우 닉네임이나, 프로필 사진이 바뀌었을 경우에 교체
        if request.nickname:
            user.nickname = request.nickname
        if request.profile_image:
            user.profile_image = request.profile_image
            
        await db.commit()
        await db.refresh(user)
        return user

# =============================================================================
# 유저 정보 조회 API
# =============================================================================
#
# URL: GET /api/users/profile/{google_id}
# 용도: Flutter 앱의 프로필 카드
#
@router.get("/profile/{google_id}", response_model=UserResponse)
async def get_profile(google_id: str, db: AsyncSession = Depends(get_db)):

    query = select(
        User.google_id, 
        User.email, 
        User.nickname, 
        User.profile_image
    ).where(User.google_id == google_id)

    result = await db.execute(query)    
    user = result.first()

    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    return user

# =============================================================================
# 설정 정보 조회 API
# =============================================================================
#
# URL: GET /api/users/settings/{google_id}
# 용도: Flutter 앱의 설정
#
@router.get("/settings/{google_id}", response_model=SettingResponse)
async def get_settings(google_id: str, db: AsyncSession = Depends(get_db)):

    query = select(
        User.push_alarm, 
        User.risk_push_alarm, 
        User.positive_push_alarm, 
        User.interest_push_alarm,
        User.night_push_prohibit,
        User.night_push_start,
        User.night_push_end
    ).where(User.google_id == google_id)

    result = await db.execute(query)    
    user = result.first()

    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    return user
    
# =============================================================================
# 설정 정보 변경 API
# =============================================================================
#
# URL: PATCH /api/users/settings/{google_id}
# 용도: Flutter 앱의 설정변경시 db에 저장
#
@router.patch("/settings/{google_id}", response_model=SettingResponse)
async def update_settings(
    google_id: str, 
    request: UserUpdateRequest,
    db: AsyncSession = Depends(get_db)
):

    query = select(User).where(User.google_id == google_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    
    # 요청 받은 데이터 중에서 값이 있는 것만 사용
    update_data = request.model_dump(exclude_unset=True)

    # 전체 푸시 OFF이면 상세 정보도 모두 OFF로 변경
    if update_data.get("push_alarm") is False:
        update_data["risk_push_alarm"] = False
        update_data["positive_push_alarm"] = False
        update_data["interest_push_alarm"] = False
    
    for key, value in update_data.items():
        setattr(user, key, value)

    await db.commit()
    await db.refresh(user)
    
    return user

# =============================================================================
# 회원 탈퇴 API
# =============================================================================
#
# URL: DELETE /api/users/{google_id}
# 용도: 회원 탈퇴 후 db에 유저 정보 삭제
#
@router.delete("/{google_id}", status_code=204)
async def delete_user(google_id: str, db: AsyncSession = Depends(get_db)):

    query = select(User).where(User.google_id == google_id)
    result = await db.execute(query)    
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    
    # 유저 정보 삭제
    await db.delete(user)
    await db.commit()
    return None