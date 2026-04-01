import asyncio
import pytz
from datetime import date, datetime
from sqlalchemy import select, func
from app.database import AsyncSessionLocal 
from app.models import Watchlist, User, Stock, Notification
from app.routers.stocks import _fetch_stock_overview
from app.services.onesignal_service import send_volatility_push_and_save

async def run_volatility_check():
    # 한국 시간대 설정
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    
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
                        user_stmt = select(User.google_id).distinct().join(
                            Watchlist, User.id == Watchlist.user_id
                        ).where(
                            Watchlist.stock_id == stock_code,
                            User.id < 9000  #테스트 ID 필터링 추가
                        )
                        
                        user_ids = (await db.execute(user_stmt)).scalars().all()
                        
                        # 중복 제거 및 발송
                        unique_user_ids = list(set(user_ids))
                        if not unique_user_ids: 
                            continue

                        await send_volatility_push_and_save(db, unique_user_ids, stock_name, rate)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"⚠️ [{stock_code}] 데이터 조회 중 에러: {e}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"❌ 감시 엔진 실행 에러: {e}")