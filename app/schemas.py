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

from pydantic import BaseModel
from typing import Optional
from datetime import datetime,time

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
    """
    유저 정보 스키마

    새로운 유저가 등장해 유저 정보 저장할 때 사용합니다.
    """
    google_id: str
    email: str
    nickname: Optional[str] = None
    profile_image: Optional[str] = None

class UserUpdateRequest(BaseModel):
    """
    설정 값 스키마

    설정 값 변경할 때 사용합니다.
    """
    push_alarm: Optional[bool] = None
    risk_push_alarm: Optional[bool] = None
    positive_push_alarm: Optional[bool] = None
    interest_push_alarm: Optional[bool] = None
    night_push_prohibit: Optional[bool] = None
    night_push_start: Optional[time] = None
    night_push_end: Optional[time] = None

class UserResponse(BaseModel):
    """
    유저 정보 응답 스키마

    유저 정보를 반환합니다.
    """
    google_id: str
    email: str
    nickname: Optional[str]
    profile_image: Optional[str]

    class Config:
        from_attributes = True

class SettingResponse(BaseModel):
    """
    설정 정보 응답 스키마

    설정값을 반환합니다.
    """
    push_alarm: bool
    risk_push_alarm: bool
    positive_push_alarm: bool
    interest_push_alarm: bool
    night_push_prohibit: bool
    night_push_start: Optional[time]
    night_push_end: Optional[time]

    class Config:
        from_attributes = True