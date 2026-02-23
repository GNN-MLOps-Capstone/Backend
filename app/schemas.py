"""
==============================================================================
API 스키마 정의 (schemas.py)
==============================================================================

이 파일은 API에서 주고받는 데이터의 형식을 정의합니다.

현재 구현:
    - NewsSimpleResponse: 앱 뉴스 목록용 (title, summary)
    - naver_news.pub_date 기준 정렬
    - crawled_news.text를 summary로 사용

==============================================================================
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime,time
import re

class NewsSimpleResponse(BaseModel):
    """
    간단한 뉴스 응답 스키마 (Flutter 앱용)
    
    Flutter 앱의 뉴스 목록 화면에서 사용합니다.
    
    API 응답 예시:
    {
        "news_id": 100,
        "title": "삼성전자 실적 발표",
        "summary": "삼성전자가 분기 실적을...",
        "pub_date": "2026-01-15T10:30:00"
    }
    """
    news_id: int
    title: str
    summary: Optional[str] = None  # crawled_news.text를 summary로 사용
    pub_date: Optional[datetime] = None
    
    # ---------------------------------------------------------
    # TODO: 크롤링 구현 후 신문사 추가
    # press: Optional[str] = None
    # ---------------------------------------------------------
    
    # ---------------------------------------------------------
    # TODO: 감성분석 구현 후 추가
    # sentiment: Optional[str] = None  # 긍정/부정/중립
    # ---------------------------------------------------------
    
    class Config:
        from_attributes = True


class NewsListResponse(BaseModel):
    """
    뉴스 목록 응답 스키마
    
    페이지네이션 정보와 뉴스 목록을 함께 반환합니다.
    """
    total: int
    items: list[NewsSimpleResponse]


class NewsDetailResponse(BaseModel):
    """
    뉴스 상세 응답 스키마
    
    뉴스 상세 조회 시 사용합니다.
    """
    news_id: int
    title: str
    summary: Optional[str] = None
    pub_date: Optional[datetime] = None
    url: Optional[str] = None
    
    class Config:
        from_attributes = True

class StockSummaryResponse(BaseModel):
    """
    요약 정보 스키마

    종목별 3줄 요약을 받아올 때 사용합니다.
    """
    stock_name: str
    summary: str
    last_updated: datetime
    message: str

    class Config:
        from_attributes = True

class UserLoginRequest(BaseModel):
    google_id: str
    email: str     
    nickname: str 
    img_url: Optional[str] = None

class UserUpdateRequest(BaseModel):
    """
    설정 값 스키마

    설정 값 변경할 때 사용합니다.
    """
    push: Optional[bool] = None
    risk_only: Optional[bool] = None
    positive_only: Optional[bool] = None
    interest_only: Optional[bool] = None
    night_push_prohibit: Optional[bool] = None
    dnd_start: Optional[time] = None
    dnd_finish: Optional[time] = None

class UserResponse(BaseModel):
    """
    유저 정보 응답 스키마

    유저 정보를 반환합니다.
    """
    id: int
    google_id: str
    email: str
    nickname: str
    img_url: Optional[str] = None
    class Config:
        from_attributes = True

class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user: UserResponse

class SettingResponse(BaseModel):
    """
    설정 정보 응답 스키마

    설정값을 반환합니다.
    """
    push: bool
    risk_only: bool
    positive_only: bool
    interest_only: bool
    night_push_prohibit: bool
    dnd_start: Optional[time]
    dnd_finish: Optional[time]

    class Config:
        from_attributes = True

class NotificationCreateRequest(BaseModel):
    """
    N-0: 알림 저장 요청 스키마 (앱 -> 서버)
    
    앱이 OneSignal 발송 성공 후 서버에 저장을 요청할 때 사용합니다.
    """
    type: str
    title: str
    body: Optional[str] = None


class NotificationResponse(BaseModel):
    """
    N-1: 알림 내역 응답 스키마
    
    알림 목록 조회 시 반환되는 형태입니다.
    DB의 'is_read' 컬럼을 API 명세서에 맞춰 'read'로 내보냅니다.
    """
    id: int
    type: str
    title: str
    body: Optional[str] = None
    read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationReadRequest(BaseModel):
    """
    N-2: 읽음 처리 요청 스키마
    
    id가 있으면 해당 알림만, 없으면(null) 전체 읽음 처리합니다.
    """
    id: Optional[int] = None


class NotificationCountResponse(BaseModel):
    """
    N-2: 읽음 처리 응답 스키마
    
    처리가 끝난 후 남은 '안 읽은 알림 개수'를 반환합니다.
    """
    unread_count: int

# =============================================================================
# 주식 API 스키마
# =============================================================================

# =============================================================================
# 관심종목 API 스키마
# =============================================================================

class WatchlistAddRequest(BaseModel):
    """관심종목 추가 요청 스키마"""
    code: str


class WatchlistStockResponse(BaseModel):
    """
    관심종목 종목 정보 응답 스키마

    Flutter 앱의 WatchlistStock 모델과 필드명을 맞춥니다.
    """
    code: str
    name: str
    weather: str        # SUNNY | CLOUDY | RAINY
    price: int
    changeRate: float
    keyword: str
    aiSummary: str

    class Config:
        from_attributes = True


class WatchlistBriefingResponse(BaseModel):
    """관심종목 AI 브리핑 응답 스키마"""
    text: str
    topIssues: list[str]

    class Config:
        from_attributes = True


class StockOverviewResponse(BaseModel):
    code: str
    name: Optional[str] = None
    last_price: Optional[int] = None
    change: Optional[float] = None
    change_rate: Optional[float] = None
    open: Optional[int] = None
    high: Optional[int] = None
    low: Optional[int] = None
    volume: Optional[int] = None
    trading_value: Optional[int] = None
    updated_at: datetime


class StockSeriesPoint(BaseModel):
    t: int
    o: int
    h: int
    l: int
    c: int
    v: int


class StockSeriesMeta(BaseModel):
    source: str
    interval: str


class StockSeriesResponse(BaseModel):
    code: str
    range: str
    tz: str
    currency: str
    points: list[StockSeriesPoint]
    meta: StockSeriesMeta


class StockSeriesQuery(BaseModel):
    range: str
    from_date: Optional[str] = None  # YYYYMMDD 형식
    to_date: Optional[str] = None    # YYYYMMDD 형식

    @field_validator("from_date", "to_date")
    @classmethod
    def _validate_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not re.fullmatch(r"\\d{8}", value):
            raise ValueError("date must be in YYYYMMDD format")
        return value
