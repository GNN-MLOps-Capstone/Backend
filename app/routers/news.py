"""
==============================================================================
뉴스 API 라우터 (news.py)
==============================================================================

이 파일은 뉴스 관련 API 엔드포인트를 정의합니다.

테이블 구조:
    - naver_news: 뉴스 메타데이터 (title, pub_date)
    - crawled_news: 뉴스 본문 (text = summary)
    - crawled_news.news_id -> naver_news.news_id (FK, 1:1)

API 엔드포인트:
    GET /api/news/simple  -> 최근 뉴스 목록 (앱용, pub_date 기준 정렬)
    GET /api/news/{id}    -> 뉴스 상세 조회

==============================================================================
"""

import html
import json
import base64
import binascii
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from typing import Optional
from datetime import datetime, timezone
from google import genai
from google.genai import types
import logging

from app.database import get_db
from app.models import (
    NaverNews,
    CrawledNews,
    ProcessStatus,
    StockSummaryCache,
    NewsStockMapping,
    FilteredNews,
    RecommendationServe,
)
from app.schemas import (
    NewsSimpleResponse,
    NewsListResponse,
    NewsDetailResponse,
    StockSummaryResponse,
    NewsRecommendationItem,
    NewsRecommendationResponse,
)
from app.config import get_settings
from app.kis.errors import KISError
from app.recommender.client import RecommendationClient, RecommendationCandidate


router = APIRouter(
    prefix="/api/news",
    tags=["news"],
)

settings = get_settings()
logger = logging.getLogger(__name__)
gemini_client = genai.Client(api_key=settings.gemini_api)
recommendation_client = RecommendationClient(settings)
_CURSOR_VERSION = 1

# =============================================================================
# 헬퍼 함수
# =============================================================================

def decode_html_entities(text: Optional[str]) -> Optional[str]:
    """
    HTML 엔티티를 실제 문자로 디코딩합니다.
    
    예시:
        &quot;  → "
        &amp;   → &
        &lt;    → <
        &gt;    → >
        &#39;   → '
    
    Args:
        text: 디코딩할 텍스트 (None이면 None 반환)
    
    Returns:
        디코딩된 텍스트
    """
    if text is None:
        return None
    return html.unescape(text)


def _encode_recommendation_cursor(*, page: int, offset: int, limit: int) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "page": page,
        "offset": offset,
        "limit": limit,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_recommendation_cursor(cursor: str) -> tuple[int, int, int | None]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid cursor format")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid cursor payload")

    if payload.get("v") != _CURSOR_VERSION:
        raise HTTPException(status_code=400, detail="Unsupported cursor version")

    try:
        page = int(payload.get("page"))
        offset = int(payload.get("offset"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid cursor values")

    if page < 1 or offset < 0:
        raise HTTPException(status_code=400, detail="Invalid cursor values")

    raw_limit = payload.get("limit")
    cursor_limit: int | None = None
    if raw_limit is not None:
        try:
            cursor_limit = int(raw_limit)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid cursor values")
        if cursor_limit < 1:
            raise HTTPException(status_code=400, detail="Invalid cursor values")

    return page, offset, cursor_limit


def _default_recommendation_path(source: str) -> str:
    path_map = {
        "recommender": "A1",
        "mock": "M1",
        "mock_page": "M2",
        "mock_fallback": "M3",
    }
    return path_map.get(source, "UNK")

# 새로운 요약문을 생성하는 함수
async def call_gemini_summary(stock_name, num_article, text_combined):
    summary_length = "2줄" if num_article <=5 else "3줄"

    system_prompt = f"""
    당신은 모바일 증권 앱의 AI 뉴스 요약 봇입니다. 
    사용자가 스마트폰으로 한눈에 볼 수 있도록, 아래 제공된 {num_article}개의 기사 요약문을 **모두 하나로 통합하여** '{stock_name}'의 전체 핵심 이슈를 **단 {summary_length}**로 압축 요약하세요.
        
    [작성 규칙]
    1. ⚠️ 절대 기사 요약문별로 개별 요약하지 말 것. 전체 기사 요약문을 아우르는 최종 {summary_length}만 출력할 것.
    2. 서술형 줄글(~했습니다)은 금지하고, 뉴스 헤드라인처럼 핵심 단어(명사형) 위주로 끝맺음할 것.
    3. 각 줄은 '- ' 기호로 시작할 것.
    4. 한 줄의 길이는 40자를 넘지 않을 것.
    5. 제목이나 인사말 없이 결과물만 바로 출력할 것.
    """

    try:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=text_combined,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2
            )
        )

        text = response.text

        if not text:
            logger.warning("Gemini 응답이 비어 있습니다: %s", stock_name)
            return None
        
        final_summary = text.strip()

        logger.info("새로운 요약문 생성 완료 [%s]:\n%s", stock_name, final_summary)

        return final_summary
    except Exception:
        logger.exception("요약 생성 오류: %s", stock_name)
        return None


