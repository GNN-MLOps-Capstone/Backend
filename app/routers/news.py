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
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import bindparam, desc, func, select
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
    NewsDomainMapping,
    RecommendationServe,
    RecommendationNewsPathMetrics,
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
    TopDwellStockResponse,
    TopDwellKeywordResponse,
    RecentTrendingStockResponse,
    RecentTrendingKeywordResponse,
)
from app.kis.errors import KISError
from app.recommender.client import RecommendationCandidate
from app.routers.users import get_current_user
from app.services.stock_service import fetch_stock_overview
from app.services.news_enrichment_service import analyze_article, generate_stock_summary
from app.utils.keyword_filters import TRENDING_KEYWORD_EXCLUDES
from app.utils.text import decode_html_entities, escape_sql_like_wildcards


router = APIRouter(
    prefix="/api/news",
    tags=["news"],
)

logger = logging.getLogger(__name__)

_CURSOR_VERSION = 1
_RECOMMENDATION_PAGE_SIZE = 20
_RECENT_RECOMMENDATION_SOURCE = "recent_news"
_ON_DEMAND_EXTRACTOR_VERSION = "backend_gemini_v1"

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


def _first_non_empty_text(*values: str | None) -> str | None:
    for value in values:
        normalized = _normalize_whitespace(value)
        if normalized:
            return normalized
    return None


def _format_change_rate(change_rate: float | None) -> str | None:
    if change_rate is None:
        return None
    return f"{change_rate:+.1f}%"


def _stock_up_from_change_rate(change_rate: float | None) -> bool | None:
    if change_rate is None:
        return None
    return change_rate >= 0


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


def _normalize_news_domain(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url if "://" in url else f"//{url}")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return None
    return hostname


def _domain_candidates_for_lookup(domain: str | None) -> tuple[str, ...]:
    if not domain:
        return ()
    if domain.startswith("www."):
        stripped = domain[4:]
        if stripped:
            return (domain, stripped)
    return (domain,)


async def _select_news_company_name_map(
    db: AsyncSession,
    news_map: dict[int, NaverNews],
) -> dict[int, str]:
    if not news_map:
        return {}

    domains_by_news_id = {
        news_id: _normalize_news_domain(news.url)
        for news_id, news in news_map.items()
    }
    lookup_domains = list(
        dict.fromkeys(
            candidate
            for domain in domains_by_news_id.values()
            for candidate in _domain_candidates_for_lookup(domain)
        )
    )
    if not lookup_domains:
        return {}

    stmt = select(
        NewsDomainMapping.domain,
        NewsDomainMapping.news_company_name,
    ).where(NewsDomainMapping.domain.in_(lookup_domains))
    rows = (await db.execute(stmt)).all()
    company_name_by_domain = {
        str(domain).strip().lower(): news_company_name
        for domain, news_company_name in rows
        if domain and news_company_name
    }

    news_company_name_map: dict[int, str] = {}
    for news_id, domain in domains_by_news_id.items():
        for candidate_domain in _domain_candidates_for_lookup(domain):
            company_name = company_name_by_domain.get(candidate_domain)
            if company_name:
                news_company_name_map[news_id] = company_name
                break
    return news_company_name_map

async def _fetch_change_rate_safe(stock_id: str) -> float | None:
    try:
        overview = await fetch_stock_overview(stock_id)
    except KISError as exc:
        logger.warning("stock overview fetch failed for %s: %s", stock_id, exc)
        return None
    except Exception as exc:
        logger.warning(
            "unexpected stock overview error for %s: %s",
            stock_id,
            exc,
            exc_info=True,
        )
        return None

    raw_change_rate = overview.get("change_rate")
    if raw_change_rate is None:
        return None
    try:
        return float(raw_change_rate)
    except (TypeError, ValueError):
        return None


async def _fetch_change_rates_for_stock_ids(stock_ids: list[str]) -> dict[str, float | None]:
    unique_stock_ids = list(
        dict.fromkeys(
            stock_id.strip()
            for stock_id in stock_ids
            if stock_id and stock_id.strip()
        )
    )
    if not unique_stock_ids:
        return {}

    change_rates = await asyncio.gather(
        *(_fetch_change_rate_safe(stock_id) for stock_id in unique_stock_ids)
    )
    return {
        stock_id: change_rate
        for stock_id, change_rate in zip(unique_stock_ids, change_rates, strict=True)
    }


