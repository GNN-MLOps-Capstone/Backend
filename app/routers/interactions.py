"""
추천/콘텐츠 상호작용 원본 이벤트 수집 라우터
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import InteractionEvent, InteractionEventType, User
from app.schemas import InteractionEventBatchRequest, InteractionIngestResponse
from app.routers.users import get_current_user


router = APIRouter(
    prefix="/api/interactions",
    tags=["interactions"],
)


_SCREEN_START = {InteractionEventType.screen_view}
_SCREEN_HEARTBEAT = {InteractionEventType.screen_heartbeat}
_SCREEN_END = {InteractionEventType.screen_leave}
_CONTENT_START = {InteractionEventType.content_open}
_CONTENT_HEARTBEAT = {InteractionEventType.content_heartbeat}
_CONTENT_END = {InteractionEventType.content_leave}
_RECOMMEND_REQUEST = {InteractionEventType.recommendation_request}
_RECOMMEND_RESPONSE = {InteractionEventType.recommendation_response}
_RECOMMEND_REQUEST_RESPONSE = _RECOMMEND_REQUEST | _RECOMMEND_RESPONSE
_RECOMMEND_IMPRESSION = {InteractionEventType.recommendation_impression}
_SCROLL = {InteractionEventType.scroll_depth}
_MAX_BATCH_SIZE = 500
_ALLOWED_TYPES = (
    _SCREEN_START
    | _SCREEN_HEARTBEAT
    | _SCREEN_END
    | _CONTENT_START
    | _CONTENT_HEARTBEAT
    | _CONTENT_END
    | _RECOMMEND_REQUEST
    | _RECOMMEND_RESPONSE
    | _RECOMMEND_IMPRESSION
    | _SCROLL
)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.post("/events", response_model=InteractionIngestResponse)
async def ingest_interaction_events(
    payload: InteractionEventBatchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if len(payload.events) > _MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"events batch too large (max {_MAX_BATCH_SIZE})",
        )

    accepted = 0
    duplicated = 0

    for item in payload.events:
        if item.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="event user_id does not match authenticated user")
        if item.event_type not in _ALLOWED_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported event_type: {item.event_type}")
        if item.event_type in _RECOMMEND_REQUEST_RESPONSE:
            if not item.request_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires request_id")
            if not item.screen_session_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires screen_session_id")
        if item.event_type in _SCREEN_START | _SCREEN_HEARTBEAT | _SCREEN_END:
            if not item.screen_session_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires screen_session_id")
        if item.event_type in _RECOMMEND_IMPRESSION:
            if not item.request_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires request_id")
            if not item.screen_session_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires screen_session_id")
            if item.news_id is None:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires news_id")
            if item.position is None:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires position")
        if item.event_type in _CONTENT_START:
            if not item.request_id:
                raise HTTPException(status_code=400, detail="content_open requires request_id")
            if not item.content_session_id:
                raise HTTPException(status_code=400, detail="content_open requires content_session_id")
            if item.news_id is None:
                raise HTTPException(status_code=400, detail="content_open requires news_id")
        if item.event_type in _CONTENT_HEARTBEAT | _CONTENT_END:
            if not item.content_session_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires content_session_id")
        if item.event_type in _SCROLL:
            if not item.screen_session_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires screen_session_id")
            if item.scroll_depth is None:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires scroll_depth")

        try:
            async with db.begin_nested():
                exists = await db.execute(
                    select(InteractionEvent.event_id).where(InteractionEvent.event_id == item.event_id)
                )
                if exists.scalar_one_or_none() is not None:
                    duplicated += 1
                    continue

                db.add(
                    InteractionEvent(
                        event_id=item.event_id,
                        user_id=item.user_id,
                        event_type=item.event_type,
                        device_id=item.device_id,
                        app_session_id=item.app_session_id,
                        screen_session_id=item.screen_session_id,
                        content_session_id=item.content_session_id,
                        news_id=item.news_id,
                        request_id=item.request_id,
                        position=item.position,
                        page=item.page,
                        scroll_depth=item.scroll_depth,
                        event_ts_client=_as_utc(item.event_ts_client),
                    )
                )
                await db.flush()
                accepted += 1
        except IntegrityError:
            exists_after = await db.execute(
                select(InteractionEvent.event_id).where(InteractionEvent.event_id == item.event_id)
            )
            if exists_after.scalar_one_or_none() is not None:
                duplicated += 1
                continue
            raise

    await db.commit()

    return InteractionIngestResponse(
        accepted=accepted,
        duplicated=duplicated,
    )
