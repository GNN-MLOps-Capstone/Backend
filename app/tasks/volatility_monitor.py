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
            sent_today_stmt = select(Notification.stock_name, Notification.type).where(
                Notification.type.in_(["risk", "high_risk"]),
                Notification.created_at >= day_start_kst.astimezone(pytz.UTC),
                Notification.created_at < day_end_kst.astimezone(pytz.UTC),
            )
            sent_result = await db.execute(sent_today_stmt)
            sent_history = set(sent_result.all())

            # 감시할 전체 종목 정보 가져오기
            stmt = select(Watchlist.stock_id, Stock.stock_name).distinct().join(
                Stock, Watchlist.stock_id == Stock.stock_id
            ).join(
                User, Watchlist.user_id == User.id
            ).where(User.id < 9000)
            result = await db.execute(stmt)
            all_stocks = result.all()

            # 이미 보낸 종목 필터링
            target_stocks = [
                (code, name) for code, name in all_stocks 
                if (name, "high_risk") not in sent_history
            ]
        except Exception as e:
            print(f"❌ 초기 데이터 조회 에러: {e}")
            return
            
    if not target_stocks:
        return

    print(f"감시 시작: {len(target_stocks)}개 종목 (오늘 완료된 {len(sent_history)}개 제외)")

    for stock_code, stock_name in target_stocks:
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

            # 알림 발송 여부 결정 로직
            should_send = False

            # 등락률 5% 이상일 때만 진행
            if abs_rate >= 10.0:
                # 10% 돌파: 오늘 이 종목으로 high_risk를 보낸 적이 없다면 발송
                if (stock_name, "high_risk") not in sent_history:
                    should_send = True
            elif abs_rate >= 5.0:
                if (stock_name, "risk") not in sent_history and (stock_name, "high_risk") not in sent_history:
                    should_send = True
            if should_send:
                # 유저 조회 (세션 분리 패턴 유지)
                async with AsyncSessionLocal() as db:
                    user_stmt = select(User.google_id).distinct().join(
                        Watchlist, User.id == Watchlist.user_id
                    ).where(
                        Watchlist.stock_id == stock_code,
                        User.id < 9000
                    )

                    user_ids = (await db.execute(user_stmt)).scalars().all()
                    unique_user_ids = list(set(user_ids))
                
                if unique_user_ids:
                    await send_volatility_push_and_save(unique_user_ids, stock_name, rate)
                    # 중요: 현재 루프 메모리 상의 sent_history도 업데이트 (중복 방지)
                    current_type = "high_risk" if abs_rate >= 10.0 else "risk"
                    sent_history.add((stock_name, current_type))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ [{stock_code}] 데이터 조회 중 에러: {e}")