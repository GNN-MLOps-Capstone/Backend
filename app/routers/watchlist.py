"""
위치리스트
"""


from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from datetime import datetime, timedelta
import logging
from google.genai import types
from google import genai

from app.database import get_db
from app.models import FilteredNews, NewsStockMapping, Stock
from app.schemas import IssueStock
from app.config import get_settings
from app.routers.news import get_stock_summary
from app.config import get_settings


router = APIRouter(
    prefix="/api/watchlist",
    tags=["watchlist"],
)

settings = get_settings()
logger = logging.getLogger(__name__)
gemini_client = genai.Client(api_key=settings.gemini_api)

_briefing_cache = {
    "data": None,
    "expires_at": datetime.min # 초기값은 과거 시간으로 설정하여 무조건 1번은 실행되게 함
}
CACHE_TTL_MINUTES = 60

async def _get_top_issues(db: AsyncSession, top_n: int = 5, days: int = 7) -> list[IssueStock]:
    """
    주어진 기간(days) 동안의 뉴스를 분석하여 이슈지수가 높은 Top N 종목을 반환하는 내부 함수.
    """
    now = datetime.utcnow()
    past_days = now - timedelta(days=days)

    # ---------------------------------------------------------
    # 1. 지정된 기간(days)의 뉴스 발생량 조회
    # ---------------------------------------------------------
    query_vol = (
        select(
            Stock.stock_name,
            func.count(FilteredNews.news_id).label('recent_news_count')
        )
        .join(NewsStockMapping, Stock.stock_id == NewsStockMapping.stock_id)
        .join(FilteredNews, NewsStockMapping.news_id == FilteredNews.news_id)
        .where(
            FilteredNews.created_at >= past_days,
            FilteredNews.created_at <= now
        )
        .group_by(Stock.stock_name)
    )
    
    result_vol = await db.execute(query_vol)
    recent_vol_stats = result_vol.all()

    # 뉴스가 하나도 없으면 빈 리스트 반환
    if not recent_vol_stats:
        return []

    # 뉴스가 있는 종목명 리스트 추출
    issue_stocks = [stat.stock_name for stat in recent_vol_stats]

    # ---------------------------------------------------------
    # 2. 해당 종목들의 평균 감성점수 계산
    # ---------------------------------------------------------
    sentiment_score_expr = case(
        (FilteredNews.sentiment == '긍정', 1.0),
        (FilteredNews.sentiment == '부정', -1.0),
        else_=0.0
    )

    query_sent = (
        select(
            Stock.stock_name,
            func.avg(sentiment_score_expr).label('avg_sentiment')
        )
        .join(NewsStockMapping, Stock.stock_id == NewsStockMapping.stock_id)
        .join(FilteredNews, NewsStockMapping.news_id == FilteredNews.news_id)
        .where(
            Stock.stock_name.in_(issue_stocks),
            FilteredNews.created_at >= past_days,
            FilteredNews.created_at <= now
        )
        .group_by(Stock.stock_name) 
    )
    
    result_sent = await db.execute(query_sent)
    recent_sentiment_stats = result_sent.all()

    sentiment_dict = {stat.stock_name: stat.avg_sentiment for stat in recent_sentiment_stats}

    # ---------------------------------------------------------
    # 3. 로직 처리 (정규화 및 이슈지수 계산)
    # ---------------------------------------------------------
    counts = [stat.recent_news_count for stat in recent_vol_stats]
    max_count = max(counts) if counts else 1
    min_count = min(counts) if counts else 0

    processed_stocks = []

    for stat in recent_vol_stats:
        stock_name = stat.stock_name
        recent_count = stat.recent_news_count
        
        # 감성점수 가져오기 및 절대값 처리
        raw_sentiment = sentiment_dict.get(stock_name)
        abs_sentiment = abs(raw_sentiment) if raw_sentiment else 0.0

        # 뉴스량 Min-Max 정규화 (0.0 ~ 1.0)
        if max_count == min_count:
            norm_recent_news = 0.0
        else:
            norm_recent_news = (recent_count - min_count) / (max_count - min_count)

        # 이슈지수 계산
        issue_index = (abs_sentiment * 0.7) + (norm_recent_news * 0.3)

        # IssueStock Pydantic 모델로 생성하여 리스트에 추가
        processed_stocks.append(IssueStock(
            stock_name=stock_name,
            recent_24h_news_count=recent_count,
            abs_recent_sentiment=round(abs_sentiment, 4),
            issue_index=round(issue_index, 4)
        ))

    # ---------------------------------------------------------
    # 4. 정렬 후 Top N 반환
    # ---------------------------------------------------------
    top_issues = sorted(processed_stocks, key=lambda x: x.issue_index, reverse=True)[:top_n]

    return top_issues

async def call_gemini_overall_briefing(combined_summaries: str):
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
                temperature=0.3  # 자연스러운 문장 생성을 위해 온도를 살짝 높임
            )
        )
        logger.info("최종 AI 브리핑 멘트 생성 완료")
        return response.text.strip()
    except Exception:
        logger.exception("최종 브리핑 생성 오류")
        return "현재 시장 이슈를 분석하는데 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
    
@router.get("/briefing")
async def get_watchlist_briefing(db: AsyncSession = Depends(get_db)):
    # 1. Top 3 이슈 종목 선정 (이전에 만든 내부 함수 호출)
    # _get_top_issues 함수는 이전 답변에서 작성한 로직을 그대로 사용하시면 됩니다.
    global _briefing_cache
    now = datetime.utcnow()

    if _briefing_cache["data"] and now < _briefing_cache["expires_at"]:
        logger.info("캐시된 AI 브리핑 데이터를 반환합니다. (속도 0.01초!)")
        return _briefing_cache["data"]
    
    logger.info("새로운 AI 브리핑 데이터를 생성합니다. (API 호출)")

    top_issues = await _get_top_issues(db, top_n=3, days=7)
    
    if not top_issues:
        return {"text": "현재 시장에 뚜렷한 이슈 종목이 없습니다.", "top_issues": []}

    # 2. news.py 의 함수를 활용해 각 종목별 개별 요약문 수집
    summaries_text_list = []
    
    for issue in top_issues:
        stock_name = issue.stock_name
        
        # 💡 news.py에 있는 요약 함수 호출 (db 세션과 종목명을 넘겨서 가져온다고 가정)
        stock_summary_response = await get_stock_summary(stock_name=stock_name, db=db)

        single_summary_text = stock_summary_response.summary 

        if single_summary_text:
            summaries_text_list.append(f"[{stock_name} 요약]\n{single_summary_text}")

    # 3. 3개의 요약문을 하나의 긴 텍스트로 합치기
    combined_summaries = "\n\n".join(summaries_text_list)

    # 4. 결합된 텍스트를 제미나이에 넣고 최종 "브리핑 멘트" 생성
    final_briefing_text = await call_gemini_overall_briefing(combined_summaries)

    result_data = {
        "text": final_briefing_text,
        "top_issues": top_issues
    }

    _briefing_cache["data"] = result_data
    _briefing_cache["expires_at"] = now + timedelta(minutes=CACHE_TTL_MINUTES)
    logger.info(f"브리핑 캐시 갱신 완료! (다음 갱신: {CACHE_TTL_MINUTES}분 후)")

    # 5. 프론트엔드 포맷에 맞춰 응답 반환
    return result_data