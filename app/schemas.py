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
from datetime import datetime
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


# =============================================================================
# 주식 API 스키마
# =============================================================================

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