# =============================================================================
# 뉴스 목록 조회 API (Flutter 앱용)
# =============================================================================
#
# URL: GET /api/news/simple
# 용도: Flutter 앱의 뉴스 목록 화면
#
# 동작:
#   1. naver_news 테이블에서 pub_date 기준 최신순 정렬
#   2. crawled_news와 조인하여 text(본문) 가져오기
#   3. 최신 20개 반환
#
@router.get("/simple", response_model=list[NewsSimpleResponse])
async def get_news_simple_list(
    limit: int = Query(20, ge=1, le=100, description="가져올 뉴스 개수 (기본 20개)"),
    search: Optional[str] = Query(None, description="검색어 (제목에서 검색)"),
    db: AsyncSession = Depends(get_db),
):
    """
    간단한 뉴스 목록 조회 (Flutter 앱용)
    
    naver_news 테이블에서 pub_date 기준 최신 뉴스를 가져오고,
    crawled_news 테이블에서 text(본문)를 조인하여 반환합니다.
    
    Parameters:
        limit: 가져올 뉴스 개수 (기본 20개, 최대 100개)
        search: 검색어 (제목에서 검색)
    
    Returns:
        list[NewsSimpleResponse]: 뉴스 목록
    """
    
    # 1. naver_news에서 crawl_status='crawl_success'인 것만 필터링
    # 2. pub_date 기준 최신순 정렬
    # 3. crawled_news와 JOIN하여 text(본문) 가져오기
    query = (
        select(NaverNews)
        .join(CrawledNews, NaverNews.news_id == CrawledNews.news_id)  # INNER JOIN
        .options(joinedload(NaverNews.crawled_news))
        .where(NaverNews.crawl_status == ProcessStatus.crawl_success)  # 크롤링 성공한 것만
        .order_by(desc(NaverNews.pub_date))  # pub_date 기준 최신순 정렬
    )
    
    # 검색어가 있으면 DB에서 LIKE 검색 (제목에서 검색)
    if search:
        query = query.where(NaverNews.title.ilike(f"%{search}%"))
    
    # limit 적용
    query = query.limit(limit)
    
    result = await db.execute(query)
    news_list = result.scalars().unique().all()
    
    # 응답 데이터 구성
    response_list = []
    for news in news_list:
        # crawled_news에서 text 가져오기 (summary로 사용)
        summary = None
        if news.crawled_news:
            summary = decode_html_entities(news.crawled_news.text)
        
        response_list.append(
            NewsSimpleResponse(
                news_id=news.news_id,
                title=decode_html_entities(news.title),  # HTML 엔티티 디코딩
                summary=summary,
                pub_date=news.pub_date,
            )
        )
    
    return response_list


