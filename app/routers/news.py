"""
==============================================================================
뉴스 API 라우터 (news.py)
==============================================================================

이 파일은 뉴스 관련 API 엔드포인트를 정의합니다.

테이블 구조:
    - naver_news: 뉴스 메타데이터 (title, pub_date)
    - crawled_news: 뉴스 본문 (text = summary)
    - filtered_news: 주식 관련 뉴스만 남긴 후처리 결과
    - crawled_news.news_id -> naver_news.news_id (FK, 1:1)

API 엔드포인트:
    GET /api/news/simple  -> 최근 주식 뉴스 목록 (앱용, pub_date 기준 정렬)
    GET /api/news/{id}    -> 뉴스 상세 조회

==============================================================================
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from google import genai
from google.genai import types
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.models import (
    NaverNews,
    CrawledNews,
    ProcessStatus,
    StockSummaryCache,
    NewsStockMapping,
    FilteredNews,
    RecommendationServe,
    User,
    Stock,
    Alias,
    Keyword,
    NewsKeywordMapping,
)
from app.schemas import (
    NewsSimpleResponse,
    NewsDetailResponse,
    StockSummaryResponse,
    NewsRecommendationItem,
    NewsRecommendationResponse,
)
from app.config import get_settings
from app.kis.errors import KISError
from app.routers.stocks import _fetch_stock_overview
from app.recommender.client import RecommendationCandidate
from app.routers.users import get_current_user


router = APIRouter(
    prefix="/api/news",
    tags=["news"],
)

settings = get_settings()
logger = logging.getLogger(__name__)
gemini_client = genai.Client(api_key=settings.gemini_api)

_CURSOR_VERSION = 1
_RECOMMENDATION_PAGE_SIZE = 20
_RECENT_RECOMMENDATION_SOURCE = "recent_news"
_ON_DEMAND_EXTRACTOR_VERSION = "backend_gemini_v1"
_GEMINI_MODEL_NAME = "gemini-2.0-flash-lite"
_GEMINI_TEMPERATURE = 0.3
_GEMINI_MAX_RETRIES = 4
_GEMINI_VALIDATION_RETRIES = 2
_VALID_SENTIMENTS = {"긍정", "중립", "부정"}
_GEMINI_SYSTEM_PROMPT = """
너는 금융 뉴스 데이터 분석 전문가야. 기사 내용을 분석해서 투자 정보를 추출해.

[핵심 작업 절차]
1. **Full Scan**: 기사의 첫 문장부터 마지막 문장까지 **한 글자도 빠뜨리지 말고 정독**해.
2. **Event Check**: 기사 안에는 서로 다른 여러 기업의 소식이 나열되어 있을 수 있다. (예: 특징주 모음, 섹터 결산 등)
3. **Selection**: 각 기업별로 **'구체적인 사건(신제품, 실적, 급등락, 공시 등)'이 서술된 경우**에만 추출해.

[상세 규칙]
1. related_stocks:
   기업이 아래 **3가지 카테고리 중 하나 이상**에 해당하면 무조건 추출해.

   **(A) 비즈니스/재무/영업 (Business & Sales)**
     - 실적, 계약, M&A, 공시.
     - **신규 서비스/제품 출시, 대규모 마케팅.**
     - **전시회 참가(CES, TGS, 지스타 등), 신작 공개/시연, 베타테스트(CBT/OBT) 진행.**
       (이유: 게임/IT 기업의 경우, 신작에 대한 '기대감'이나 '공개 행사' 자체가 중요한 투자 재료임. 기자가 '체험해봤다'는 형식의 기사라도 신작 공개가 핵심이면 추출할 것.)

   **(B) ESG/사회공헌/협력 (Cooperation & ESG)**
     - 업무협약(MOU), 제휴, 정부 지원사업 참여, 기부, 상생 활동.

   **(C) 리스크/사건사고 (Risk & Issue)**
     - 수사, 규제, 소송, 해킹, 화재, 횡령.
     - 기업 인프라 악용, 보안 사고, 서비스 장애 등 관리 책임 이슈.
     - 기업의 대응(해명, 사과 등)이 포함된 경우.

   **[제외 기준]**
   - 단순히 비교 대상으로 언급된 경쟁사.
   - 기사의 핵심 사건과 직접적인 관련이 없는 단순 배경 기업.

