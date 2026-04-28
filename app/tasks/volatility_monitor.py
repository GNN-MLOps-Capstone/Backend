import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.kis.transformers import KST
from app.database import AsyncSessionLocal 
from app.models import Watchlist, User, Stock, Notification, UserSettings
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
            sent_history: set[tuple[str, str, str]] = {
                (row[0], row[1], row[2]) for row in sent_result.all()
            }

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

            active_threshold = now - timedelta(days=7)

            # 이 종목의 watcher 전체 조회
            async with AsyncSessionLocal() as db:
                user_stmt = (
                    select(
                        User.google_id, 
                        UserSettings.positive_only, # 상승 알림 여부
                        UserSettings.risk_only      # 하락 알림 여부
                    )
                    .distinct()
                    .join(Watchlist, User.id == Watchlist.user_id)
                    .join(UserSettings, User.id == UserSettings.user_id) # 설정 테이블 조인
                    .where(
                        Watchlist.stock_id == stock_code,
                        User.id < 9000,
                        UserSettings.push.is_(True),
                        User.last_login >= active_threshold
                    )
                )
                user_result = await db.execute(user_stmt)
                user_configs = user_result.all()
            
            if not user_configs:
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

            target_users = []
            for gid, pos_only, risk_only in user_configs:
                
                # 1. 상승 중인데 유저가 상승 알림(positive_only)을 꺼두었다면 패스
                if rate > 0 and not pos_only:
                    continue
                
                # 2. 하락 중인데 유저가 하락 알림(risk_only)을 꺼두었다면 패스
                if rate < 0 and not risk_only:
                    continue
                
                # 3. 이미 오늘 알림을 받았다면 패스
                if already_notified(gid, current_type):
                    continue
                
                target_users.append(gid)

            if not target_users:
                continue
                
            now = datetime.now(KST)
            if now.weekday() >= 5 or not (8 <= now.hour < 20):
                print("운영 시간 종료, 발송을 중단합니다.")
                break
            
            push_sent, db_saved = await send_volatility_push_and_save(
                user_ids=target_users,
                stock_name=stock_name,
                date_kst=now.date(),
                rate=rate,
                alert_type=current_type,
            )

            if db_saved:
                # 1. DB 저장이 성공했다면, 설령 푸시가 실패했더라도 중복 방지 리스트에 추가
                for google_id in target_users:
                    sent_history.add((google_id, stock_name, current_type))
                
                if push_sent:
                    print(f"✅ [{stock_name}] {len(target_users)}명에게 변동성 알림 발송 완료")
                else:
                    # 아이폰 Never Subscribed 등 푸시 호출은 안 됐지만 기록은 남은 경우
                    print(f"⚠️ [{stock_name}] 푸시는 실패했으나 DB 기록 완료 (앱 내 내역 생성)")
            else:
                # 2. DB 저장이 실패했다면 다음 주기에 다시 시도되도록 sent_history에 넣지 않음
                if push_sent:
                    print(f"[{stock_name}] 푸시는 이미 갔는데 DB 저장 실패! 다음 루프 중복 발송 위험")
                else:
                    print(f"[{stock_name}] 푸시 및 DB 저장 모두 실패")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ [{stock_code}] 데이터 조회 중 에러: {e}")