async def _load_news_by_ids(
    db: AsyncSession,
    candidates: list[RecommendationCandidate],
    source: str,
) -> list[NewsRecommendationItem]:
    """
    추천 서버가 반환한 news_id 목록을 DB에서 조회해 프론트 응답 형태로 변환합니다.
    """
    if not candidates:
        return []

    candidate_map = {candidate.news_id: candidate for candidate in candidates}
    news_ids = list(candidate_map.keys())

    query = (
        select(NaverNews)
        .where(NaverNews.news_id.in_(news_ids))
    )
    result = await db.execute(query)
    rows = result.scalars().unique().all()
    row_map = {row.news_id: row for row in rows}

    summary_result = await db.execute(
        select(FilteredNews.news_id, FilteredNews.summary).where(FilteredNews.news_id.in_(news_ids))
    )
    summary_map = {news_id: summary for news_id, summary in summary_result.all()}

    response_items: list[NewsRecommendationItem] = []
    for news_id in news_ids:
        news = row_map.get(news_id)
        if not news:
            continue

        summary = decode_html_entities(summary_map.get(news_id))
        candidate = candidate_map[news_id]
        response_items.append(
            NewsRecommendationItem(
                news_id=news.news_id,
                title=decode_html_entities(news.title),
                summary=summary,
                pub_date=news.pub_date,
                path=candidate.path or _default_recommendation_path(source),
            )
        )

    return response_items


async def _mock_candidates_from_db(db: AsyncSession, limit: int) -> list[RecommendationCandidate]:
    """
    추천 서버가 준비되지 않은 동안 사용할 DB 기반 목업 추천 결과입니다.
    """
    return await _mock_candidates_from_db_with_offset(db=db, limit=limit, offset=0)


