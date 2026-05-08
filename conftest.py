# conftest.py (프로젝트 루트)
import pytest
import pytest_asyncio
from sqlalchemy import Text
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
import json
from typing import Any
from sqlalchemy.types import TypeDecorator
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker, AsyncSession
from unittest.mock import patch, AsyncMock

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

# 모델 import는 패치 이후에 해야 적용됨
from app.database import Base
from app import models  # noqa: F401 — 모든 모델 등록용


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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        
@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine, create_tables):
    async with engine.connect() as conn:
        trans = await conn.begin()
        # join_transaction_mode가 핵심입니다.
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        
        yield session
        
        await session.close()   # 1. 세션 먼저 닫기
        await trans.rollback()  # 2. 트랜잭션 롤백[cite: 1]
        await conn.close()      # 3. 연결 완전히 종료[cite: 1]

@pytest_asyncio.fixture(autouse=True)
async def patch_async_session_local(db_session: AsyncSession):
    """
    모든 테스트에서 AsyncSessionLocal이 테스트용 db_session을 반환하도록 패치.
    실제 PostgreSQL 연결을 차단합니다.
    """
    from unittest.mock import MagicMock

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("app.database.AsyncSessionLocal", return_value=mock_cm), \
         patch("app.services.onesignal_service.AsyncSessionLocal", return_value=mock_cm), \
         patch("app.tasks.news_tasks.AsyncSessionLocal", return_value=mock_cm), \
         patch("app.tasks.volatility_monitor.AsyncSessionLocal", return_value=mock_cm), \
         patch("app.routers.stocks.AsyncSessionLocal", return_value=mock_cm): 
        yield