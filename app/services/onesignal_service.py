import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.config import get_settings
from app.models import Notification

settings = get_settings()

async def send_volatility_push_and_save(db: AsyncSession, user_ids: list, stock_name: str, rate: float):
    """
    위험도(10% 기준)에 따라 메시지를 차별화하여 발송하고 DB에 기록합니다.
    """
    if not user_ids:
        return None

    # 1. 위험도 및 메시지 분기
    is_high_risk = abs(rate) >= 10.0
    alert_type = "high_risk" if is_high_risk else "risk"
    
    direction = "🚀 급등" if rate > 0 else "📉 급락"
    prefix = "⚠️ [초고변동 경고]" if is_high_risk else "🔔 [변동 알림]"
    
    title = f"{prefix} {stock_name} {direction}"
    body = (
        f"‼️ 주의: {stock_name} 종목이 {rate}%로 폭주 중입니다!" 
        if is_high_risk else 
        f"{stock_name} 종목이 전일 대비 {rate}% {direction} 중입니다."
    )

    url = "https://onesignal.com/api/v1/notifications"
    headers = {
        "Authorization": f"Basic {settings.onesignal_rest_api_key}",
        "Content-Type": "application/json; charset=utf-8"
    }

    payload = {
        "app_id": settings.onesignal_app_id,
        "include_external_user_ids": user_ids,
        "headings": {"en": title,"ko": title},
        "contents": {"en": body,"ko": body},
        "data": {"type": alert_type, "stock_name": stock_name}
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                print(f"❌ OneSignal API 에러: {response.status_code} - {response.text}")
                return None
            
            os_id = response.json().get("id")
            if not os_id:
                print("❌ OneSignal 응답에 ID가 없습니다")
                return None
            
            # 2. 비동기 방식의 DB 대량 저장 (add_all 사용)
            new_notifications = []
            for gid in user_ids:
                new_notifications.append(Notification(
                    user_id=gid,
                    type=alert_type,
                    title=title,
                    body=body,
                    is_read=False,
                    star=False,
                    stock_name=stock_name,
                    sentiment_score=rate,
                    onesignal_notification_id=os_id
                ))
            
            db.add_all(new_notifications)
            await db.commit() # ✅ 비동기 커밋
            
            print(f"✅ [{alert_type}] {stock_name} 처리 완료 (푸시ID: {os_id})")
            return os_id
                
        except httpx.RequestError as e:
            # 네트워크 연결이나 타임아웃 관련 에러만 따로 처리
            print(f"OneSignal API 연결 실패: {e}")
            return None

        except IntegrityError as e:
            # DB 제약 조건 위반 (예: 필수값 누락 등)
            await db.rollback()
            print(f"DB 데이터 무결성 에러: {e}")
            return None

        except Exception as e:
            # 그 외 우리가 예상치 못한 진짜 "치명적인" 에러
            await db.rollback()
            print(f"예상치 못한 시스템 에러: {e}")
            return None