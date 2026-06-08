"""온보딩 API 라우터"""

from __future__ import annotations

import unicodedata
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Keyword, NewsKeywordMapping, OnboardingTheme, User, UserOnboardingKeyword
from app.routers.users import get_current_subject, get_current_user
from app.schemas import (
    OnboardingKeywordResponse,
    OnboardingThemeResponse,
    UserOnboardingKeywordCreateRequest,
    UserOnboardingKeywordCreateResponse,
    UserOnboardingKeywordResponse,
)

router = APIRouter(
    prefix="/api/onboarding",
    tags=["onboarding"],
)

settings = get_settings()
_VECTOR_COLUMN_CANDIDATES = (
    "embedding_vector",
    "embedding",
    "vector",
    "word_vector",
    "keyword_vector",
)


def _normalize_keyword(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


async def _select_keyword_by_normalized_word(
    db: AsyncSession,
    normalized_keyword: str,
) -> tuple[int, str] | None:
    result = await db.execute(select(Keyword.keyword_id, Keyword.word))
    for keyword_id, word in result.all():
        if _normalize_keyword(str(word)) == normalized_keyword:
            return int(keyword_id), str(word)
    return None


def _embedding_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.10g}" for value in embedding) + "]"


async def _request_ollama_embedding(keyword: str) -> list[float]:
    base_url = settings.ollama_base_url.rstrip("/")
    timeout = settings.ollama_timeout
    model = settings.ollama_embedding_model

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                f"{base_url}/api/embed",
                json={"model": model, "input": keyword},
            )
            if response.status_code == 404:
                response = await client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": model, "prompt": keyword},
                )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="Ollama embedding request failed") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama embedding request HTTP {response.status_code}",
        )

    try:
        data: Any = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Ollama embedding response is not JSON") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Ollama embedding response is invalid")

    embedding = data.get("embedding")
    if embedding is None and isinstance(data.get("embeddings"), list) and data["embeddings"]:
        embedding = data["embeddings"][0]

    if not isinstance(embedding, list) or not embedding:
        raise HTTPException(status_code=502, detail="Ollama embedding response has no embedding")

    try:
        return [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Ollama embedding response is invalid") from exc


async def _select_keyword_vector_column(db: AsyncSession) -> str:
    result = await db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'keywords'
              AND column_name IN (
                  'embedding_vector',
                  'embedding',
                  'vector',
                  'word_vector',
                  'keyword_vector'
              )
            """
        )
    )
    found = {str(row.column_name) for row in result.all()}
    for column_name in _VECTOR_COLUMN_CANDIDATES:
        if column_name in found:
            return column_name
    raise HTTPException(
        status_code=500,
        detail="keywords embedding vector column is not configured",
    )


async def _select_most_similar_keyword(
    db: AsyncSession,
    embedding: list[float],
) -> tuple[int, str] | None:
    vector_column = await _select_keyword_vector_column(db)
    stmt = text(
        f"""
        SELECT keyword_id, word
        FROM keywords
        WHERE {vector_column} IS NOT NULL
        ORDER BY {vector_column} <=> CAST(:embedding AS vector)
        LIMIT 1
        """
    )
    result = await db.execute(stmt, {"embedding": _embedding_literal(embedding)})
    row = result.one_or_none()
    if row is None:
        return None
    return int(row.keyword_id), str(row.word)


async def _create_user_onboarding_keyword(
    db: AsyncSession,
    *,
    user_id: int,
    keyword_id: int,
    original_keyword: str,
) -> tuple[int, bool]:
    stored_original_keyword = original_keyword[:50]
    stmt = (
        pg_insert(UserOnboardingKeyword)
        .values(
            user_id=user_id,
            keyword_id=keyword_id,
            original_keyword=stored_original_keyword,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "keyword_id"],
        )
        .returning(UserOnboardingKeyword.id)
    )
    result = await db.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    if inserted_id is not None:
        return int(inserted_id), True

    existing = await db.execute(
        select(UserOnboardingKeyword.id).where(
            UserOnboardingKeyword.user_id == user_id,
            UserOnboardingKeyword.keyword_id == keyword_id,
        )
    )
    existing_id = existing.scalar_one()
    return int(existing_id), False


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
    """뉴스 집계 기준 상위 키워드 조회. q 파라미터로 실시간 검색 지원."""
    count_col = func.count(NewsKeywordMapping.mapping_id).label("count")
    stmt = (
        select(Keyword.keyword_id.label("id"), Keyword.word, count_col)
        .join(NewsKeywordMapping, Keyword.keyword_id == NewsKeywordMapping.keyword_id)
        .group_by(Keyword.keyword_id, Keyword.word)
        .order_by(count_col.desc())
        .limit(limit)
    )
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Keyword.word.ilike(f"%{escaped}%", escape="\\"))

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


@router.post("/keywords", response_model=UserOnboardingKeywordCreateResponse)
async def create_user_onboarding_keywords(
    request: UserOnboardingKeywordCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """사용자 온보딩 관심 키워드 등록"""
    response_items: list[UserOnboardingKeywordResponse] = []

    for item in request.keywords:
        original_keyword = item.original_keyword
        normalized_keyword = _normalize_keyword(original_keyword)
        if not normalized_keyword:
            raise HTTPException(status_code=400, detail="original_keyword must contain searchable characters")

        selected = await _select_keyword_by_normalized_word(db, normalized_keyword)
        if selected is not None:
            keyword_id, _ = selected
            matched_by = "normalized_word"
        else:
            embedding = await _request_ollama_embedding(normalized_keyword)
            selected = await _select_most_similar_keyword(db, embedding)
            if selected is None:
                raise HTTPException(status_code=404, detail="no keyword vector candidates found")
            keyword_id, _ = selected
            matched_by = "embedding_similarity"

        stored_original_keyword = original_keyword[:50]
        onboarding_keyword_id, created = await _create_user_onboarding_keyword(
            db,
            user_id=current_user.id,
            keyword_id=keyword_id,
            original_keyword=stored_original_keyword,
        )
        response_items.append(
            UserOnboardingKeywordResponse(
                id=onboarding_keyword_id,
                keyword_id=keyword_id,
                original_keyword=stored_original_keyword,
                matched_by=matched_by,
                created=created,
            )
        )

    await db.commit()
    return UserOnboardingKeywordCreateResponse(items=response_items)
