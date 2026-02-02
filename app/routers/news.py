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
from datetime import datetime
from google import genai
from google.genai import types

from app.database import get_db
from app.models import NaverNews, CrawledNews, ProcessStatus, StockSummaryCache, NewsStockMapping, FilteredNews
from app.schemas import NewsSimpleResponse, NewsListResponse, NewsDetailResponse, StockSummaryResponse
from app.config import get_settings


router = APIRouter(
    prefix="/api/news",
    tags=["news"],
)

settings = get_settings()

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

# 새로운 요약문을 생성하는 함수
async def call_gemini_summary(stock_name, num_article, text_combined):
    GOOGLE_API_KEY = settings.gemini_api
    client = genai.Client(api_key=GOOGLE_API_KEY)
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
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=text_combined,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2
            )
        )
        print("<새로운 요약문 생성 완료>")
        return response.text
    except Exception as e:
        print("<요약 생성 오류>")
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
            last_updated=cache.created_at or datetime.now(),
            message="관련 뉴스가 존재하지 않습니다."
        )

    latest_news_id = target_news_ids[0]

    if cache.latest_news_id == latest_news_id:
        return StockSummaryResponse(
            stock_name=stock_name,
            summary=cache.summary_text,
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
        cache.created_at = datetime.now() # onupdate 설정이 없다면 수동 갱신
        
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