async def _mock_candidates_from_db_with_offset(
    db: AsyncSession,
    limit: int,
    offset: int,
) -> list[RecommendationCandidate]:
    """
    무한 스크롤 구현을 위해 offset 기반 목업 추천 결과를 반환합니다.
    """
    query = (
        select(FilteredNews.news_id)
        .where(
            FilteredNews.summary.is_not(None),
            FilteredNews.summary != "",
        )
        .order_by(desc(FilteredNews.created_at), desc(FilteredNews.news_id))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.scalars().all()
    return [RecommendationCandidate(news_id=int(news_id)) for news_id in rows]


async def _log_recommendation_serve(
    db: AsyncSession,
    user_id: int,
    request_id: str,
    page: int,
    limit: int,
    screen_session_id: str | None,
    app_session_id: str | None,
    source: str,
    candidates: list[RecommendationCandidate],
) -> bool:
    """
    추천 목록 응답(요청 단위 + 아이템 목록)을 저장합니다.
    같은 request_id/page 조합은 중복 저장하지 않습니다.
    """
    base_position = (page - 1) * limit
    served_items = []
    for idx, candidate in enumerate(candidates, start=1):
        served_items.append(
            {
                "news_id": candidate.news_id,
                "position": base_position + idx,
                "path": candidate.path or _default_recommendation_path(source),
            }
        )

    serve = RecommendationServe(
        request_id=request_id,
        user_id=user_id,
        screen_session_id=screen_session_id,
        app_session_id=app_session_id,
        source=source,
        page=page,
        limit=limit,
        served_count=len(candidates),
        is_mock=source.startswith("mock"),
        served_items=served_items,
    )
    db.add(serve)
    try:
        await db.commit()
        return True
    except IntegrityError as exc:
        await db.rollback()
        error_text = str(getattr(exc, "orig", exc)).lower()
        is_request_page_duplicate = (
            ("unique" in error_text or "duplicate" in error_text)
            and (
                "uq_recommendation_serves_request_page" in error_text
                or (
                    "recommendation_serves" in error_text
                    and "request_id" in error_text
                    and "page" in error_text
                )
            )
        )
        if is_request_page_duplicate:
            logger.info(
                "recommendation serve duplicate skipped: request_id=%s page=%s err=%s",
                request_id,
                page,
                exc,
            )
            return False
        raise


@router.get("/recommendations", response_model=NewsRecommendationResponse)
async def get_news_recommendations(
    user_id: int = Query(..., ge=1, description="추천 대상 사용자 ID(users.id)"),
    limit: int = Query(20, ge=1, le=100, description="가져올 추천 뉴스 개수"),
    page: int = Query(1, ge=1, le=1000, description="무한 스크롤 페이지 (1부터 시작)"),
    cursor: Optional[str] = Query(None, max_length=512, description="다음 페이지 커서 (전달 시 page보다 우선)"),
    request_id: Optional[str] = Query(None, max_length=128, description="추천 요청 추적 ID (미전달 시 서버 생성)"),
    screen_session_id: Optional[str] = Query(None, max_length=64, description="추천 화면 세션 ID"),
    app_session_id: Optional[str] = Query(None, max_length=255, description="앱 세션 ID"),
    log_served: bool = Query(True, description="추천 응답을 DB 로깅할지 여부"),
    db: AsyncSession = Depends(get_db),
):
    """
    추천 시스템 서버와 연동해 사용자 맞춤 뉴스 추천 목록을 반환합니다.

    - RECOMMENDER_MOCK_MODE=true: 외부 서버 대신 DB 기반 mock 추천 사용
    - RECOMMENDER_MOCK_MODE=false: 외부 추천 서버 호출
    - cursor 전달 시 page 대신 cursor 기반으로 다음 구간 조회
    - page > 1: 아직 추천 서버 페이지네이션 미연결 상태라 mock 오프셋으로 동작
    """
    resolved_request_id = request_id or f"req-{uuid4().hex}"
    resolved_page = page
    offset = (resolved_page - 1) * limit
    if cursor:
        resolved_page, offset, cursor_limit = _decode_recommendation_cursor(cursor)
        if cursor_limit is not None and cursor_limit != limit:
            raise HTTPException(status_code=400, detail="Cursor limit mismatch")

    source = "recommender"
    candidates: list[RecommendationCandidate] = []

    # source 값은 추천 결과의 출처를 분석/디버깅할 때 그대로 사용됩니다.
    # - mock: 강제 목업 모드
    # - mock_page: 외부 추천은 1페이지만 사용하고 2페이지 이상은 DB 오프셋 목업
    # - mock_fallback: 외부 추천 실패/빈 결과 시 자동 대체
    # - recommender: 외부 추천 서버 정상 결과
    if settings.recommender_mock_mode:
        source = "mock"
        candidates = await _mock_candidates_from_db_with_offset(db=db, limit=limit, offset=offset)
    elif resolved_page > 1:
        source = "mock_page"
        candidates = await _mock_candidates_from_db_with_offset(db=db, limit=limit, offset=offset)
    else:
        try:
            candidates = await recommendation_client.get_news_candidates(user_id=user_id, limit=limit)
        except KISError as exc:
            logger.warning("추천 서버 호출 실패: %s", exc)
            candidates = []

        if not candidates:
            source = "mock_fallback"
            candidates = await _mock_candidates_from_db_with_offset(db=db, limit=limit, offset=offset)

    items = await _load_news_by_ids(db, candidates, source=source)
    candidate_by_id: dict[int, RecommendationCandidate] = {
        candidate.news_id: candidate for candidate in candidates
    }
    served_candidates = [
        candidate_by_id[item.news_id]
        for item in items
        if item.news_id in candidate_by_id
    ]
    logged = False
    if log_served:
        logged = await _log_recommendation_serve(
            db=db,
            user_id=user_id,
            request_id=resolved_request_id,
            page=resolved_page,
            limit=limit,
            screen_session_id=screen_session_id,
            app_session_id=app_session_id,
            source=source,
            candidates=served_candidates,
        )

    next_cursor: Optional[str] = None
    if len(items) == limit:
        next_cursor = _encode_recommendation_cursor(
            page=resolved_page + 1,
            offset=offset + len(items),
            limit=limit,
        )

    return NewsRecommendationResponse(
        user_id=user_id,
        request_id=resolved_request_id,
        source=source,
        page=resolved_page,
        next_cursor=next_cursor,
        served_count=len(items),
        logged=logged,
        items=items,
    )


# =============================================================================
# 뉴스 상세 조회 API
# =============================================================================
#
# URL: GET /api/news/{news_id}
#
@router.get("/{news_id}", response_model=NewsDetailResponse)
async def get_news_detail(
    news_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    뉴스 상세 조회
    
    Parameters:
        news_id: 뉴스 ID (PK)
    
    Returns:
        NewsDetailResponse: 뉴스 상세 정보
    """
    
    query = (
        select(NaverNews)
        .options(joinedload(NaverNews.crawled_news))
        .where(NaverNews.news_id == news_id)
    )
    
    result = await db.execute(query)
    news = result.scalar_one_or_none()
    
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    
    # summary 가져오기
    summary = None
    if news.crawled_news:
        summary = decode_html_entities(news.crawled_news.text)
    
    return NewsDetailResponse(
        news_id=news.news_id,
        title=decode_html_entities(news.title),  # HTML 엔티티 디코딩
        summary=summary,
        pub_date=news.pub_date,
        url=news.url,
    )


# =============================================================================
# 뉴스 통계 API
# =============================================================================
#
# URL: GET /api/news/stats/summary
#
@router.get("/stats/summary")
async def get_news_stats(
    db: AsyncSession = Depends(get_db),
):
    """
    뉴스 통계 조회
    
    Returns:
        dict: 통계 정보
    """
    
    # 전체 뉴스 개수
    total_result = await db.execute(select(func.count(NaverNews.news_id)))
    total = total_result.scalar() or 0
    
    # 크롤링된 뉴스 개수
    crawled_result = await db.execute(select(func.count(CrawledNews.crawled_news_id)))
    crawled = crawled_result.scalar() or 0
    
    return {
        "total_naver_news": total,
        "total_crawled_news": crawled,
    }

# =============================================================================
# 종목의 3줄 요약 응답 API
# =============================================================================
#
# URL: GET /api/news/summary/{stock_name}
#
@router.get("/summary/{stock_name}", response_model=StockSummaryResponse)
async def get_stock_summary(
    stock_name: str,
    db: AsyncSession = Depends(get_db)
):
    """
    종목별 3줄 요약 조회 및 생성
    """

    stmt = select(StockSummaryCache).where(StockSummaryCache.stock_name == stock_name)
    result = await db.execute(stmt)
    cache = result.scalar_one_or_none()

    if not cache:
        raise HTTPException(status_code=404, detail="해당 종목명은 존재하지 않습니다.")

    news_stmt = (
        select(NewsStockMapping.news_id)
        .where(NewsStockMapping.stock_id == cache.stock_id)
        .order_by(desc(NewsStockMapping.created_at))
        .limit(10)
    )
    news_res = await db.execute(news_stmt)
    target_news_ids = [row[0] for row in news_res.fetchall()]

    if not target_news_ids:
        return StockSummaryResponse(
            stock_name=stock_name,
            summary=f"{stock_name} 종목에 관련된 최신 뉴스가 없습니다.",
            last_updated=cache.created_at or datetime.now(timezone.utc),
            message="관련 뉴스가 존재하지 않습니다."
        )

    latest_news_id = target_news_ids[0]

    if cache.latest_news_id == latest_news_id:
        return StockSummaryResponse(
            stock_name=stock_name,
            summary=cache.summary_text or "요약 정보가 없습니다.",
            last_updated=cache.created_at,
            message="기존 요약문을 가져왔습니다."
        )
    
    content_stmt = select(FilteredNews.summary).where(FilteredNews.news_id.in_(target_news_ids))
    content_res = await db.execute(content_stmt)
    news_summaries = [row[0] for row in content_res.fetchall() if row[0]]

    combined_text = "\n\n".join([f"### [기사 {i+1}]\n{s}" for i, s in enumerate(news_summaries)])
    
    # Gemini AI 호출
    new_summary = await call_gemini_summary(stock_name, len(news_summaries), combined_text)

    if new_summary:
        cache.latest_news_id = latest_news_id
        cache.summary_text = new_summary
        cache.created_at = datetime.now(timezone.utc) # onupdate 설정이 없다면 수동 갱신
        
        await db.commit()

        return StockSummaryResponse(
            stock_name=stock_name,
            summary=new_summary,
            last_updated=cache.created_at,
            message="새로운 요약문을 생성했습니다."
        )
    
    # 생성 실패 시 기존 데이터 반환
    return StockSummaryResponse(
        stock_name=stock_name,
        summary=cache.summary_text,
        last_updated=cache.created_at,
        message="요약 생성에 실패하여 기존 데이터를 반환합니다."
    )
