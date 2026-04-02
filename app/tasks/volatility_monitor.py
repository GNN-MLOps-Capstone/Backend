import asyncio
from datetime import datetime
from sqlalchemy import select, func
from app.kis.transformers import KST
from app.database import AsyncSessionLocal 
from app.models import Watchlist, User, Stock, Notification
from app.routers.stocks import _fetch_stock_overview
from app.services.onesignal_service import (
    classify_volatility_type,
    send_volatility_push_and_save,
)

async def run_volatility_check() -> None:
    # 한국 시간대 설정
    now = datetime.now(KST)
    
    # 주말이거나 운영 시간이 아니면 종료
    if now.weekday() >= 5 or not (8 <= now.hour < 20):
        return

    async with AsyncSessionLocal() as db:
        try:
            # 오늘 이미 알림을 보낸 종목명 리스트 가져오기 (중복 방지)
            sent_today_stmt = select(
                Notification.user_id,
                Notification.stock_name,
                Notification.type
            ).where(
                Notification.type.in_(["risk", "high_risk"]),
                Notification.date_kst == now.date(),
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
            all_stocks: list[tuple[str, str]] = result.all()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"❌ 초기 데이터 조회 에러: {e}")
            return
            
    if not all_stocks:
        return

    print(f"감시 시작: {len(all_stocks)}개 종목")

    for stock_code, stock_name in all_stocks:
        now = datetime.now(KST)
        if now.weekday() >= 5 or not (8 <= now.hour < 20):
            print("운영 시간 종료, 감시 루프를 중단합니다.")
            break
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

            current_type = classify_volatility_type(rate)

            # 이 종목의 watcher 전체 조회
            async with AsyncSessionLocal() as db:
                user_stmt = select(User.google_id).distinct().join(
                    Watchlist, User.id == Watchlist.user_id
                ).where(
                    Watchlist.stock_id == stock_code,
                    User.id < 9000
                )
                user_result = await db.execute(user_stmt)
                user_google_ids: list[str] = [row[0] for row in user_result.all()]
            
            if not user_google_ids:
                continue
            def already_notified(google_id: str, stype: str, stock_name: str = stock_name) -> bool:
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
                for google_id in user_google_ids
                if not already_notified(google_id, current_type)
            ]
 
            if not target_users:
                continue
                
            now = datetime.now(KST)
            if now.weekday() >= 5 or not (8 <= now.hour < 20):
                print("운영 시간 종료, 발송을 중단합니다.")
                break
            
            push_sent, db_saved = await send_volatility_push_and_save(
                target_users, stock_name, rate, now.date()
            )

            if not db_saved:
                print(f"[{stock_code}] DB 저장 실패 - 푸시 발송 여부: {push_sent}")
 
            if push_sent:
                for google_id in target_users:
                    sent_history.add((google_id, stock_name, current_type))
 
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ [{stock_code}] 데이터 조회 중 에러: {e}")