import asyncio
import pytz
from datetime import date, datetime, time
from sqlalchemy import select, func
from app.database import AsyncSessionLocal 
from app.models import Watchlist, User, Stock, Notification, UserSettings
from app.routers.stocks import _fetch_stock_overview
from app.services.onesignal_service import send_volatility_push_and_save

async def run_volatility_check():
    # 한국 시간대 설정
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    current_time = now.time()

    is_night_range = current_time >= time(23, 0) or current_time < time(7, 0)
    
    # 주말이거나 운영 시간이 아니면 종료
    if now.weekday() >= 5 or not (8 <= now.hour < 20):
        return

    async with AsyncSessionLocal() as db:
        try:
            today_kst = now.date()
            # 오늘 이미 알림을 보낸 종목명 리스트 가져오기 (중복 방지)
            sent_today_stmt = select(Notification.stock_name).where(
                Notification.type.in_(["risk", "high_risk"]),
                func.date(Notification.created_at) == today_kst
            ).distinct()
            sent_result = await db.execute(sent_today_stmt)
            sent_stock_names = set(sent_result.scalars().all())

            # 감시할 전체 종목 정보 가져오기
            stmt = select(Watchlist.stock_id, Stock.stock_name).distinct().join(
                Stock, Watchlist.stock_id == Stock.stock_id
            ).join(
                User, Watchlist.user_id == User.id
            ).where(
                User.id < 9000
            )
            result = await db.execute(stmt)
            all_stocks = result.all()

            # 이미 보낸 종목 필터링
            target_stocks = [
                (code, name) for code, name in all_stocks 
                if name not in sent_stock_names
            ]
            
            if not target_stocks:
                return

            print(f"감시 시작: {len(target_stocks)}개 종목 (오늘 완료된 {len(sent_stock_names)}개 제외)")

            for stock_code, stock_name in target_stocks:
                # API 호출 속도 제한 (1.2초 간격)
                await asyncio.sleep(1.2) 

                try:
                    overview = await _fetch_stock_overview(stock_code)
                    if not overview:
                        continue
                    
                    rate = float(overview.get("change_rate") or 0.0)

                    # 등락률 5% 이상일 때만 진행
                    if abs(rate) >= 5.0:
                        # 해당 종목을 즐겨찾기한 유저 조회 + 테스트 ID(9000이상) 제외
                        user_stmt = select(
                            User.google_id, 
                            UserSettings.night_push_prohibit
                        ).distinct().join(
                            UserSettings, User.id == UserSettings.user_id
                        ).join(
                            Watchlist, User.id == Watchlist.user_id
                        ).where(
                            Watchlist.stock_id == stock_code,
                            User.id < 9000
                        )
                        
                        user_results = (await db.execute(user_stmt)).all()
                        
                        push_now_ids = [] # 알람 푸시를 보낼 대상들
                        save_only_ids = [] # db에만 알람을 저장할 대상들

                        for google_id, night_push_prohibit in user_results:
                            # 야간 시간이고 유저가 금지 모드를 켰는가?
                            if is_night_range and night_push_prohibit:
                                save_only_ids.append(google_id)
                            else:
                                push_now_ids.append(google_id)
                        
                        # 즉시 발송 + DB 저장
                        if push_now_ids:
                            await send_volatility_push_and_save(
                                db, push_now_ids, stock_name, rate, skip_push=False
                            )
                        # DB 저장만 (야간 모드 유저들)
                        if save_only_ids:
                            await send_volatility_push_and_save(
                                db, save_only_ids, stock_name, rate, skip_push=True
                            )

                except Exception as e:
                    print(f"⚠️ [{stock_code}] 데이터 조회 중 에러: {e}")

        except Exception as e:
            print(f"❌ 감시 엔진 실행 에러: {e}")