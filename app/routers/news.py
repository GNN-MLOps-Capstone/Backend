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
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import joinedload
from typing import Optional

from app.database import get_db
from app.models import NaverNews, CrawledNews, ProcessStatus
from app.schemas import NewsSimpleResponse, NewsListResponse, NewsDetailResponse


router = APIRouter(
    prefix="/api/news",
    tags=["news"],
)


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
