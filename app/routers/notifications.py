"""
==============================================================================
알람 API 라우터 (notifications.py)
==============================================================================

이 파일은 알림 관련 API 엔드포인트를 정의합니다.

테이블 구조:
    - notifications: 알람 데이터

API 엔드포인트:
    GET /api/notifications  -> 알림 내역 조회
    PATCH /api/notifications/read  -> 읽음처리
    POST /api/notifications    -> 알림 DB에 저장

==============================================================================
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, desc
from typing import List

from app.database import get_db
from app.models import Notification,User
from app.schemas import NotificationCreateRequest, NotificationResponse, NotificationReadRequest, NotificationCountResponse
from app.routers.users import get_current_user

router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
)


# =============================================================================
# 알람 내역 조회 API
# =============================================================================
#
# URL: GET /api/notifications
# 용도: 알림 탭 진입 시 목록 조회 (무한 스크롤 지원)
#
@router.get("", response_model=List[NotificationResponse])
async def get_notifications(
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    
    offset = (page - 1) * size
    noti_query = (
        select(Notification)
        .where(Notification.user_id == current_user.google_id) # current_user 사용
        .order_by(desc(Notification.created_at))
        .offset(offset)
        .limit(size)
    )
    
    result = await db.execute(noti_query)
    notifications = result.scalars().all()
    
    # 3. 결과 반환
    # (팁: response_model 덕분에 그냥 notifications 리스트를 바로 리턴해도 되지만, 
    #  안전하게 명시적으로 변환하신 기존 코드를 유지했습니다.)
    response_list = []
    for noti in notifications:
        response_list.append(NotificationResponse(
            id=noti.id,
            type=noti.type,
            title=noti.title,
            body=noti.body,
            read=noti.is_read,
            created_at=noti.created_at
        ))
        
    return response_list


# =============================================================================
# 읽음 처리 API
# =============================================================================
#
# URL: PATCH /api/notifications/read
# 용도: 특정 알림 클릭(단건) 또는 '모두 읽음' 버튼(전체)
#
@router.patch("/read", response_model=NotificationCountResponse)
async def read_notification(
    payload: NotificationReadRequest, 
    current_user: User = Depends(get_current_user), # ⭐️ 인증 적용
    db: AsyncSession = Depends(get_db)
):
    target_id = payload.id

    if target_id is not None:
        # [Case 1] 단건 읽음 처리
        # 내 알림이 맞는지(user_id == current_user.google_id) 확인하고 업데이트
        await db.execute(
            update(Notification)
            .where(Notification.id == target_id, Notification.user_id == current_user.google_id)
            .values(is_read=True)
        )
    else:
        # [Case 2] 전체 읽음 처리
        await db.execute(
            update(Notification)
            .where(Notification.user_id == current_user.google_id, Notification.is_read == False)
            .values(is_read=True)
        )
    
    await db.commit()

    # 3. 남은 안 읽은 개수 리턴
    count_query = select(func.count()).where(
        Notification.user_id == current_user.google_id,
        Notification.is_read == False
    )
    result = await db.execute(count_query)
    
    return {"unread_count": result.scalar()}
# =============================================================================
# 알림 저장 API
# =============================================================================
#
# URL: POST /api/notifications
# 용도: 앱이 OneSignal 발송 성공 후, DB에 이력을 남기기 위해 호출
#
@router.post("", status_code=201)
async def create_notification(
    req: NotificationCreateRequest, # Body에는 내용만 있음 (user_id 없음)
    current_user: User = Depends(get_current_user), # ⭐️ 인증 적용
    db: AsyncSession = Depends(get_db)
):
    new_noti = Notification(
        user_id=current_user.google_id,  # 👈 토큰 주인 ID
        type=req.type,
        title=req.title,
        body=req.body,
        is_read=False
    )
    
    try:
        db.add(new_noti)
        await db.commit()
        await db.refresh(new_noti)
        return {"success": True, "id": new_noti.id}
    except Exception as e:
        print(f"❌ 알림 저장 실패: {e}")
        raise HTTPException(status_code=400, detail="알림 저장 실패")