2. keywords:
   - 기사의 길이와 정보량에 따라 **핵심 명사(Noun)를 3개에서 6개 사이**로 유동적으로 추출해.
   - 내용이 짧거나 단순하면 3개만, 복잡하고 중요하면 최대 6개까지 추출.
   - (중요) 개수를 맞추기 위해 불필요한 단어를 억지로 포함하지 말 것.

3. summary: 기사를 한 줄로 요약.
4. sentiment: 주가에 미칠 영향 (긍정/부정/중립).

반드시 JSON 형식으로만 응답해. 잡담하지 마.
""".strip()

# =============================================================================
# 헬퍼 함수
# =============================================================================


def decode_html_entities(text: str | None) -> str | None:
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


def _normalize_whitespace(text: str | None) -> str:
    if not text:
        return ""
    cleaned = decode_html_entities(text) or ""
    lines = [" ".join(line.split()) for line in cleaned.replace("\r", "\n").split("\n")]
    filtered = [line for line in lines if line]
    return "\n".join(filtered).strip()


def _preview_text(text: str | None, limit: int = 180) -> str | None:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _fallback_summary(text: str | None) -> str:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return "요약 생성 실패"
    return normalized[:220]


def _format_change_rate(change_rate: float | None) -> str | None:
    if change_rate is None:
        return None
    return f"{change_rate:+.1f}%"


def _stock_up_from_change_rate(change_rate: float | None) -> bool | None:
    if change_rate is None:
        return None
    return change_rate >= 0


def _path_from_stock_up(stock_up: bool | None) -> str:
    return "A2" if stock_up is False else "A1"


def _encode_recommendation_cursor(*, page: int, offset: int, limit: int) -> str:
    payload = {"v": _CURSOR_VERSION, "page": page, "offset": offset, "limit": limit}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_recommendation_cursor(cursor: str) -> tuple[int, int, int | None]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor format") from exc

    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise HTTPException(status_code=400, detail="Invalid cursor payload")

    try:
        page = int(payload.get("page"))
        offset = int(payload.get("offset"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor values") from exc

    raw_limit = payload.get("limit")
    cursor_limit: int | None = None
    if raw_limit is not None:
        try:
            cursor_limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor values") from exc

    if page < 1 or offset < 0 or (cursor_limit is not None and cursor_limit < 1):
        raise HTTPException(status_code=400, detail="Invalid cursor values")

    return page, offset, cursor_limit


def _default_recommendation_path(source: str) -> str:
    path_map = {
        "recommender": "A1",
        "mock": "M1",
        "mock_fallback": "M2",
        _RECENT_RECOMMENDATION_SOURCE: "A1",
    }
    return path_map.get(source, "A1")


# 새로운 요약문을 생성하는 함수
async def call_gemini_summary(stock_name: str, num_article: int, text_combined: str) -> str | None:
    summary_length = "2줄" if num_article <= 5 else "3줄"
    system_prompt = f"""
    당신은 모바일 증권 앱의 AI 뉴스 요약 봇입니다.
    사용자가 스마트폰으로 한눈에 볼 수 있도록, 아래 제공된 {num_article}개의 기사 요약문을 **모두 하나로 통합하여** '{stock_name}'의 전체 핵심 이슈를 **단 {summary_length}**로 압축 요약하세요.

    [작성 규칙]
    1. 전체 기사 요약문을 아우르는 최종 {summary_length}만 출력할 것.
    2. 서술형 줄글(~했습니다)은 금지하고, 뉴스 헤드라인처럼 핵심 단어(명사형) 위주로 끝맺음할 것.
    3. 각 줄은 '- ' 기호로 시작할 것.
    4. 한 줄의 길이는 40자를 넘지 않을 것.
    5. 제목이나 인사말 없이 결과물만 바로 출력할 것.
    """
    try:
        response = await gemini_client.aio.models.generate_content(
            model=_GEMINI_MODEL_NAME,
            contents=text_combined,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception:
        logger.exception("stock summary generation failed: %s", stock_name)
        return None


def _extract_valid_analysis(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    summary = payload.get("summary")
    sentiment = payload.get("sentiment")
    if not isinstance(summary, str):
        summary = ""
    if not isinstance(sentiment, str):
        sentiment = ""
    summary = summary.strip()
    sentiment = sentiment.strip()
    if sentiment not in _VALID_SENTIMENTS:
        sentiment = ""
    return summary, sentiment


def _normalize_keywords(raw_keywords: object) -> list[str]:
    if not isinstance(raw_keywords, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_keyword in raw_keywords:
        keyword = str(raw_keyword).strip()
        if not keyword:
            continue
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(keyword)
        if len(normalized) >= 6:
            break
    return normalized


def _normalize_related_stocks(raw_stocks: object) -> list[str]:
    if not isinstance(raw_stocks, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_stock in raw_stocks:
        if isinstance(raw_stock, dict) and raw_stock:
            raw_stock = next(iter(raw_stock.values()))
        name = str(raw_stock).strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(name)
    return normalized


async def _call_article_gemini(text: str) -> dict | None:
    for attempt in range(_GEMINI_MAX_RETRIES):
        try:
            response = await gemini_client.aio.models.generate_content(
                model=_GEMINI_MODEL_NAME,
                contents=text,
                config=types.GenerateContentConfig(
                    temperature=_GEMINI_TEMPERATURE,
                    system_instruction=_GEMINI_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )
            response_text = (response.text or "").strip()
            if not response_text:
                raise ValueError("empty Gemini response")
            payload = json.loads(response_text)
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            if isinstance(payload, dict):
                return payload
            raise ValueError("invalid Gemini response payload")
        except Exception as exc:
            logger.warning("article Gemini call failed (%s/%s): %s", attempt + 1, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES - 1:
                await asyncio.sleep(4**attempt)
    return None


async def _analyze_article_with_retry(text: str) -> dict:
    normalized_text = _normalize_whitespace(text)
    if not normalized_text or not settings.gemini_api.strip():
        return {
            "summary": _fallback_summary(normalized_text),
            "sentiment": "중립",
            "keywords": [],
            "related_stocks": [],
        }

    total_attempts = max(1, _GEMINI_VALIDATION_RETRIES + 1)
    last_payload: dict | None = None
    had_response = False

    for attempt in range(total_attempts):
        payload = await _call_article_gemini(normalized_text)
        if not payload:
            continue
        had_response = True
        last_payload = payload
        summary, sentiment = _extract_valid_analysis(payload)
        if summary and sentiment:
            return {
                "summary": summary,
                "sentiment": sentiment,
                "keywords": _normalize_keywords(payload.get("keywords")),
                "related_stocks": _normalize_related_stocks(payload.get("related_stocks")),
            }
        if attempt < total_attempts - 1:
            logger.warning("invalid Gemini analysis format. retrying (%s/%s)", attempt + 1, total_attempts - 1)
            await asyncio.sleep(1.0)

    fallback_summary = _fallback_summary(normalized_text)
    if had_response and last_payload:
        return {
            "summary": fallback_summary,
            "sentiment": "중립",
            "keywords": _normalize_keywords(last_payload.get("keywords")),
            "related_stocks": _normalize_related_stocks(last_payload.get("related_stocks")),
        }
    return {
        "summary": fallback_summary,
        "sentiment": "중립",
        "keywords": [],
        "related_stocks": [],
    }


async def _fetch_change_rate_safe(stock_id: str) -> float | None:
    try:
        overview = await _fetch_stock_overview(stock_id)
    except KISError as exc:
        logger.warning("stock overview fetch failed for %s: %s", stock_id, exc)
        return None
    except Exception as exc:
        logger.warning("unexpected stock overview error for %s: %s", stock_id, exc)
        return None

    raw_change_rate = overview.get("change_rate")
    if raw_change_rate is None:
        return None
    try:
        return float(raw_change_rate)
    except (TypeError, ValueError):
        return None


async def _load_change_rates(stock_ids: list[str]) -> dict[str, float | None]:
    unique_stock_ids = list(dict.fromkeys(stock_id for stock_id in stock_ids if stock_id))
    if not unique_stock_ids:
        return {}

    semaphore = asyncio.Semaphore(4)

    async def _bounded_fetch(stock_id: str) -> tuple[str, float | None]:
        async with semaphore:
            return stock_id, await _fetch_change_rate_safe(stock_id)

    results = await asyncio.gather(*[_bounded_fetch(stock_id) for stock_id in unique_stock_ids])
    return {stock_id: change_rate for stock_id, change_rate in results}


async def _select_top_stock_rows(
    db: AsyncSession,
    news_ids: list[int],
) -> dict[int, dict[str, str | float | datetime | None]]:
    if not news_ids:
        return {}

    stmt = (
        select(
            NewsStockMapping.news_id,
            NewsStockMapping.stock_id,
            Stock.stock_name,
            NewsStockMapping.weight,
            NewsStockMapping.created_at,
            NewsStockMapping.mapping_id,
        )
        .join(Stock, NewsStockMapping.stock_id == Stock.stock_id)
        .where(NewsStockMapping.news_id.in_(news_ids))
        .order_by(
            NewsStockMapping.news_id,
            desc(func.coalesce(NewsStockMapping.weight, 1.0)),
            desc(NewsStockMapping.created_at),
            desc(NewsStockMapping.mapping_id),
        )
    )
    result = await db.execute(stmt)

    top_rows: dict[int, dict[str, str | float | datetime | None]] = {}
    for row in result.all():
        if row.news_id in top_rows:
            continue
        top_rows[int(row.news_id)] = {
            "stock_id": row.stock_id,
            "stock_name": row.stock_name,
            "weight": row.weight,
            "created_at": row.created_at,
        }
    return top_rows


async def _get_keywords_for_news(db: AsyncSession, news_id: int, limit: int = 5) -> list[str]:
    stmt = (
        select(Keyword.word)
        .join(NewsKeywordMapping, NewsKeywordMapping.keyword_id == Keyword.keyword_id)
        .where(NewsKeywordMapping.news_id == news_id)
        .order_by(
            desc(func.coalesce(NewsKeywordMapping.weight, 1.0)),
            desc(NewsKeywordMapping.created_at),
            desc(NewsKeywordMapping.mapping_id),
        )
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [word for (word,) in result.all() if word]


async def _resolve_related_stocks(
    db: AsyncSession,
    related_stock_names: list[str],
) -> list[tuple[str, str]]:
    if not related_stock_names:
        return []

    normalized_names = [name.strip() for name in related_stock_names if name.strip()]
    lowered_names = list(dict.fromkeys(name.lower() for name in normalized_names))
    if not lowered_names:
        return []

    stock_stmt = (
        select(Stock.stock_id, Stock.stock_name)
        .where(func.lower(Stock.stock_name).in_(lowered_names))
    )
    alias_stmt = (
        select(Alias.alias_name, Stock.stock_id, Stock.stock_name)
        .join(Stock, Alias.stock_id == Stock.stock_id)
        .where(func.lower(Alias.alias_name).in_(lowered_names))
    )
    stock_rows = (await db.execute(stock_stmt)).all()
    alias_rows = (await db.execute(alias_stmt)).all()

    stock_name_map = {
        (stock_name or "").strip().lower(): (stock_id, stock_name or stock_id)
        for stock_id, stock_name in stock_rows
        if stock_name
    }
    alias_name_map = {
        (alias_name or "").strip().lower(): (stock_id, stock_name or stock_id)
        for alias_name, stock_id, stock_name in alias_rows
        if alias_name
    }

    resolved: list[tuple[str, str]] = []
    seen_stock_ids: set[str] = set()
    for raw_name in normalized_names:
        lowered = raw_name.lower()
        match = stock_name_map.get(lowered) or alias_name_map.get(lowered)
        if not match or match[0] in seen_stock_ids:
            continue
        seen_stock_ids.add(match[0])
        resolved.append(match)
    return resolved


async def _persist_article_enrichment(
    db: AsyncSession,
    *,
    news_id: int,
    article_text: str | None,
    summary: str | None,
    sentiment: str | None,
    generated_keywords: list[str],
    related_stocks: list[tuple[str, str]],
    should_insert_keywords: bool,
    should_insert_stocks: bool,
) -> None:
    filtered_stmt = select(FilteredNews).where(FilteredNews.news_id == news_id)
    filtered = (await db.execute(filtered_stmt)).scalar_one_or_none()

    if filtered is None:
        filtered = FilteredNews(
            news_id=news_id,
            refined_text=article_text or None,
            summary=summary,
            sentiment=sentiment,
        )
        db.add(filtered)
        await db.flush()
    else:
        if article_text and not filtered.refined_text:
            filtered.refined_text = article_text
        if summary and not filtered.summary:
            filtered.summary = summary
        if sentiment and not filtered.sentiment:
            filtered.sentiment = sentiment
        await db.flush()

    if should_insert_keywords and generated_keywords:
        existing_keyword_stmt = select(Keyword).where(Keyword.word.in_(generated_keywords))
        existing_keywords = {
            keyword.word: keyword
            for keyword in (await db.execute(existing_keyword_stmt)).scalars().all()
        }

        for word in generated_keywords:
            if word in existing_keywords:
                continue
            keyword = Keyword(word=word)
            db.add(keyword)
            await db.flush()
            existing_keywords[word] = keyword

        existing_mapping_ids = {
            keyword_id
            for (keyword_id,) in (
                await db.execute(
                    select(NewsKeywordMapping.keyword_id).where(NewsKeywordMapping.news_id == news_id)
                )
            ).all()
        }
        total_keywords = len(generated_keywords)
        for index, word in enumerate(generated_keywords):
            keyword = existing_keywords.get(word)
            if keyword is None or keyword.keyword_id in existing_mapping_ids:
                continue
            db.add(
                NewsKeywordMapping(
                    news_id=news_id,
                    keyword_id=keyword.keyword_id,
                    extractor_version=_ON_DEMAND_EXTRACTOR_VERSION,
                    weight=float(total_keywords - index),
                )
            )

    if should_insert_stocks and related_stocks:
        existing_stock_ids = {
            stock_id
            for (stock_id,) in (
                await db.execute(
                    select(NewsStockMapping.stock_id).where(NewsStockMapping.news_id == news_id)
                )
            ).all()
        }
        total_stocks = len(related_stocks)
        for index, (stock_id, _) in enumerate(related_stocks):
            if stock_id in existing_stock_ids:
                continue
            db.add(
                NewsStockMapping(
                    news_id=news_id,
                    stock_id=stock_id,
                    extractor_version=_ON_DEMAND_EXTRACTOR_VERSION,
                    weight=float(total_stocks - index),
                )
            )

    await db.commit()


async def _build_recommendation_items(
    db: AsyncSession,
    candidates: list[RecommendationCandidate],
    source: str,
) -> list[NewsRecommendationItem]:
    if not candidates:
        return []

    news_ids = [candidate.news_id for candidate in candidates]
    news_stmt = select(NaverNews).where(NaverNews.news_id.in_(news_ids))
    crawled_stmt = select(CrawledNews.news_id, CrawledNews.text).where(CrawledNews.news_id.in_(news_ids))
    filtered_stmt = select(FilteredNews.news_id, FilteredNews.summary).where(FilteredNews.news_id.in_(news_ids))

    news_rows = (await db.execute(news_stmt)).scalars().all()
    crawled_rows = (await db.execute(crawled_stmt)).all()
    filtered_rows = (await db.execute(filtered_stmt)).all()

    news_map = {int(row.news_id): row for row in news_rows}
    crawled_map = {int(news_id): text for news_id, text in crawled_rows}
    filtered_map = {int(news_id): summary for news_id, summary in filtered_rows}

    top_stock_map = await _select_top_stock_rows(db, news_ids)
    change_rate_map = await _load_change_rates(
        [str(payload["stock_id"]) for payload in top_stock_map.values() if payload.get("stock_id")]
    )

    items: list[NewsRecommendationItem] = []
    for candidate in candidates:
        news = news_map.get(candidate.news_id)
        if news is None:
            continue

        top_stock = top_stock_map.get(candidate.news_id)
        stock_id = str(top_stock["stock_id"]) if top_stock and top_stock.get("stock_id") else None
        change_rate = change_rate_map.get(stock_id) if stock_id else None
        stock_up = _stock_up_from_change_rate(change_rate)

        summary = filtered_map.get(candidate.news_id) or crawled_map.get(candidate.news_id)
        items.append(
            NewsRecommendationItem(
                news_id=int(news.news_id),
                title=decode_html_entities(news.title) or "",
                summary=_preview_text(summary),
                pub_date=news.pub_date,
                path=candidate.path or _path_from_stock_up(stock_up) or _default_recommendation_path(source),
                stock_name=str(top_stock["stock_name"]) if top_stock and top_stock.get("stock_name") else None,
                stock_change=_format_change_rate(change_rate),
                stock_up=stock_up,
            )
        )
    return items


async def _recent_candidates_from_db_with_offset(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> list[RecommendationCandidate]:
    stmt = (
        select(FilteredNews.news_id)
        .select_from(FilteredNews)
        .join(NaverNews, FilteredNews.news_id == NaverNews.news_id)
        .join(CrawledNews, NaverNews.news_id == CrawledNews.news_id)
        .where(NaverNews.crawl_status == ProcessStatus.crawl_success)
        .order_by(desc(NaverNews.pub_date), desc(NaverNews.news_id))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [RecommendationCandidate(news_id=int(news_id)) for (news_id,) in result.all()]


async def _log_recommendation_serve(
    db: AsyncSession,
    *,
    user_id: int,
    request_id: str,
    page: int,
    limit: int,
    screen_session_id: str | None,
    app_session_id: str | None,
    source: str,
    candidates: list[RecommendationCandidate],
) -> bool:
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
        is_duplicate = (
            ("unique" in error_text or "duplicate" in error_text)
            and (
                "uq_recommendation_serves_request_page" in error_text
                or ("recommendation_serves" in error_text and "request_id" in error_text and "page" in error_text)
            )
        )
        if is_duplicate:
            logger.info("recommendation serve duplicate skipped: request_id=%s page=%s", request_id, page)
            return False
        raise


# =============================================================================
# 뉴스 목록 조회 API (Flutter 앱용)
# =============================================================================
#
# URL: GET /api/news/simple
# 용도: Flutter 앱의 뉴스 목록 화면
#
# 동작:
#   1. filtered_news에 존재하는 주식 관련 뉴스만 대상으로 삼기
#   2. naver_news.pub_date 기준 최신순 정렬
#   3. crawled_news / filtered_news와 조인하여 요약 정보 구성
#
@router.get("/simple", response_model=list[NewsSimpleResponse])
async def get_news_simple_list(
    limit: int = Query(20, ge=1, le=100, description="가져올 뉴스 개수"),
    search: str | None = Query(None, description="검색어"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(NaverNews, CrawledNews.text, FilteredNews.summary)
        .select_from(FilteredNews)
        .join(NaverNews, FilteredNews.news_id == NaverNews.news_id)
        .join(CrawledNews, NaverNews.news_id == CrawledNews.news_id)
        .where(NaverNews.crawl_status == ProcessStatus.crawl_success)
        .order_by(desc(NaverNews.pub_date), desc(NaverNews.news_id))
        .limit(limit)
    )
    if search:
        query = query.where(NaverNews.title.ilike(f"%{search}%"))

    result = await db.execute(query)
    news_rows = result.all()
    return [
        NewsSimpleResponse(
            news_id=int(news.news_id),
            title=decode_html_entities(news.title) or "",
            summary=_preview_text(filtered_summary or crawled_text),
            pub_date=news.pub_date,
        )
        for news, crawled_text, filtered_summary in news_rows
    ]


# =============================================================================
# 추천 뉴스 조회 API
# =============================================================================
#
# URL: GET /api/news/recommendations
#
@router.get("/recommendations", response_model=NewsRecommendationResponse)
async def get_news_recommendations(
    user_id: int | None = Query(None, ge=1, description="호환용 사용자 ID"),
    limit: int = Query(20, ge=1, le=100, description="호환용 파라미터"),
    page: int = Query(1, ge=1, le=1000),
    cursor: str | None = Query(None, max_length=512),
    request_id: str | None = Query(None, max_length=128),
    screen_session_id: str | None = Query(None, max_length=64),
    app_session_id: str | None = Query(None, max_length=255),
    log_served: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user_id is not None and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="user_id does not match authenticated user")

    effective_limit = _RECOMMENDATION_PAGE_SIZE
    resolved_page = page
    offset = (resolved_page - 1) * effective_limit
    if cursor:
        resolved_page, offset, cursor_limit = _decode_recommendation_cursor(cursor)
        if cursor_limit is not None and cursor_limit != effective_limit:
            raise HTTPException(status_code=400, detail="Unsupported cursor limit")

    candidates = await _recent_candidates_from_db_with_offset(db, limit=effective_limit, offset=offset)
    items = await _build_recommendation_items(db, candidates, _RECENT_RECOMMENDATION_SOURCE)

    resolved_request_id = request_id or f"req-{uuid4().hex}"
    logged = False
    served_candidates = [
        RecommendationCandidate(news_id=item.news_id, path=item.path)
        for item in items
    ]
    if log_served:
        try:
            logged = await _log_recommendation_serve(
                db,
                user_id=current_user.id,
                request_id=resolved_request_id,
                page=resolved_page,
                limit=effective_limit,
                screen_session_id=screen_session_id,
                app_session_id=app_session_id,
                source=_RECENT_RECOMMENDATION_SOURCE,
                candidates=served_candidates,
            )
        except Exception as exc:
            await db.rollback()
            logger.warning("recommendation serve logging failed: %s", exc)

    next_cursor: str | None = None
    if len(items) == effective_limit:
        next_cursor = _encode_recommendation_cursor(
            page=resolved_page + 1,
            offset=offset + len(items),
            limit=effective_limit,
        )

    return NewsRecommendationResponse(
        user_id=current_user.id,
        request_id=resolved_request_id,
        source=_RECENT_RECOMMENDATION_SOURCE,
        page=resolved_page,
        next_cursor=next_cursor,
        served_count=len(items),
        logged=logged,
        items=items,
    )


# =============================================================================
# 뉴스 통계 API
# =============================================================================
#
# URL: GET /api/news/stats/summary
#
@router.get("/stats/summary")
async def get_news_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total_result = await db.execute(select(func.count(NaverNews.news_id)))
    crawled_result = await db.execute(select(func.count(CrawledNews.crawled_news_id)))
    return {
        "total_naver_news": total_result.scalar() or 0,
        "total_crawled_news": crawled_result.scalar() or 0,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(StockSummaryCache).where(StockSummaryCache.stock_name == stock_name)
    cache = (await db.execute(stmt)).scalar_one_or_none()
    if not cache:
        raise HTTPException(status_code=404, detail="해당 종목명은 존재하지 않습니다.")

    news_stmt = (
        select(NewsStockMapping.news_id)
        .where(NewsStockMapping.stock_id == cache.stock_id)
        .order_by(desc(NewsStockMapping.created_at))
        .limit(10)
    )
    target_news_ids = [row[0] for row in (await db.execute(news_stmt)).fetchall()]
    if not target_news_ids:
        return StockSummaryResponse(
            stock_name=stock_name,
            summary=f"{stock_name} 종목에 관련된 최신 뉴스가 없습니다.",
            last_updated=cache.created_at or datetime.now(timezone.utc),
            message="관련 뉴스가 존재하지 않습니다.",
        )

    latest_news_id = target_news_ids[0]
    if cache.latest_news_id == latest_news_id and cache.summary_text:
        return StockSummaryResponse(
            stock_name=stock_name,
            summary=cache.summary_text,
            last_updated=cache.created_at,
            message="기존 요약문을 가져왔습니다.",
        )

    content_stmt = select(FilteredNews.summary).where(FilteredNews.news_id.in_(target_news_ids))
    news_summaries = [row[0] for row in (await db.execute(content_stmt)).fetchall() if row[0]]
    combined_text = "\n\n".join([f"### [기사 {i + 1}]\n{s}" for i, s in enumerate(news_summaries)])
    new_summary = await call_gemini_summary(stock_name, len(news_summaries), combined_text)

    if new_summary:
        cache.latest_news_id = latest_news_id
        cache.summary_text = new_summary
        cache.created_at = datetime.now(timezone.utc)
        await db.commit()
        return StockSummaryResponse(
            stock_name=stock_name,
            summary=new_summary,
            last_updated=cache.created_at,
            message="새로운 요약문을 생성했습니다.",
        )

    return StockSummaryResponse(
        stock_name=stock_name,
        summary=cache.summary_text or "요약 생성에 실패했습니다.",
        last_updated=cache.created_at,
        message="요약 생성에 실패하여 기존 데이터를 반환합니다.",
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(NaverNews)
        .options(joinedload(NaverNews.crawled_news))
        .where(NaverNews.news_id == news_id)
    )
    news = (await db.execute(query)).scalar_one_or_none()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")

    filtered = (
        await db.execute(select(FilteredNews).where(FilteredNews.news_id == news_id))
    ).scalar_one_or_none()

    article_text = _normalize_whitespace(
        filtered.refined_text if filtered and filtered.refined_text else news.crawled_news.text if news.crawled_news else None
    )
    existing_keywords = await _get_keywords_for_news(db, news_id)
    existing_top_stock_map = await _select_top_stock_rows(db, [news_id])
    existing_top_stock = existing_top_stock_map.get(news_id)

    missing_summary = not filtered or not (filtered.summary or "").strip()
    missing_sentiment = not filtered or not (filtered.sentiment or "").strip()
    missing_keywords = len(existing_keywords) == 0
    missing_stocks = existing_top_stock is None

    if missing_summary or missing_sentiment or missing_keywords or missing_stocks:
        analysis = await _analyze_article_with_retry(article_text)
        related_stocks = await _resolve_related_stocks(db, analysis["related_stocks"]) if missing_stocks else []
        try:
            await _persist_article_enrichment(
                db,
                news_id=news_id,
                article_text=article_text or None,
                summary=analysis.get("summary"),
                sentiment=analysis.get("sentiment"),
                generated_keywords=analysis.get("keywords") or [],
                related_stocks=related_stocks,
                should_insert_keywords=missing_keywords,
                should_insert_stocks=missing_stocks,
            )
        except Exception:
            await db.rollback()
            logger.exception("on-demand news enrichment failed: news_id=%s", news_id)

        filtered = (
            await db.execute(select(FilteredNews).where(FilteredNews.news_id == news_id))
        ).scalar_one_or_none()
        existing_keywords = await _get_keywords_for_news(db, news_id)
        existing_top_stock_map = await _select_top_stock_rows(db, [news_id])
        existing_top_stock = existing_top_stock_map.get(news_id)

    stock_id = str(existing_top_stock["stock_id"]) if existing_top_stock and existing_top_stock.get("stock_id") else None
    change_rate = await _fetch_change_rate_safe(stock_id) if stock_id else None
    body = _normalize_whitespace(filtered.refined_text if filtered and filtered.refined_text else news.crawled_news.text if news.crawled_news else None)
    summary = decode_html_entities(filtered.summary) if filtered and filtered.summary else _fallback_summary(body)
    sentiment = decode_html_entities(filtered.sentiment) if filtered and filtered.sentiment else None
    stock_up = _stock_up_from_change_rate(change_rate)

    return NewsDetailResponse(
        news_id=int(news.news_id),
        title=decode_html_entities(news.title) or "",
        summary=summary,
        body=body or None,
        pub_date=news.pub_date,
        url=news.url,
        sentiment=sentiment,
        keywords=existing_keywords,
        stock_name=str(existing_top_stock["stock_name"]) if existing_top_stock and existing_top_stock.get("stock_name") else None,
        stock_change=_format_change_rate(change_rate),
        stock_up=stock_up,
    )
