"""
추천 탭/뉴스 체류시간 수집 라우터
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import InteractionEvent, ScreenSession, ContentSession, RecommendationFeedback
from app.schemas import (
    InteractionEventBatchRequest,
    InteractionIngestResponse,
    SessionFinalizeResponse,
)


router = APIRouter(
    prefix="/api/interactions",
    tags=["interactions"],
)


_SCREEN_START = {"screen_view"}
_SCREEN_HEARTBEAT = {"screen_heartbeat"}
_SCREEN_END = {"screen_leave"}
_CONTENT_START = {"content_open"}
_CONTENT_HEARTBEAT = {"content_heartbeat"}
_CONTENT_END = {"content_leave"}
_RECOMMEND_REQUEST = {"recommendation_request"}
_RECOMMEND_RESPONSE = {"recommendation_response"}
_RECOMMEND_IMPRESSION = {"recommendation_impression"}
_SCROLL = {"scroll_depth"}
_MAX_BATCH_SIZE = 500
# 이벤트 타입 그룹별 후속 처리 요약
# - screen_*: screen_sessions 갱신
# - content_*: content_sessions 갱신
# - recommendation_impression/content_*: recommendation_feedback 보정
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


def _as_utc(dt: datetime | None) -> datetime:
    now = datetime.now(timezone.utc)
    if dt is None:
        return now
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dwell_ms(started_at: datetime, ended_at: datetime) -> int:
    delta_ms = int((ended_at - started_at).total_seconds() * 1000)
    # 비정상 타임스탬프/장시간 방치 이벤트로 과대 집계되지 않도록 10분으로 상한 제한
    return max(0, min(delta_ms, 10 * 60 * 1000))


async def _upsert_recommendation_feedback(db: AsyncSession, event: InteractionEvent, event_at: datetime) -> int:
    """
    이벤트 원본 로그를 기반으로 추천 학습용 피드백 레코드를 보정합니다.
    """
    if event.event_type in _RECOMMEND_IMPRESSION:
        if not event.request_id or event.news_id is None:
            return 0
        page = event.page or 1
        result = await db.execute(
            select(RecommendationFeedback).where(
                RecommendationFeedback.request_id == event.request_id,
                RecommendationFeedback.user_id == event.user_id,
                RecommendationFeedback.page == page,
                RecommendationFeedback.news_id == event.news_id,
                RecommendationFeedback.position == event.position,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            db.add(
                RecommendationFeedback(
                    request_id=event.request_id,
                    user_id=event.user_id,
                    app_session_id=event.app_session_id,
                    screen_session_id=event.screen_session_id,
                    source="recommendations",
                    page=page,
                    news_id=event.news_id,
                    position=event.position,
                    impression_count=1,
                    first_impression_at=event_at,
                    last_impression_at=event_at,
                )
            )
            return 1

        row.impression_count += 1
        if row.first_impression_at is None or event_at < row.first_impression_at:
            row.first_impression_at = event_at
        if row.last_impression_at is None or event_at > row.last_impression_at:
            row.last_impression_at = event_at
        if event.app_session_id and not row.app_session_id:
            row.app_session_id = event.app_session_id
        if event.screen_session_id and not row.screen_session_id:
            row.screen_session_id = event.screen_session_id
        return 1

    if event.event_type in _CONTENT_START:
        if not event.request_id or event.news_id is None:
            return 0
        stmt = select(RecommendationFeedback).where(
            RecommendationFeedback.request_id == event.request_id,
            RecommendationFeedback.user_id == event.user_id,
            RecommendationFeedback.news_id == event.news_id,
        )
        if event.page is not None:
            stmt = stmt.where(RecommendationFeedback.page == event.page)
        if event.position is not None:
            stmt = stmt.where(RecommendationFeedback.position == event.position)
        result = await db.execute(
            stmt.order_by(RecommendationFeedback.updated_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            db.add(
                RecommendationFeedback(
                    request_id=event.request_id,
                    user_id=event.user_id,
                    app_session_id=event.app_session_id,
                    screen_session_id=event.screen_session_id,
                    content_session_id=event.content_session_id,
                    source="recommendations",
                    page=event.page or 1,
                    news_id=event.news_id,
                    position=event.position,
                    clicked=True,
                    clicked_at=event_at,
                )
            )
            return 1

        row.clicked = True
        if row.clicked_at is None or event_at < row.clicked_at:
            row.clicked_at = event_at
        if event.content_session_id:
            row.content_session_id = event.content_session_id
        if event.position is not None and row.position is None:
            row.position = event.position
        if event.page is not None and row.page == 1 and event.page > 1:
            row.page = event.page
        if event.app_session_id and not row.app_session_id:
            row.app_session_id = event.app_session_id
        if event.screen_session_id and not row.screen_session_id:
            row.screen_session_id = event.screen_session_id
        return 1

    if event.event_type in _CONTENT_END:
        if not event.content_session_id:
            return 0
        result = await db.execute(
            select(RecommendationFeedback)
            .where(RecommendationFeedback.content_session_id == event.content_session_id)
            .order_by(RecommendationFeedback.updated_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return 0

        started_at = row.clicked_at or row.last_impression_at or row.first_impression_at or event_at
        row.exited_at = event_at
        row.dwell_ms = _dwell_ms(started_at, event_at)
        # 학습용 지표 단순화를 위해 15초 이상 체류를 completed_read 기준으로 사용
        row.completed_read = row.dwell_ms >= 15_000
        return 1

    return 0


async def _upsert_screen_session(db: AsyncSession, event: InteractionEvent, event_at: datetime) -> int:
    if not event.screen_session_id:
        return 0

    result = await db.execute(
        select(ScreenSession).where(ScreenSession.screen_session_id == event.screen_session_id)
    )
    session = result.scalar_one_or_none()

    if event.event_type in _SCREEN_START:
        if session is None:
            session = ScreenSession(
                screen_session_id=event.screen_session_id,
                user_id=event.user_id,
                app_session_id=event.app_session_id,
                request_id=event.request_id,
                started_at=event_at,
                last_heartbeat_at=event_at,
                status="active",
            )
            db.add(session)
            return 1

        if session.status != "ended":
            if event_at < session.started_at:
                session.started_at = event_at
            if session.last_heartbeat_at is None or event_at > session.last_heartbeat_at:
                session.last_heartbeat_at = event_at
            return 1
        return 0

    if session is None:
        # 모바일 배치 전송 특성상 heartbeat/leave가 먼저 도착할 수 있어 세션을 보정 생성
        session = ScreenSession(
            screen_session_id=event.screen_session_id,
            user_id=event.user_id,
            app_session_id=event.app_session_id,
            request_id=event.request_id,
            started_at=event_at,
            status="active",
        )
        db.add(session)

    if event.event_type in _SCREEN_HEARTBEAT:
        if session.status != "ended":
            if session.last_heartbeat_at is None or event_at > session.last_heartbeat_at:
                session.last_heartbeat_at = event_at
            return 1
        return 0

    if event.event_type in _SCREEN_END:
        if session.status != "ended":
            session.ended_at = event_at
            session.last_heartbeat_at = event_at if session.last_heartbeat_at is None or event_at > session.last_heartbeat_at else session.last_heartbeat_at
            session.dwell_ms = _dwell_ms(session.started_at, event_at)
            session.status = "ended"
            return 1

    return 0


async def _upsert_content_session(db: AsyncSession, event: InteractionEvent, event_at: datetime) -> int:
    if not event.content_session_id:
        return 0

    result = await db.execute(
        select(ContentSession).where(ContentSession.content_session_id == event.content_session_id)
    )
    session = result.scalar_one_or_none()

    if event.event_type in _CONTENT_START:
        if event.news_id is None:
            raise HTTPException(status_code=400, detail="content_open requires news_id")

        if session is None:
            session = ContentSession(
                content_session_id=event.content_session_id,
                user_id=event.user_id,
                app_session_id=event.app_session_id,
                screen_session_id=event.screen_session_id,
                news_id=event.news_id,
                started_at=event_at,
                last_heartbeat_at=event_at,
                status="active",
            )
            db.add(session)
            return 1

        if session.status != "ended":
            if event_at < session.started_at:
                session.started_at = event_at
            if session.last_heartbeat_at is None or event_at > session.last_heartbeat_at:
                session.last_heartbeat_at = event_at
            if event.news_id is not None:
                session.news_id = event.news_id
            return 1
        return 0

    if session is None:
        # content_open 누락 상황 보정: content_session_id는 있으나 news_id 없으면 집계 불가
        if event.news_id is None:
            return 0
        session = ContentSession(
            content_session_id=event.content_session_id,
            user_id=event.user_id,
            app_session_id=event.app_session_id,
            screen_session_id=event.screen_session_id,
            news_id=event.news_id,
            started_at=event_at,
            status="active",
        )
        db.add(session)

    if event.event_type in _CONTENT_HEARTBEAT:
        if session.status != "ended":
            if session.last_heartbeat_at is None or event_at > session.last_heartbeat_at:
                session.last_heartbeat_at = event_at
            return 1
        return 0

    if event.event_type in _CONTENT_END:
        if session.status != "ended":
            session.ended_at = event_at
            session.last_heartbeat_at = event_at if session.last_heartbeat_at is None or event_at > session.last_heartbeat_at else session.last_heartbeat_at
            session.dwell_ms = _dwell_ms(session.started_at, event_at)
            session.status = "ended"
            return 1

    return 0


@router.post("/events", response_model=InteractionIngestResponse)
async def ingest_interaction_events(
    payload: InteractionEventBatchRequest,
    db: AsyncSession = Depends(get_db),
):
    if len(payload.events) > _MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"events batch too large (max {_MAX_BATCH_SIZE})",
        )

    accepted = 0
    duplicated = 0
    screen_updated = 0
    content_updated = 0
    feedback_updated = 0

    for item in payload.events:
        if item.event_type not in _ALLOWED_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported event_type: {item.event_type}")
        if item.event_type in (_RECOMMEND_REQUEST | _RECOMMEND_RESPONSE):
            if not item.request_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires request_id")
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
        if item.event_type in _SCROLL:
            if not item.screen_session_id:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires screen_session_id")
            if item.scroll_depth is None:
                raise HTTPException(status_code=400, detail=f"{item.event_type} requires scroll_depth")

        try:
            async with db.begin_nested():
                # event_id 기준 사전 중복 체크로 대부분의 재전송 이벤트를 빠르게 스킵
                exists = await db.execute(
                    select(InteractionEvent.event_id).where(InteractionEvent.event_id == item.event_id)
                )
                if exists.scalar_one_or_none() is not None:
                    duplicated += 1
                    continue

                event_at = _as_utc(item.event_ts_client)
                event_row = InteractionEvent(
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
                    event_ts_client=event_at,
                )
                db.add(event_row)
                await db.flush()

                screen_updated += await _upsert_screen_session(db, event_row, event_at)
                content_updated += await _upsert_content_session(db, event_row, event_at)
                feedback_updated += await _upsert_recommendation_feedback(db, event_row, event_at)

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
        screen_updated=screen_updated,
        content_updated=content_updated,
        feedback_updated=feedback_updated,
    )


@router.post("/finalize-timeouts", response_model=SessionFinalizeResponse)
async def finalize_timeout_sessions(
    grace_seconds: int = Query(30, ge=5, le=300),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=grace_seconds)

    screen_result = await db.execute(
        select(ScreenSession).where(
            ScreenSession.status == "active",
            ((ScreenSession.last_heartbeat_at.is_not(None)) & (ScreenSession.last_heartbeat_at < threshold))
            | ((ScreenSession.last_heartbeat_at.is_(None)) & (ScreenSession.started_at < threshold))
        )
    )
    screen_rows = screen_result.scalars().all()

    for row in screen_rows:
        end_at = row.last_heartbeat_at or row.started_at
        row.ended_at = end_at
        row.dwell_ms = _dwell_ms(row.started_at, end_at)
        row.status = "ended"

    content_result = await db.execute(
        select(ContentSession).where(
            ContentSession.status == "active",
            ((ContentSession.last_heartbeat_at.is_not(None)) & (ContentSession.last_heartbeat_at < threshold))
            | ((ContentSession.last_heartbeat_at.is_(None)) & (ContentSession.started_at < threshold))
        )
    )
    content_rows = content_result.scalars().all()

    for row in content_rows:
        end_at = row.last_heartbeat_at or row.started_at
        row.ended_at = end_at
        row.dwell_ms = _dwell_ms(row.started_at, end_at)
        row.status = "ended"

    await db.commit()

    return SessionFinalizeResponse(
        screen_finalized=len(screen_rows),
        content_finalized=len(content_rows),
    )
