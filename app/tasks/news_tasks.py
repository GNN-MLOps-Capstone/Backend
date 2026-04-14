import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
import httpx
from app.database import AsyncSessionLocal
from app.models import Watchlist, User, Stock, Notification, UserSettings
from app.kis.transformers import KST  # 한국 시간대 (없으면 pytz.timezone('Asia/Seoul') 등 사용)
from app.routers.news import get_stock_news_stats_from_db
from app.services.onesignal_service import send_volatility_push_and_save

logger = logging.getLogger(__name__)

# app/tasks/news_tasks.py

async def run_news_keyword_check() -> None:
    start_now = datetime.now(KST)
    start_today = start_now.date()
    active_threshold = start_now - timedelta(days=7)
    current_hour = start_now.hour
    
    # 야간 시간 여부 체크 (23:00 ~ 07:00)
    is_night_time = current_hour >= 23 or current_hour < 7
    
    logger.info(f"🕒 [{start_now.strftime('%Y-%m-%d %H:%M')}] 뉴스 키워드 감시 시작 (야간 여부: {is_night_time})")

    async with AsyncSessionLocal() as db:
        try:
            # 1. 중복 발송 내역 조회 (기존과 동일)
            sent_stmt = select(Notification.user_id, Notification.stock_name).where(
                Notification.type == "keywords",
                Notification.date_kst == start_today
            )
            sent_result = await db.execute(sent_stmt)
            sent_history = {
                (row[0], row[1])
                for row in sent_result.all()
            }

            # 2. 감시 대상 종목 조회 (기존과 동일)
            stocks_stmt = (
                select(Stock.stock_id, Stock.stock_name)
                .distinct()
                .join(Watchlist, Stock.stock_id == Watchlist.stock_id)
                .join(User, Watchlist.user_id == User.id)
                .where(User.id < 9000, User.last_login >= active_threshold)
            )
            stocks_result = await db.execute(stocks_stmt)
            all_stocks = stocks_result.all()
        except SQLAlchemyError as e:
            logger.error(f"❌ DB 조회 에러 (SQLAlchemy): {e}")
            return
        except Exception as e:
            logger.error(f"🚨 시스템 치명적 에러: {e}")
            raise

    for stock_id, stock_name in all_stocks:

        await asyncio.sleep(0.5)

        current_now = datetime.now(KST)
        current_today = current_now.date()
        current_hour = current_now.hour
        is_night_time = current_hour >= 23 or current_hour < 7

        if current_today != start_today:
            logger.warning(f"날짜가 변경되어({start_today} -> {current_today}) 작업을 중단합니다.")
            break

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
                        .where(
                            Watchlist.stock_id == stock_id,
                            User.id < 9000,
                            User.last_login >= active_threshold
                        )
                    )
                    u_res = await db.execute(user_stmt)
                    user_configs = u_res.all()

                    final_targets = []
                    for gid, push_enabled, night_prohibit, interest_only in user_configs:
                        if not push_enabled or not interest_only: continue
                        if is_night_time and night_prohibit: continue
                        if (gid, stock_name) in sent_history: continue
                        final_targets.append(gid)

                    if not final_targets:
                        continue

                    push_sent, db_saved = await send_volatility_push_and_save(
                        user_ids=final_targets,
                        stock_name=stock_name,
                        date_kst=current_today,
                        alert_type="keywords",
                        news_count=stats['count'],
                        keywords=stats['keywords']
                    )

                    if db_saved:
                        # 1. DB 저장이 성공했다면, 중복 방지 목록에 추가 (다음 주기 중복 방지)
                        for gid in final_targets:
                            sent_history.add((gid, stock_name))
                        if push_sent:
                            logger.info(f"✅ [{stock_name}] {len(final_targets)}명에게 뉴스 알림 발송")
                        else:
                            # 아이폰이나 벡그라운드에서 앱이 실행될 때의 경우
                            logger.warning(f"⚠️ [{stock_name}] 푸시는 실패했으나 DB 기록 완료 (앱 내 알림함 생성)")
                    else:
                        # 2. DB 저장이 실패했다면, 다음 30분 뒤 실행 시 다시 시도될 수 있도록
                        # sent_history에 넣지 않고 에러 로그를 남깁니다.
                        if push_sent:
                            logger.error(f"🚨 [{stock_name}] 푸시는 이미 갔는데 DB 저장 실패! 다음 주기에 중복 발송 위험 있음.")
                        else:
                            logger.error(f"❌ [{stock_name}] 푸시 및 DB 저장 모두 실패")

        except (SQLAlchemyError, httpx.RequestError) as e:
            logger.error(f"⚠️ [{stock_name}] 처리 중 {type(e).__name__} 발생: {e}")
        except Exception as e:
            logger.exception(f"⚠️ [{stock_name}] 처리 중 예상치 못한 에러 발생: {e}")

    logger.info("✨ 뉴스 감시 종료")