"""온보딩 API 라우터"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import OnboardingTheme, OnboardingThemeCategory
from app.schemas import OnboardingThemeResponse

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
