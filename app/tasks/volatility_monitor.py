import asyncio
import pytz
from datetime import datetime, timedelta
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
            day_start_kst = now.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end_kst = day_start_kst + timedelta(days=1)
            # 오늘 이미 알림을 보낸 종목명 리스트 가져오기 (중복 방지)
            sent_today_stmt = select(
                Notification.user_id,
                Notification.stock_name,
                Notification.type
            ).where(
                Notification.type.in_(["risk", "high_risk"]),
                Notification.created_at >= day_start_kst.astimezone(pytz.UTC),
                Notification.created_at < day_end_kst.astimezone(pytz.UTC),
            )
            sent_result = await db.execute(sent_today_stmt)
            sent_history: set[tuple[str, str, str]] = set(sent_result.all())

            # 감시할 전체 종목 정보 가져오기
            stmt = select(Watchlist.stock_id, Stock.stock_name).distinct().join(
                Stock, Watchlist.stock_id == Stock.stock_id
            ).join(
                User, Watchlist.user_id == User.id
            ).where(User.id < 9000)
            result = await db.execute(stmt)
            all_stocks = result.all()

        except Exception as e:
            print(f"❌ 초기 데이터 조회 에러: {e}")
            return
            
    if not all_stocks:
        return

    print(f"감시 시작: {len(all_stocks)}개 종목")

    for stock_code, stock_name in all_stocks:
        # API 호출 속도 제한 (1.2초 간격)
        await asyncio.sleep(1.2) 

        try:
            overview = await _fetch_stock_overview(stock_code)
            if not overview:
                continue
                    
            raw_rate = overview.get("change_rate")
            if raw_rate is None:
                print(f"⚠️ [{stock_code}] change_rate가 없어 변동성 계산을 건너뜁니다.")
                continue
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                print(f"⚠️ [{stock_code}] change_rate 파싱 실패: {raw_rate!r}")
                continue
            abs_rate = abs(rate)

            if abs_rate < 5.0:
                continue

            current_type = "high_risk" if abs_rate >= 10.0 else "risk"

             # 이 종목의 watcher 전체 조회
            async with AsyncSessionLocal() as db:
                user_stmt = select(User.google_id).distinct().join(
                    Watchlist, User.id == Watchlist.user_id
                ).where(
                    Watchlist.stock_id == stock_code,
                    User.id < 9000
                )
                user_rows = [row[0] for row in (await db.execute(user_stmt)).all()]
 
            if not user_rows:
                continue
            def already_notified(google_id: str, stype: str) -> bool:
                if stype == "high_risk":
                    # high_risk 전송 대상: high_risk를 아직 안 받은 유저
                    return (google_id, stock_name, "high_risk") in sent_history
                else:  # risk
                    # risk 전송 대상: risk도 high_risk도 아직 안 받은 유저
                    return (
                        (google_id, stock_name, "risk") in sent_history
                        or (google_id, stock_name, "high_risk") in sent_history
                    )
 
            target_users = [
                google_id
                for google_id in user_rows
                if not already_notified(google_id, current_type)
            ]
 
            if not target_users:
                continue
 
            await send_volatility_push_and_save(
                target_users,
                stock_name,
                rate,
                now.date(),
            )
 
            # ✅ 발송 후 메모리 상의 sent_history 업데이트 (같은 루프 내 중복 방지)
            for google_id in user_rows:
                if google_id in target_users:
                    sent_history.add((google_id, stock_name, current_type))
 
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ [{stock_code}] 데이터 조회 중 에러: {e}")