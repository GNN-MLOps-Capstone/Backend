from uuid import NAMESPACE_URL, uuid5
import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from datetime import date
from app.config import get_settings
from app.models import Notification
from app.database import AsyncSessionLocal
import logging

logger = logging.getLogger(__name__)

settings = get_settings()

def classify_volatility_type(rate: float) -> str:
    return "high_risk" if abs(rate) >= 10.0 else "risk"

async def send_volatility_push_and_save(
    user_ids: list[str],
    stock_name: str,
    rate: float,
    date_kst: date,
) -> tuple[bool, bool]:
    """
    위험도(10% 기준)에 따라 메시지를 차별화하여 발송하고 DB에 기록합니다.
    """
    normalized_user_ids = sorted(set(user_ids))
    if not normalized_user_ids:
        return False, False
    if not settings.onesignal_app_id or not settings.onesignal_rest_api_key:
        logger.error("OneSignal 설정이 누락되어 변동성 알림 발송을 건너뜁니다.")
        return False, False

    # 1. 위험도 및 메시지 분기
    alert_type = classify_volatility_type(rate)
    is_high_risk = alert_type == "high_risk"
    
    direction = "🚀 급등" if rate > 0 else "📉 급락"
    prefix = "⚠️ [초고변동 경고]" if is_high_risk else "🔔 [변동 알림]"
    
    title = f"{prefix} {stock_name} {direction}"
    body = (
        f"‼️ 주의: {stock_name} 종목이 {rate}%로 폭주 중입니다!" 
        if is_high_risk else 
        f"{stock_name} 종목이 전일 대비 {rate}% {direction} 중입니다."
    )

    url = "https://api.onesignal.com/notifications"
    headers = {
        "Authorization": f"Key {settings.onesignal_rest_api_key}",
        "Content-Type": "application/json; charset=utf-8"
    }

    payload = {
        "app_id": settings.onesignal_app_id,
        "include_aliases": {"external_id": normalized_user_ids},
        "idempotency_key": str(
            uuid5(
                NAMESPACE_URL,
                f"{date_kst}:{alert_type}:{stock_name}:{','.join(normalized_user_ids)}",
            )
        ),
        "target_channel": "push",
        "headings": {"en": title,"ko": title},
        "contents": {"en": body,"ko": body},
        "data": {"type": alert_type, "stock_name": stock_name}
    }

    os_id = None

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                logger.error("OneSignal API 에러(status=%s)", response.status_code)
                return False, False
            
            try:
                body_json = response.json()
            except ValueError:
                logger.exception("OneSignal 응답 JSON 파싱 실패(status=%s)", response.status_code)
                return False, False

            os_id = body_json.get("id")
        except httpx.RequestError as e:
            logger.exception("OneSignal API 연결 실패: %s", e)
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
                for gid in normalized_user_ids
            ]
            stmt = pg_insert(Notification).values(rows).on_conflict_do_nothing()
            await db.execute(stmt)
            await db.commit()
            logger.info(f"[{alert_type}] {stock_name} 처리 완료 (푸시ID: {os_id})")
            return True, True
            
        except SQLAlchemyError as e:
            await db.rollback()
            logger.exception("DB 저장 중 예상치 못한 에러: %s", e)
            return True, False