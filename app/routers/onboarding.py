"""온보딩 API 라우터"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import OnboardingTheme, OnboardingThemeCategory, Keyword, NewsKeywordMapping
from app.routers.users import get_current_subject
from app.schemas import OnboardingThemeResponse, OnboardingKeywordResponse

router = APIRouter(
    prefix="/api/onboarding",
    tags=["onboarding"],
)


@router.get("/themes", response_model=list[OnboardingThemeResponse])
async def get_themes(db: AsyncSession = Depends(get_db)):
    """온보딩 테마 목록 (카테고리 포함, 기타 포함)"""
    result = await db.execute(
        select(OnboardingTheme)
        .options(selectinload(OnboardingTheme.categories))
        .order_by(OnboardingTheme.display_order)
    )
    themes = result.scalars().all()
    return [
        OnboardingThemeResponse(
            id=t.id,
            name=t.name,
            display_order=t.display_order,
            categories=[c.category_name for c in t.categories],
        )
        for t in themes
    ]


@router.get("/keywords", response_model=list[OnboardingKeywordResponse])
async def get_top_keywords(
    q: str | None = Query(None, description="키워드 검색어"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_subject),
):
    """뉴스 집계 기준 상위 키워드 조회 (온보딩용)"""
    count_col = func.count(NewsKeywordMapping.mapping_id).label("count")
    stmt = (
        select(Keyword.keyword_id.label("id"), Keyword.word, count_col)
        .join(NewsKeywordMapping, Keyword.keyword_id == NewsKeywordMapping.keyword_id)
        .group_by(Keyword.keyword_id, Keyword.word)
        .order_by(count_col.desc())
        .limit(limit)
    )
    if q and q.strip():
        stmt = stmt.where(Keyword.word.ilike(f"%{q.strip()}%"))

    result = await db.execute(stmt)
    return [
        OnboardingKeywordResponse(id=row.id, word=row.word, count=row.count)
        for row in result.all()
    ]


@router.get("/misc-categories", response_model=list[str])
async def get_misc_categories(db: AsyncSession = Depends(get_db)):
    """기타(미분류) 카테고리 목록"""
    result = await db.execute(
        select(OnboardingTheme)
        .options(selectinload(OnboardingTheme.categories))
        .where(OnboardingTheme.name == "기타")
    )
    theme = result.scalar_one_or_none()
    if theme is None:
        return []
    return [c.category_name for c in theme.categories]
