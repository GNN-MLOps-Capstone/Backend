# conftest.py (프로젝트 루트)
import pytest
import pytest_asyncio
from sqlalchemy import Text
from sqlalchemy.pool import StaticPool
import json
from typing import Any
from sqlalchemy.types import TypeDecorator
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker, AsyncSession

# =============================================================================
# SQLite 호환 처리: JSONB → Text 교체
# =============================================================================
# SQLite는 PostgreSQL 전용 JSONB를 지원하지 않으므로
# 테스트용 인메모리 DB에서는 Text로 대체합니다.
# (실제 프로덕션 PostgreSQL에는 영향 없음)
class _SQLiteJSONB(TypeDecorator):
    """SQLite 테스트용 JSONB 대체 타입"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value: str | None, dialect: Any) -> Any:
        if value is None:
            return None
        return json.loads(value)

# JSONB 클래스를 패치하여 SQLite에서도 동작하게 함
import sqlalchemy.dialects.postgresql as pg_dialect
pg_dialect.JSONB = _SQLiteJSONB


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
def engine() -> AsyncEngine:
    """테스트 세션 동안 재사용하는 SQLite 인메모리 엔진"""
    return create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture(scope="session")
async def create_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    """세션 시작 시 테이블 생성, 종료 시 삭제"""
    # 모델 import는 JSONB 패치 이후에 해야 적용됨
    from app.database import Base
    from app import models  # noqa: F401 — 모든 모델 등록용

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(
    engine: AsyncEngine,
    create_tables: AsyncIterator[None],
) -> AsyncIterator[AsyncSession]:
    """각 테스트마다 독립적인 세션 (롤백으로 데이터 격리)"""
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        async with session.begin():
            yield session
            await session.rollback()  # 테스트 후 롤백 → 다음 테스트에 영향 없음