import asyncio
import logging
from datetime import datetime
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Watchlist, User, Stock, Notification, UserSettings
from app.kis.transformers import KST  # 한국 시간대 (없으면 pytz.timezone('Asia/Seoul') 등 사용)
from app.routers.news import get_stock_news_stats_from_db
from app.services.onesignal_service import send_volatility_push_and_save

logger = logging.getLogger(__name__)

# app/tasks/news_tasks.py

async def run_news_keyword_check() -> None:
    now = datetime.now(KST)
    today = now.date()
    current_hour = now.hour
    
    # 야간 시간 여부 체크 (23:00 ~ 07:00)
    is_night_time = current_hour >= 23 or current_hour < 7
    
    print(f"🕒 [{now.strftime('%Y-%m-%d %H:%M')}] 뉴스 키워드 감시 시작 (야간 여부: {is_night_time})")

    async with AsyncSessionLocal() as db:
        try:
            # 1. 중복 발송 내역 조회 (기존과 동일)
            sent_stmt = select(Notification.user_id, Notification.stock_name, Notification.created_at).where(
                Notification.type == "keywords",
                Notification.date_kst == today
            )
            sent_result = await db.execute(sent_stmt)
            sent_history = {
                (row[0], row[1])
                for row in sent_result.all()
            }

            # 2. 감시 대상 종목 조회 (기존과 동일)
            stocks_stmt = select(Stock.stock_id, Stock.stock_name).distinct().join(
                Watchlist, Stock.stock_id == Watchlist.stock_id
            )
            stocks_result = await db.execute(stocks_stmt)
            all_stocks = stocks_result.all()
        except Exception as e:
            logger.error(f"❌ 초기 조회 에러: {e}")
            return

    for stock_id, stock_name in all_stocks:
        await asyncio.sleep(0.5)

        try:
            async with AsyncSessionLocal() as db:
                stats = await get_stock_news_stats_from_db(db, stock_id, stock_name)
            
                if stats and stats.get("is_spike"):
                    user_stmt = (
                        select(
                            User.google_id,
                            UserSettings.push,
                            UserSettings.night_push_prohibit, 
                            UserSettings.interest_only
                        )
                        .distinct()
                        .join(Watchlist, User.id == Watchlist.user_id)
                        .join(UserSettings, User.id == UserSettings.user_id)
                        .where(Watchlist.stock_id == stock_id)
                    )
                    u_res = await db.execute(user_stmt)
                    user_configs = u_res.all()

                    final_targets = []
                    for gid, push_enabled, night_prohibit, interest_only in user_configs:
                        if not push_enabled:
                            continue
                        if not interest_only:
                            continue
                        
                        if is_night_time and night_prohibit:
                            continue
                        
                        if (gid, stock_name) in sent_history:
                            continue
                        
                        final_targets.append(gid)

                    if not final_targets:
                        continue

                    push_sent, db_saved = await send_volatility_push_and_save(
                        user_ids=final_targets,
                        stock_name=stock_name,
                        date_kst=today,
                        alert_type="keywords",
                        news_count=stats['count'],
                        keywords=stats['keywords']
                    )

                    if push_sent:
                        print(f"✅ [{stock_name}] {len(final_targets)}명에게 뉴스 알림 발송")
                        for gid in final_targets:
                            sent_history.add((gid, stock_name))

        except Exception as e:
            logger.error(f"⚠️ [{stock_name}] 처리 에러: {e}")
            continue

    print(f"✨ 뉴스 감시 종료")