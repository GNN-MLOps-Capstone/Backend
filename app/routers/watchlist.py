"""
==============================================================================
관심종목 API 라우터 (watchlist.py)
==============================================================================

API 엔드포인트:
    GET    /api/watchlist              -> 관심종목 목록
    POST   /api/watchlist              -> 종목 추가
    DELETE /api/watchlist/{code}       -> 종목 삭제
    GET    /api/watchlist/briefing     -> AI 브리핑 (Gemini, 시장 Top3 이슈)
    GET    /api/stocks/{code}          -> 종목 상세

==============================================================================
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, case, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert
import asyncio

from datetime import datetime, timedelta, timezone
import logging
from google.genai import types
from google import genai

from app.database import get_db

from app.models import Watchlist, Stock, StockSummaryCache, User, FilteredNews, NewsStockMapping
from app.routers.users import get_current_user
from app.routers.news import call_gemini_summary
from app.routers.news import get_stock_summary
from app.schemas import (
    WatchlistAddRequest,
    WatchlistStockResponse,
    IssueStock,
    IssueRankingResponse,
)
from app.config import get_settings
from app.services.kis_service import kis_service


router = APIRouter(
    prefix="/api",

    tags=["watchlist"],
)

settings = get_settings()
logger = logging.getLogger(__name__)
gemini_client = genai.Client(api_key=settings.gemini_api)

# =============================================================================
# 관심종목 목록 조회
# =============================================================================

@router.get("/watchlist", response_model=list[WatchlistStockResponse])
async def get_watchlist(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """관심종목 목록 조회"""
    query = (
        select(Watchlist)
        .where(Watchlist.user_id == current_user.id)
        .order_by(Watchlist.created_at.desc())
    )
    result = await db.execute(query)
    watchlist_items = result.scalars().all()

    if not watchlist_items:
        return []

    stock_codes = [item.stock_id for item in watchlist_items]

    prices = await kis_service.get_multiple_prices(stock_codes)

    stock_result = await db.execute(
        select(Stock).where(Stock.stock_id.in_(stock_codes))
    )
    stock_map = {row.stock_id: row for row in stock_result.scalars().all()}

    summary_map = {}
    ai_tasks_payload = []
    for item in watchlist_items:
        s_id = item.stock_id
        s_name = stock_map[s_id].stock_name if s_id in stock_map else s_id
        
        # 캐시 및 최신 뉴스 ID 확인
        cache_stmt = select(StockSummaryCache).where(StockSummaryCache.stock_id == s_id)
        cache_res = await db.execute(cache_stmt)
        cache = cache_res.scalar_one_or_none()

        news_stmt = (
            select(NewsStockMapping.news_id)
            .where(NewsStockMapping.stock_id == s_id)
            .order_by(desc(NewsStockMapping.created_at))
            .limit(10)
        )
        news_res = await db.execute(news_stmt)
        target_news_ids = [row[0] for row in news_res.fetchall()]

        # 케이스 분류: 뉴스가 아예 없는 경우
        if not target_news_ids:
            summary_map[s_id] = (cache.summary_text or f"{s_name}에 대한 최신 뉴스가 없습니다.") if cache else f"{s_name}에 대한 최신 뉴스가 없습니다."
            continue

        latest_news_id = target_news_ids[0]

        # 케이스 분류: 캐시가 최신인 경우 (Null 안전 체크 포함)
        if cache and cache.latest_news_id == latest_news_id and cache.summary_text:
            summary_map[s_id] = cache.summary_text
            continue

        # 케이스 분류: AI 요약 갱신이 필요한 경우 (뉴스 본문 수집)
        content_stmt = select(FilteredNews.summary).where(FilteredNews.news_id.in_(target_news_ids))
        content_res = await db.execute(content_stmt)
        news_summaries = [row[0] for row in content_res.fetchall() if row[0]]

        if not news_summaries:
            summary_map[s_id] = (cache.summary_text or "기사 내용을 불러올 수 없습니다.") if cache else "기사 내용을 불러올 수 없습니다."
            continue

        combined_text = "\n\n".join([f"### [기사 {i+1}]\n{s}" for i, s in enumerate(news_summaries)])
        
        # AI 호출용 페이로드 구성 (DB 세션 없이 순수 데이터만)
        ai_tasks_payload.append({
            "stock_id": s_id,
            "stock_name": s_name,
            "news_count": len(news_summaries),
            "combined_text": combined_text,
            "latest_news_id": latest_news_id
        })

    # 제미나이만 호출
    if ai_tasks_payload:
        # call_gemini_summary는 순수 I/O 작업이므로 병렬 처리가 가장 효율적입니다.
        gemini_results = await asyncio.gather(*[
            call_gemini_summary(p["stock_name"], p["news_count"], p["combined_text"])
            for p in ai_tasks_payload
        ])

        # ai 결과를 db에 저장
        for payload, new_summary in zip(ai_tasks_payload, gemini_results):
            s_id = payload["stock_id"]
            
            if new_summary:
                summary_map[s_id] = new_summary
                
                stmt = pg_insert(StockSummaryCache).values(
                    stock_id=s_id,
                    stock_name=payload["stock_name"],
                    summary_text=new_summary,
                    latest_news_id=payload["latest_news_id"],
                    created_at=datetime.now(timezone.utc)
                )

                stmt = stmt.on_conflict_do_update(
                    index_elements=["stock_id"],  # PK 혹은 Unique 제약 조건 컬럼
                    set_={
                        "summary_text": new_summary,
                        "latest_news_id": payload["latest_news_id"],
                        "stock_name": payload["stock_name"],
                        "created_at": datetime.now(timezone.utc)
                    }
                )
                await db.execute(stmt)
            else:
                summary_map[s_id] = "요약 생성에 실패했습니다."

        await db.commit() # 모든 변경 사항 일괄 저장

    response_list = []
    for item in watchlist_items:
        stock = stock_map.get(item.stock_id)
        stock_name = stock.stock_name if stock else item.stock_id

        price_info = prices.get(item.stock_id, {})
        price = price_info.get("price", 0)
        change_rate = price_info.get("change_rate", 0.0)

        if change_rate >= 2.0:
            weather = "SUNNY"
        elif change_rate <= -2.0:
            weather = "RAINY"
        else:
            weather = "CLOUDY"

        response_list.append(
            WatchlistStockResponse(
                code=item.stock_id,
                name=stock_name,
                weather=weather,
                price=price,
                changeRate=change_rate,
                keyword=stock.industry if stock and stock.industry else "",
                aiSummary=summary_map.get(item.stock_id) or "",
            )
        )

    return response_list


# =============================================================================
# 관심종목 추가
# =============================================================================

@router.post("/watchlist", response_model=dict)
async def add_watchlist(
    request: WatchlistAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """관심종목 추가"""
    stock_result = await db.execute(
        select(Stock).where(Stock.stock_id == request.code)
    )
    if not stock_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="존재하지 않는 종목입니다")

    try:
        db.add(Watchlist(user_id=current_user.id, stock_id=request.code))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return {"message": "이미 추가된 종목입니다", "code": request.code}

    return {"message": "관심종목 추가 완료", "code": request.code}


# =============================================================================
# 관심종목 삭제
# =============================================================================

@router.delete("/watchlist/{code}", response_model=dict)
async def delete_watchlist(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """관심종목 삭제"""
    await db.execute(
        delete(Watchlist)
        .where(Watchlist.stock_id == code, Watchlist.user_id == current_user.id)
    )
    await db.commit()

    return {"message": "관심종목 삭제 완료", "code": code}


# =============================================================================
# AI 브리핑 (Gemini - 시장 Top3 이슈 종목)
# =============================================================================

async def _get_top_issues(
    db: AsyncSession, 
    current_user: User, 
    top_n: int = 3
) -> list[IssueStock] | int:
    now = datetime.now(timezone.utc)
    past_7_days = now - timedelta(days=7)

    # 1. 사용자의 관심종목 리스트 가져오기
    watchlist_query = select(Watchlist.stock_id).where(Watchlist.user_id == current_user.id)
    watchlist_result = await db.execute(watchlist_query)
    watchlist_ids = [row for row in watchlist_result.scalars().all()]

    if not watchlist_ids:
        return 1  # 관심종목이 존재하지 않음

    # 2. 관심종목들의 최근 7일간 뉴스 통계 조회 (이슈지수 계산용)
    # FilteredNews와 NewsStockMapping을 조인하여 관심종목(watchlist_ids)에 해당하는 데이터만 필터링
    query_vol = (
        select(
            Stock.stock_id,
            Stock.stock_name,
            func.count(FilteredNews.news_id).label('recent_news_count')
        )
        .select_from(Stock)
        .join(NewsStockMapping, Stock.stock_id == NewsStockMapping.stock_id)
        .join(FilteredNews, NewsStockMapping.news_id == FilteredNews.news_id)
        .where(
            Stock.stock_id.in_(watchlist_ids),
            FilteredNews.created_at >= past_7_days,
            FilteredNews.created_at <= now
        )
        .group_by(Stock.stock_id, Stock.stock_name)
    )

    result_vol = await db.execute(query_vol)
    recent_vol_stats = result_vol.all()

    if not recent_vol_stats:
        return 2  # 관심종목에 대한 최근 7일간 뉴스가 없음

    # 3. 감성 점수 계산 (7일간 평균)
    sentiment_score_expr = case(
        (FilteredNews.sentiment == '긍정', 1.0),
        (FilteredNews.sentiment == '부정', -1.0),
        else_=0.0
    )

    issue_stock_ids = [stat.stock_id for stat in recent_vol_stats]

    query_sent = (
        select(
            Stock.stock_id,
            func.avg(sentiment_score_expr).label('avg_sentiment')
        )
        .select_from(Stock)
        .join(NewsStockMapping, Stock.stock_id == NewsStockMapping.stock_id)
        .join(FilteredNews, NewsStockMapping.news_id == FilteredNews.news_id)
        .where(
            Stock.stock_id.in_(issue_stock_ids),
            FilteredNews.created_at >= past_7_days,
            FilteredNews.created_at <= now
        )
        .group_by(Stock.stock_id)
    )
    
    result_sent = await db.execute(query_sent)
    sentiment_dict = {stat.stock_id: stat.avg_sentiment for stat in result_sent.all()}

    # 4. 이슈지수 산출 및 정규화
    counts = [stat.recent_news_count for stat in recent_vol_stats]
    max_count = max(counts) if counts else 1
    min_count = min(counts) if counts else 0

    processed_stocks = []
    for stat in recent_vol_stats:
        sid = stat.stock_id
        sname = stat.stock_name
        recent_count = stat.recent_news_count
        
        raw_sentiment = sentiment_dict.get(sid, 0.0)
        abs_sentiment = abs(raw_sentiment)

        # 뉴스량 Min-Max 정규화
        norm_recent_news = 0.0 if max_count == min_count else (recent_count - min_count) / (max_count - min_count)

        # 이슈지수 = (감성강도 * 0.7) + (뉴스량 * 0.3)
        issue_index = (abs_sentiment * 0.7) + (norm_recent_news * 0.3)

        processed_stocks.append(IssueStock(
            stock_name=sname,
            recent_news_count=recent_count,
            abs_recent_sentiment=round(abs_sentiment, 4),
            issue_index=round(issue_index, 4)
        ))

    # 5. 정렬 후 Top N 반환
    top_issues = sorted(processed_stocks, key=lambda x: x.issue_index, reverse=True)[:top_n]
    return top_issues

async def _call_gemini_briefing(combined_summaries: str) -> str:
    """3개 종목의 개별 요약문을 받아, 하나의 자연스러운 종합 브리핑으로 묶어줍니다."""

    system_prompt = """
    당신은 모바일 증권 앱의 수석 AI 애널리스트입니다.
    오늘 시장에서 가장 뜨거운 이슈가 된 Top 3 종목의 개별 요약문이 제공됩니다.
    제공된 요약문들을 바탕으로, 사용자가 모바일 화면에서 한눈에 읽기 편한 2~3문장 분량의 '종합 브리핑' 텍스트를 작성해주세요.
    
    [작성 규칙]
    1. ⚠️ 마크다운 기호(**, -, * 등)는 절대 사용하지 마세요. 오직 순수 텍스트로만 작성하세요.
    2. ⚠️ 첫 문장은 반드시 3개 종목의 공통된 테마나 오늘 시장의 전반적인 분위기를 아우르는 요약 문장으로 시작하세요.
       - 좋은 예: "오늘은 반도체·이차전지·AI 관련 종목을 중심으로 시장의 관심이 집중되었습니다."
    3. 두 번째 문장부터는 각 종목명과 그들의 이슈(강세/약세 이유)를 자연스럽게 이어 붙여 하나의 문단으로 완성하세요. 
       - 좋은 예: "특히 삼성전자는 ~로 강세를 보였고, 에코프로비엠은 ~와 함께 주가가 반등했습니다."
    4. 문장은 정중한 존댓말(~했습니다, ~입니다)을 사용하고, 기계적인 느낌 없이 아나운서가 뉴스를 브리핑하듯 물 흐르듯 자연스럽게 작성하세요.
    """
    
    try:
        # ※ gemini_client 초기화 코드가 외부에 있다고 가정합니다.
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=combined_summaries,
            config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.3
        )
        )
        logger.info("최종 AI 브리핑 멘트 생성 완료")
        text = response.text
        if not text:
            logger.warning("Gemini 응답이 비어 있습니다.")
            return "현재 시장 이슈 요약을 생성할 수 없습니다. 잠시 후 다시 시도해주세요."
        final_text = text.strip()
        logger.info(f"생성된 브리핑 결과 :  {final_text}")
        return final_text
    except Exception:
        logger.exception("최종 브리핑 생성 오류")
        return "현재 시장 이슈를 분석하는데 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

@router.get("/watchlist/briefing", response_model=IssueRankingResponse)
async def get_watchlist_briefing(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    logger.info(f"유저 {current_user.id}: 실시간 AI 브리핑 생성을 시작합니다.")

    top_issues = await _get_top_issues(db, current_user, top_n=3)
    if top_issues == 1:
        return IssueRankingResponse(text="선택하신 관심종목이 존재하지 않습니다.", top_issues=[])
    if top_issues == 2:
        return IssueRankingResponse(text="관심종목에 대해 최근 7일간 발생한 뉴스가 없습니다.", top_issues=[])

    summaries_text_list = []

    for issue in top_issues:
        stock_name = issue.stock_name
        try:
            stock_summary_response = await get_stock_summary(stock_name=stock_name, db=db)
            single_summary_text = stock_summary_response.summary
            if single_summary_text:
                summaries_text_list.append(f"[{stock_name} 요약]\n{single_summary_text}")
        except HTTPException:
            logger.warning("종목 요약 캐시 없음, 건너뜀: %s", stock_name)
            continue

    if not summaries_text_list:
        return IssueRankingResponse(
            text="현재 오류가 발생하여 다시 한번 나갔다가 들어와주시기 바랍니다.",
            top_issues=top_issues
        )

    combined_summaries = "\n\n".join(summaries_text_list)

    final_briefing_text = await _call_gemini_briefing(combined_summaries)

    return IssueRankingResponse(
        text=final_briefing_text,
        top_issues=top_issues
    )


# =============================================================================
# 종목 상세 조회
# =============================================================================

@router.get("/stocks/{code}", response_model=WatchlistStockResponse)
async def get_stock_detail(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """종목 상세 조회"""
    result = await db.execute(
        select(Stock).where(Stock.stock_id == code)
    )
    stock = result.scalar_one_or_none()

    if not stock:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다")

    price_info = await kis_service.get_stock_price(code)
    price = price_info.get("price", 0)
    change_rate = price_info.get("change_rate", 0.0)

    cache_result = await db.execute(
        select(StockSummaryCache).where(StockSummaryCache.stock_id == code)
    )
    cache = cache_result.scalar_one_or_none()

    if change_rate >= 2.0:
        weather = "SUNNY"
    elif change_rate <= -2.0:
        weather = "RAINY"
    else:
        weather = "CLOUDY"

    return WatchlistStockResponse(
        code=stock.stock_id,
        name=stock.stock_name or code,
        weather=weather,
        price=price,
        changeRate=change_rate,
        keyword=stock.industry or "",
        aiSummary=cache.summary_text if cache and cache.summary_text else "",
    )

async def get_or_update_summary(stock_id: str, db: AsyncSession, stock_name: str = None) -> str:
    """
    stock_id를 기준으로 캐시를 관리하고 최신 뉴스 발생 시 요약을 갱신함
    """
    # 1. stock_id로 캐시 조회
    stmt = select(StockSummaryCache).where(StockSummaryCache.stock_id == stock_id)
    result = await db.execute(stmt)
    cache = result.scalar_one_or_none()

    # 2. stock_name이 없다면 DB에서 조회 (AI 프롬프트용)
    if not stock_name:
        stock_stmt = select(Stock.stock_name).where(Stock.stock_id == stock_id)
        stock_res = await db.execute(stock_stmt)
        stock_name = stock_res.scalar_one_or_none() or stock_id

    # 캐시 레코드가 없으면 생성
    if not cache:
        cache = StockSummaryCache(stock_id=stock_id, stock_name=stock_name, summary_text="")
        db.add(cache)

    # 3. 최신 뉴스 10개 ID 확인
    news_stmt = (
        select(NewsStockMapping.news_id)
        .where(NewsStockMapping.stock_id == stock_id)
        .order_by(desc(NewsStockMapping.created_at))
        .limit(10)
    )
    news_res = await db.execute(news_stmt)
    target_news_ids = [row[0] for row in news_res.fetchall()]

    if not target_news_ids:
        return cache.summary_text or f"{stock_name}에 대한 최신 뉴스가 없습니다."

    latest_news_id = target_news_ids[0]

    # 4. 캐시가 최신이고 내용이 있다면 그대로 반환
    if cache.latest_news_id == latest_news_id and cache.summary_text:
        return cache.summary_text

    # 5. 캐시가 만료되었거나 비어있으면 갱신 로직 실행
    content_stmt = select(FilteredNews.summary).where(FilteredNews.news_id.in_(target_news_ids))
    content_res = await db.execute(content_stmt)
    news_summaries = [row[0] for row in content_res.fetchall() if row[0]]

    if not news_summaries:
        return cache.summary_text or "기사 내용을 불러올 수 없습니다."

    combined_text = "\n\n".join([f"### [기사 {i+1}]\n{s}" for i, s in enumerate(news_summaries)])
    
    # Gemini AI 호출 (이름을 전달하여 정확한 요약 유도)
    new_summary = await call_gemini_summary(stock_name, len(news_summaries), combined_text)

    if new_summary:
        cache.latest_news_id = latest_news_id
        cache.summary_text = new_summary
        cache.stock_name = stock_name # 이름 업데이트
        cache.created_at = datetime.now(timezone.utc)
        return new_summary
    
    return cache.summary_text or "요약 생성에 실패했습니다."