async def _select_stock_rows(
    db: AsyncSession,
    news_ids: list[int],
) -> dict[int, list[dict[str, str | datetime | None]]]:
    if not news_ids:
        return {}

    stmt = (
        select(
            NewsStockMapping.news_id,
            NewsStockMapping.stock_id,
            func.coalesce(StockSummaryCache.stock_name, Stock.stock_name).label("stock_name"),
            NewsStockMapping.created_at,
            NewsStockMapping.mapping_id,
        )
        .join(Stock, NewsStockMapping.stock_id == Stock.stock_id)
        .outerjoin(StockSummaryCache, NewsStockMapping.stock_id == StockSummaryCache.stock_id)
        .where(NewsStockMapping.news_id.in_(news_ids))
        .order_by(
            NewsStockMapping.news_id,
            desc(func.coalesce(NewsStockMapping.weight, 1.0)),
            desc(NewsStockMapping.created_at),
            desc(NewsStockMapping.mapping_id),
        )
    )
    result = await db.execute(stmt)

    stock_rows: dict[int, list[dict[str, str | datetime | None]]] = {}
    for row in result.all():
        stock_rows.setdefault(int(row.news_id), []).append(
            {
                "stock_id": row.stock_id,
                "stock_name": row.stock_name,
                "created_at": row.created_at,
            }
        )
    return stock_rows


async def _select_top_stock_row_per_news(
    db: AsyncSession,
    news_ids: list[int],
) -> dict[int, dict[str, str | datetime | None]]:
    if not news_ids:
        return {}

    ranked_stock_rows = (
        select(
            NewsStockMapping.news_id.label("news_id"),
            NewsStockMapping.stock_id.label("stock_id"),
            func.coalesce(StockSummaryCache.stock_name, Stock.stock_name).label("stock_name"),
            NewsStockMapping.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=NewsStockMapping.news_id,
                order_by=(
                    desc(func.coalesce(NewsStockMapping.weight, 1.0)),
                    desc(NewsStockMapping.created_at),
                    desc(NewsStockMapping.mapping_id),
                ),
            )
            .label("row_num"),
        )
        .join(Stock, NewsStockMapping.stock_id == Stock.stock_id)
        .outerjoin(StockSummaryCache, NewsStockMapping.stock_id == StockSummaryCache.stock_id)
        .where(NewsStockMapping.news_id.in_(news_ids))
        .subquery()
    )
    stmt = (
        select(
            ranked_stock_rows.c.news_id,
            ranked_stock_rows.c.stock_id,
            ranked_stock_rows.c.stock_name,
            ranked_stock_rows.c.created_at,
        )
        .where(ranked_stock_rows.c.row_num == 1)
        .order_by(ranked_stock_rows.c.news_id)
    )
    result = await db.execute(stmt)
    return {
        int(row.news_id): {
            "stock_id": row.stock_id,
            "stock_name": row.stock_name,
            "created_at": row.created_at,
        }
        for row in result.all()
    }


