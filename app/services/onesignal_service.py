import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import date
from app.config import get_settings
from app.models import Notification
from app.database import AsyncSessionLocal

settings = get_settings()

async def send_volatility_push_and_save(
    user_ids: list[str],
    stock_name: str,
    rate: float,
    date_kst: date,
) -> tuple[bool, bool]:
    """
    위험도(10% 기준)에 따라 메시지를 차별화하여 발송하고 DB에 기록합니다.
    """
    if not user_ids:
        return False, False

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

    os_id = None

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                print(f"❌ OneSignal API 에러: {response.status_code} - {response.text}")
                return False, False
            
            os_id = response.json().get("id")
        except httpx.RequestError as e:
            print(f"OneSignal API 연결 실패: {e}")
            return False, False
        
    if not os_id:
        return False, False
    
    async with AsyncSessionLocal() as db:
        try:
            rows = [
                {
                    "user_id": gid,
                    "type": alert_type,
                    "title": title,
                    "body": body,
                    "is_read": False,
                    "star": False,
                    "stock_name": stock_name,
                    "sentiment_score": rate,
                    "onesignal_notification_id": os_id,
                    "date_kst": date_kst, 
                }
                for gid in user_ids
            ]
            stmt = pg_insert(Notification).values(rows).on_conflict_do_nothing(
                index_elements=["user_id", "stock_name", "type", "date_kst"]
            )
            await db.execute(stmt)
            await db.commit()
            print(f"✅ [{alert_type}] {stock_name} 처리 완료 (푸시ID: {os_id})")
            return True, True
            
        except Exception as e:
            await db.rollback()
            print(f"❌ DB 저장 중 예상치 못한 에러: {e}")
            return True, False