"""
market_cap이 null인 종목만 골라 KIS API로 재시도하는 스크립트.

실행:
    python -m scripts.retry_market_cap
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from sqlalchemy import select, update
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.kis.client import KISClient
from app.models import Stock

_CHUNK_SIZE = 100


async def main() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Stock.stock_id).where(Stock.market_cap.is_(None))
        )
        null_ids = [row[0] for row in result.all()]

    logger.info("market_cap null 종목: %d개", len(null_ids))
    if not null_ids:
        logger.info("재시도할 종목 없음")
        return

    settings = get_settings()
    client = KISClient(settings)

    chunk: dict[str, int] = {}
    total_updated = 0
    failed = 0

    try:
        for stock_id in null_ids:
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
                if hts_avls and str(hts_avls).strip():
                    chunk[stock_id] = int(hts_avls) * 100_000_000
                else:
                    logger.warning("종목 %s hts_avls 비어있음 (값: %r)", stock_id, hts_avls)
                    failed += 1
            except Exception as exc:
                logger.warning("종목 %s 조회 실패: %s", stock_id, exc)
                failed += 1

            if len(chunk) >= _CHUNK_SIZE:
                async with AsyncSessionLocal() as db:
                    for sid, market_cap in chunk.items():
                        await db.execute(
                            update(Stock)
                            .where(Stock.stock_id == sid)
                            .values(market_cap=market_cap)
                        )
                    await db.commit()
                total_updated += len(chunk)
                logger.info("저장: %d / %d 완료", total_updated, len(null_ids))
                chunk.clear()

        if chunk:
            async with AsyncSessionLocal() as db:
                for sid, market_cap in chunk.items():
                    await db.execute(
                        update(Stock)
                        .where(Stock.stock_id == sid)
                        .values(market_cap=market_cap)
                    )
                await db.commit()
            total_updated += len(chunk)

    finally:
        await client.aclose()

    logger.info("완료 — 성공: %d / 실패: %d", total_updated, failed)


if __name__ == "__main__":
    asyncio.run(main())
