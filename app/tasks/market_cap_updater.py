import asyncio
import logging

from sqlalchemy import select, update

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.kis.client import KISClient
from app.kis.transformers import KST
from app.models import Stock

logger = logging.getLogger(__name__)


_CHUNK_SIZE = 100


async def _flush_chunk(chunk: dict[str, int]) -> None:
    async with AsyncSessionLocal() as db:
        for stock_id, market_cap in chunk.items():
            await db.execute(
                update(Stock)
                .where(Stock.stock_id == stock_id)
                .values(market_cap=market_cap)
            )
        await db.commit()


async def run_market_cap_update() -> None:
    """매일 오전 8시: KIS API로 전 종목 시가총액을 갱신한다."""
    settings = get_settings()
    client = KISClient(settings)

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Stock.stock_id))
            stock_ids = [row[0] for row in result.all()]

        logger.info("시가총액 업데이트 시작: %d개 종목", len(stock_ids))

        chunk: dict[str, int] = {}
        total_updated = 0
        failed = 0

        for stock_id in stock_ids:
            try:
                data = await client.request(
                    "GET",
                    "/uapi/domestic-stock/v1/quotations/inquire-price",
                    tr_id="FHKST01010100",
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": stock_id,
                    },
                )
                hts_avls = (data.get("output") or {}).get("hts_avls")
                if hts_avls:
                    chunk[stock_id] = int(hts_avls) * 100_000_000  # 억원 → 원
            except Exception as exc:
                logger.warning("종목 %s 시가총액 조회 실패: %s", stock_id, exc)
                failed += 1

            if len(chunk) >= _CHUNK_SIZE:
                await _flush_chunk(chunk)
                total_updated += len(chunk)
                logger.info("진행: %d / %d 저장 완료", total_updated, len(stock_ids))
                chunk.clear()

        if chunk:
            await _flush_chunk(chunk)
            total_updated += len(chunk)

        logger.info(
            "시가총액 업데이트 완료: 성공 %d / 실패 %d", total_updated, failed
        )

    except Exception:
        logger.exception("시가총액 업데이트 중 치명적 오류 발생")
        raise
    finally:
        await client.aclose()