def _build_related_stock_payloads(
    stock_rows: list[dict[str, str | datetime | None]],
    change_rate_map: dict[str, float | None],
) -> list[dict[str, str | bool | None]]:
    payloads: list[dict[str, str | bool | None]] = []
    seen_stock_ids: set[str] = set()
    for row in stock_rows:
        stock_id = str(row.get("stock_id") or "").strip()
        stock_name = str(row.get("stock_name") or stock_id).strip()
        if not stock_id or not stock_name or stock_id in seen_stock_ids:
            continue
        seen_stock_ids.add(stock_id)
        change_rate = change_rate_map.get(stock_id)
        payloads.append(
            {
                "stock_id": stock_id,
                "stock_name": stock_name,
                "stock_change": _format_change_rate(change_rate),
                "stock_up": _stock_up_from_change_rate(change_rate),
            }
        )
    return payloads


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
    alias_name_map: dict[str, tuple[str, str]] = {}
    ambiguous_aliases: set[str] = set()
    for alias_name, stock_id, stock_name in alias_rows:
        normalized_alias = (alias_name or "").strip().lower()
        if not normalized_alias or normalized_alias in ambiguous_aliases:
            continue
        match = (stock_id, stock_name or stock_id)
        existing = alias_name_map.get(normalized_alias)
        if existing and existing[0] != stock_id:
            ambiguous_aliases.add(normalized_alias)
            alias_name_map.pop(normalized_alias, None)
            continue
        alias_name_map[normalized_alias] = match

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
    news_company_name_map = await _select_news_company_name_map(db, news_map)

    top_stock_map = await _select_top_stock_row_per_news(db, news_ids)
    change_rate_map = await _fetch_change_rates_for_stock_ids(
        [
            str(top_stock.get("stock_id") or "").strip()
            for top_stock in top_stock_map.values()
        ]
    )

    items: list[NewsRecommendationItem] = []
    for candidate in candidates:
        news = news_map.get(candidate.news_id)
        if news is None:
            continue

        top_stock = top_stock_map.get(candidate.news_id)
        stock_id = str(top_stock.get("stock_id") or "").strip() if top_stock else ""
        change_rate = change_rate_map.get(stock_id) if stock_id else None
        summary = _first_non_empty_text(
            filtered_map.get(candidate.news_id),
            crawled_map.get(candidate.news_id),
        )
        items.append(
            NewsRecommendationItem(
                news_id=int(news.news_id),
                title=decode_html_entities(news.title) or "",
                summary=_preview_text(summary),
                pub_date=news.pub_date,
                path=candidate.path or _default_recommendation_path(source),
                news_company_name=news_company_name_map.get(candidate.news_id),
                stock_name=str(top_stock["stock_name"]) if top_stock and top_stock.get("stock_name") else None,
                stock_change=_format_change_rate(change_rate),
                stock_up=_stock_up_from_change_rate(change_rate),
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
        escaped_search = escape_sql_like_wildcards(search)
        pattern = f"%{escaped_search}%"
        query = query.where(
            NaverNews.title.ilike(
                bindparam("title_search_pattern", pattern),
                escape="\\",
            )
        )

    result = await db.execute(query)
    news_rows = result.all()
    return [
        NewsSimpleResponse(
            news_id=int(news.news_id),
            title=decode_html_entities(news.title) or "",
            summary=_preview_text(_first_non_empty_text(filtered_summary, crawled_text)),
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
            logger.warning(
                "recommendation serve logging failed (%s): %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )

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


@router.get("/top-dwell-stocks", response_model=list[TopDwellStockResponse])
async def get_top_dwell_stocks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    최근 24시간 동안 뉴스 상세 체류 이벤트 수가 가장 많았던 종목 상위 3개를 반환합니다.
    """
    _ = current_user
    window_start = datetime.utcnow() - timedelta(hours=24)

    aggregated_metrics = (
        select(
            NewsStockMapping.stock_id.label("stock_id"),
            func.sum(RecommendationNewsPathMetrics.dwell_event_count).label(
                "total_dwell_event_count"
            ),
            func.count(func.distinct(RecommendationNewsPathMetrics.news_id)).label("news_count"),
            func.max(RecommendationNewsPathMetrics.bucket_end).label("latest_bucket_end"),
        )
        .join(
            NewsStockMapping,
            NewsStockMapping.news_id == RecommendationNewsPathMetrics.news_id,
        )
        .where(RecommendationNewsPathMetrics.path == "TOTAL")
        .where(RecommendationNewsPathMetrics.bucket_end >= window_start)
        .group_by(NewsStockMapping.stock_id)
        .subquery()
    )

    query = (
        select(
            aggregated_metrics.c.stock_id,
            StockSummaryCache.stock_name,
            aggregated_metrics.c.total_dwell_event_count,
            aggregated_metrics.c.news_count,
            aggregated_metrics.c.latest_bucket_end,
        )
        .outerjoin(
            StockSummaryCache,
            StockSummaryCache.stock_id == aggregated_metrics.c.stock_id,
        )
        .order_by(
            desc(aggregated_metrics.c.total_dwell_event_count),
            desc(aggregated_metrics.c.news_count),
            desc(aggregated_metrics.c.latest_bucket_end),
        )
        .limit(3)
    )

    result = await db.execute(query)
    rows = result.mappings().all()

    return [
        TopDwellStockResponse(
            stock_id=row["stock_id"],
            stock_name=decode_html_entities(row["stock_name"]),
            total_dwell_event_count=int(row["total_dwell_event_count"] or 0),
            news_count=int(row["news_count"] or 0),
            latest_bucket_end=row["latest_bucket_end"],
        )
        for row in rows
    ]


@router.get("/top-dwell-keywords", response_model=list[TopDwellKeywordResponse])
async def get_top_dwell_keywords(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    최근 24시간 동안 뉴스 상세 체류 이벤트 수가 가장 많았던 키워드 상위 3개를 반환합니다.
    """
    _ = current_user
    window_start = datetime.utcnow() - timedelta(hours=24)

    aggregated_metrics = (
        select(
            Keyword.word.label("keyword"),
            func.sum(RecommendationNewsPathMetrics.dwell_event_count).label(
                "total_dwell_event_count"
            ),
            func.count(func.distinct(RecommendationNewsPathMetrics.news_id)).label("news_count"),
            func.max(RecommendationNewsPathMetrics.bucket_end).label("latest_bucket_end"),
        )
        .join(
            NewsKeywordMapping,
            NewsKeywordMapping.keyword_id == Keyword.keyword_id,
        )
        .join(
            RecommendationNewsPathMetrics,
            RecommendationNewsPathMetrics.news_id == NewsKeywordMapping.news_id,
        )
        .where(RecommendationNewsPathMetrics.path == "TOTAL")
        .where(RecommendationNewsPathMetrics.bucket_end >= window_start)
        .group_by(Keyword.keyword_id, Keyword.word)
        .subquery()
    )

    query = (
        select(
            aggregated_metrics.c.keyword,
            aggregated_metrics.c.total_dwell_event_count,
            aggregated_metrics.c.news_count,
            aggregated_metrics.c.latest_bucket_end,
        )
        .order_by(
            desc(aggregated_metrics.c.total_dwell_event_count),
            desc(aggregated_metrics.c.news_count),
            desc(aggregated_metrics.c.latest_bucket_end),
            aggregated_metrics.c.keyword,
        )
        .limit(3)
    )

    result = await db.execute(query)
    rows = result.mappings().all()

    return [
        TopDwellKeywordResponse(
            keyword=decode_html_entities(row["keyword"]) or "",
            total_dwell_event_count=int(row["total_dwell_event_count"] or 0),
            news_count=int(row["news_count"] or 0),
            latest_bucket_end=row["latest_bucket_end"],
        )
        for row in rows
    ]


@router.get("/trending-stocks", response_model=list[RecentTrendingStockResponse])
async def get_recent_trending_stocks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    최근 24시간 동안 발행된 뉴스에서 많이 등장한 종목 상위 3개를 반환합니다.
    """
    _ = current_user
    window_start = datetime.utcnow() - timedelta(hours=24)
    news_count_expr = func.count(func.distinct(NewsStockMapping.news_id))
    latest_pub_date_expr = func.max(NaverNews.pub_date)

    query = (
        select(
            NewsStockMapping.stock_id.label("stock_id"),
            func.coalesce(
                StockSummaryCache.stock_name,
                Stock.stock_name,
                NewsStockMapping.stock_id,
            ).label("stock_name"),
            news_count_expr.label("news_count"),
            latest_pub_date_expr.label("latest_pub_date"),
        )
        .select_from(FilteredNews)
        .join(NaverNews, FilteredNews.news_id == NaverNews.news_id)
        .join(NewsStockMapping, NewsStockMapping.news_id == NaverNews.news_id)
        .outerjoin(Stock, Stock.stock_id == NewsStockMapping.stock_id)
        .outerjoin(StockSummaryCache, StockSummaryCache.stock_id == NewsStockMapping.stock_id)
        .where(NaverNews.crawl_status == ProcessStatus.crawl_success)
        .where(NaverNews.pub_date.is_not(None))
        .where(NaverNews.pub_date >= window_start)
        .group_by(
            NewsStockMapping.stock_id,
            StockSummaryCache.stock_name,
            Stock.stock_name,
        )
        .order_by(
            desc(news_count_expr),
            desc(latest_pub_date_expr),
            NewsStockMapping.stock_id.asc(),
        )
        .limit(3)
    )

    result = await db.execute(query)
    rows = result.mappings().all()

    return [
        RecentTrendingStockResponse(
            stock_id=row["stock_id"],
            stock_name=decode_html_entities(row["stock_name"]),
            news_count=int(row["news_count"] or 0),
            latest_pub_date=row["latest_pub_date"],
        )
        for row in rows
    ]


@router.get("/trending-keywords", response_model=list[RecentTrendingKeywordResponse])
async def get_recent_trending_keywords(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    최근 24시간 동안 발행된 뉴스에서 많이 등장한 키워드 상위 3개를 반환합니다.
    """
    _ = current_user
    window_start = datetime.utcnow() - timedelta(hours=24)
    news_count_expr = func.count(func.distinct(NewsKeywordMapping.news_id))
    latest_pub_date_expr = func.max(NaverNews.pub_date)

    query = (
        select(
            Keyword.word.label("keyword"),
            news_count_expr.label("news_count"),
            latest_pub_date_expr.label("latest_pub_date"),
        )
        .select_from(FilteredNews)
        .join(NaverNews, FilteredNews.news_id == NaverNews.news_id)
        .join(NewsKeywordMapping, NewsKeywordMapping.news_id == NaverNews.news_id)
        .join(Keyword, Keyword.keyword_id == NewsKeywordMapping.keyword_id)
        .where(NaverNews.crawl_status == ProcessStatus.crawl_success)
        .where(NaverNews.pub_date.is_not(None))
        .where(NaverNews.pub_date >= window_start)
        .where(func.btrim(Keyword.word) != "")
    )

    if TRENDING_KEYWORD_EXCLUDES:
        query = query.where(
            func.lower(func.btrim(Keyword.word)).not_in(sorted(TRENDING_KEYWORD_EXCLUDES))
        )

    query = (
        query
        .group_by(Keyword.keyword_id, Keyword.word)
        .order_by(
            desc(news_count_expr),
            desc(latest_pub_date_expr),
            Keyword.word.asc(),
        )
        .limit(3)
    )

    result = await db.execute(query)
    rows = result.mappings().all()

    return [
        RecentTrendingKeywordResponse(
            keyword=decode_html_entities(row["keyword"]) or "",
            news_count=int(row["news_count"] or 0),
            latest_pub_date=row["latest_pub_date"],
        )
        for row in rows
    ]


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

    content_stmt = (
        select(FilteredNews.summary, FilteredNews.refined_text, CrawledNews.text)
        .select_from(NaverNews)
        .outerjoin(FilteredNews, FilteredNews.news_id == NaverNews.news_id)
        .outerjoin(CrawledNews, CrawledNews.news_id == NaverNews.news_id)
        .where(NaverNews.news_id.in_(target_news_ids))
    )
    news_summaries = [
        content
        for row in (await db.execute(content_stmt)).fetchall()
        if (content := _first_non_empty_text(row[0], row[1], row[2]))
    ]
    combined_text = "\n\n".join([f"### [기사 {i + 1}]\n{s}" for i, s in enumerate(news_summaries)])
    new_summary = await generate_stock_summary(stock_name, len(news_summaries), combined_text)

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
        news.crawled_news.text if news.crawled_news else None,
    ) or ""
    pipeline_keywords = await _get_keywords_for_news(db, news_id)
    existing_keywords = pipeline_keywords
    existing_stock_rows_map = await _select_stock_rows(db, [news_id])
    existing_stock_rows = existing_stock_rows_map.get(news_id, [])
    existing_top_stock = existing_stock_rows[0] if existing_stock_rows else None

    existing_summary = _first_non_empty_text(filtered.summary if filtered else None) or ""
    existing_sentiment = _first_non_empty_text(filtered.sentiment if filtered else None) or ""

    missing_stocks = existing_top_stock is None
    analysis: dict | None = None

    if missing_stocks:
        analysis = await analyze_article(article_text)
        related_stocks = await _resolve_related_stocks(db, analysis.get("related_stocks") or [])
        if related_stocks:
            existing_stock_rows = [
                {
                    "stock_id": stock_id,
                    "stock_name": stock_name,
                    "created_at": None,
                }
                for stock_id, stock_name in related_stocks
            ]
            existing_top_stock = existing_stock_rows[0]

    related_stock_change_rates = await _fetch_change_rates_for_stock_ids(
        [
            str(stock_row.get("stock_id") or "").strip()
            for stock_row in existing_stock_rows
        ]
    )
    related_stock_payloads = _build_related_stock_payloads(
        existing_stock_rows,
        related_stock_change_rates,
    )
    primary_stock = related_stock_payloads[0] if related_stock_payloads else None
    body = article_text
    summary = existing_summary or None
    sentiment = existing_sentiment or None

    return NewsDetailResponse(
        news_id=int(news.news_id),
        title=decode_html_entities(news.title) or "",
        summary=summary,
        body=body or None,
        pub_date=news.pub_date,
        url=news.url,
        sentiment=sentiment,
        keywords=existing_keywords,
        related_stocks=related_stock_payloads,
        stock_name=str(primary_stock["stock_name"]) if primary_stock else None,
        stock_change=str(primary_stock["stock_change"]) if primary_stock and primary_stock.get("stock_change") else None,
        stock_up=primary_stock["stock_up"] if primary_stock and primary_stock.get("stock_up") is not None else None,
    )
