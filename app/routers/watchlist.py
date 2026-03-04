"""
==============================================================================
관심종목 API 라우터 (watchlist.py)
==============================================================================

API 엔드포인트:
    GET    /api/watchlist          -> 관심종목 목록
    POST   /api/watchlist          -> 종목 추가
    DELETE /api/watchlist/{code}   -> 종목 삭제
    GET    /api/watchlist/briefing -> AI 브리핑
    GET    /api/stocks/{code}      -> 종목 상세

==============================================================================
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import Watchlist, Stock, StockSummaryCache, User
from app.routers.users import get_current_user
from app.schemas import (
    WatchlistAddRequest,
    WatchlistStockResponse,
    WatchlistBriefingResponse,
)
from app.services.kis_service import kis_service


router = APIRouter(
    prefix="/api",
    tags=["watchlist"],
)


# =============================================================================
# 관심종목 목록 조회
# =============================================================================

@router.get("/watchlist", response_model=list[WatchlistStockResponse])
async def get_watchlist(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    관심종목 목록 조회

    Returns:
        list[WatchlistStockResponse]: 관심종목 목록
    """
    # watchlist 조회
    query = (
        select(Watchlist)
        .where(Watchlist.user_id == current_user.google_id)
        .order_by(Watchlist.created_at.desc())
    )

    result = await db.execute(query)
    watchlist_items = result.scalars().all()

    if not watchlist_items:
        return []

    # 종목 코드 리스트 추출
    stock_codes = [item.stock_id for item in watchlist_items]

    # 한국투자증권 API로 실시간 가격 조회
    prices = await kis_service.get_multiple_prices(stock_codes)

    # Stock 및 StockSummaryCache 일괄 조회
    stock_result = await db.execute(
        select(Stock).where(Stock.stock_id.in_(stock_codes))
    )
    stock_map = {row.stock_id: row for row in stock_result.scalars().all()}

    cache_result = await db.execute(
        select(StockSummaryCache).where(StockSummaryCache.stock_id.in_(stock_codes))
    )
    summary_map = {row.stock_id: row.summary_text for row in cache_result.scalars().all()}

    # 종목 정보 조회 및 응답 구성
    response_list = []
    for item in watchlist_items:
        stock = stock_map.get(item.stock_id)
        stock_name = stock.stock_name if stock else item.stock_id

        # 실시간 가격 정보
        price_info = prices.get(item.stock_id, {})
        price = price_info.get("price", 0)
        change_rate = price_info.get("change_rate", 0.0)

        # 등락률에 따른 날씨 결정
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
    """
    관심종목 추가

    Parameters:
        request: { code: "종목코드" }

    Returns:
        성공 메시지
    """
    # 종목 존재 여부 확인 (FK 제약조건 위반 방지)
    stock_result = await db.execute(
        select(Stock).where(Stock.stock_id == request.code)
    )
    if not stock_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="존재하지 않는 종목입니다")

    # 새 관심종목 추가 (UNIQUE 제약으로 중복 방지, 경쟁 상태 안전)
    try:
        db.add(Watchlist(user_id=current_user.google_id, stock_id=request.code))
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
    """
    관심종목 삭제

    Parameters:
        code: 종목 코드

    Returns:
        성공 메시지
    """
    await db.execute(
        delete(Watchlist)
        .where(Watchlist.stock_id == code, Watchlist.user_id == current_user.google_id)
    )
    await db.commit()

    return {"message": "관심종목 삭제 완료", "code": code}


# =============================================================================
# AI 브리핑
# =============================================================================

@router.get("/watchlist/briefing", response_model=WatchlistBriefingResponse)
async def get_watchlist_briefing(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    관심종목 AI 브리핑

    Returns:
        WatchlistBriefingResponse: AI 브리핑
    """
    # watchlist 조회
    query = select(Watchlist).where(Watchlist.user_id == current_user.google_id)

    result = await db.execute(query)
    watchlist_items = result.scalars().all()

    if not watchlist_items:
        return WatchlistBriefingResponse(
            text="관심종목을 추가해주세요.",
            topIssues=[],
        )

    # 종목 코드 리스트 추출
    stock_codes = [item.stock_id for item in watchlist_items]

    # 한국투자증권 API로 실시간 가격 조회
    prices = await kis_service.get_multiple_prices(stock_codes)

    # Stock 일괄 조회
    stock_result = await db.execute(
        select(Stock).where(Stock.stock_id.in_(stock_codes))
    )
    stock_map = {row.stock_id: row for row in stock_result.scalars().all()}

    # 종목 정보 수집
    stock_data = []
    for item in watchlist_items:
        stock = stock_map.get(item.stock_id)
        stock_name = stock.stock_name if stock else item.stock_id
        price_info = prices.get(item.stock_id, {})
        change_rate = price_info.get("change_rate", 0.0)
        stock_data.append({
            "name": stock_name,
            "change_rate": change_rate,
            "industry": stock.industry if stock else "",
        })

    # 브리핑 텍스트 생성
    rising = [s for s in stock_data if s["change_rate"] > 0]
    falling = [s for s in stock_data if s["change_rate"] < 0]

    text_parts = []
    if rising:
        top_rising = sorted(rising, key=lambda x: x["change_rate"], reverse=True)[:3]
        names = ", ".join([f"{s['name']}(+{s['change_rate']:.1f}%)" for s in top_rising])
        text_parts.append(f"상승 종목: {names}")

    if falling:
        top_falling = sorted(falling, key=lambda x: x["change_rate"])[:3]
        names = ", ".join([f"{s['name']}({s['change_rate']:.1f}%)" for s in top_falling])
        text_parts.append(f"하락 종목: {names}")

    if not text_parts:
        text_parts.append("관심종목이 보합세를 유지하고 있습니다.")

    # 주요 이슈 (업종 기반)
    industries = list(set([s["industry"] for s in stock_data if s["industry"]]))[:3]

    return WatchlistBriefingResponse(
        text=" ".join(text_parts),
        topIssues=industries if industries else ["관심종목 분석중"],
    )


# =============================================================================
# 종목 상세 조회
# =============================================================================

@router.get("/stocks/{code}", response_model=WatchlistStockResponse)
async def get_stock_detail(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    종목 상세 조회

    Parameters:
        code: 종목 코드

    Returns:
        WatchlistStockResponse: 종목 상세 정보
    """
    # stocks 테이블에서 종목 정보 조회
    result = await db.execute(
        select(Stock).where(Stock.stock_id == code)
    )
    stock = result.scalar_one_or_none()

    if not stock:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다")

    # 한국투자증권 API로 실시간 가격 조회
    price_info = await kis_service.get_stock_price(code)
    price = price_info.get("price", 0)
    change_rate = price_info.get("change_rate", 0.0)

    # StockSummaryCache에서 AI 요약 조회
    cache_result = await db.execute(
        select(StockSummaryCache).where(StockSummaryCache.stock_id == code)
    )
    cache = cache_result.scalar_one_or_none()

    # 등락률에 따른 날씨 결정
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
