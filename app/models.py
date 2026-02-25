"""
==============================================================================
데이터베이스 모델 정의 (models.py)
==============================================================================

이 파일은 DB 테이블의 구조를 정의합니다.

현재 사용하는 테이블:
    1. naver_news - 네이버 뉴스 원본 데이터
    2. crawled_news - 크롤링한 뉴스 본문 데이터

테이블 관계:
    crawled_news.news_id -> naver_news.news_id (FK, 1:1 관계)

==============================================================================
"""

import enum
from sqlalchemy import Column, BigInteger, String, Text, DateTime, Integer, ForeignKey, Float, Boolean, Time
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import time

from app.database import Base


# =============================================================================
# PostgreSQL Enum 타입 정의
# =============================================================================
#
# DB에 이미 정의된 'process_status_enum' 타입과 매핑됩니다.
# DB의 enum 값: pending, url_filtered, to_crawl, crawling, 
#              crawl_success, crawl_failed, crawl_skipped
#
class ProcessStatus(enum.Enum):
    """크롤링 처리 상태를 나타내는 Enum"""
    pending = "pending"
    url_filtered = "url_filtered"
    to_crawl = "to_crawl"
    crawling = "crawling"
    crawl_success = "crawl_success"
    crawl_failed = "crawl_failed"
    crawl_skipped = "crawl_skipped"


# PostgreSQL에 이미 존재하는 enum 타입과 연결 (create_type=False)
ProcessStatusEnum = PG_ENUM(
    ProcessStatus,
    name='process_status_enum',
    create_type=False,  # DB에 이미 존재하므로 생성하지 않음
)


class NaverNews(Base):
    """
    네이버 뉴스 테이블
    
    API로 수집한 뉴스 메타데이터를 저장합니다.
    """
    
    __tablename__ = "naver_news"
    
    # PK
    news_id = Column(BigInteger, primary_key=True, index=True)
    
    # 뉴스 정보
    title = Column(String(500), nullable=False)
    pub_date = Column(DateTime, nullable=False)
    url = Column(String(1000), nullable=False, unique=True)
    search_keyword = Column(String(200), nullable=True)
    
    # 크롤링 상태 (PostgreSQL enum 타입 사용)
    crawl_status = Column(ProcessStatusEnum, default=ProcessStatus.pending)
    crawl_attempt_count = Column(Integer, default=0)
    
    # 타임스탬프
    api_request_date = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    
    # 관계 설정: crawled_news와 1:1 관계
    crawled_news = relationship(
        "CrawledNews", 
        back_populates="naver_news", 
        uselist=False,
        foreign_keys="CrawledNews.news_id"
    )
    
    def __repr__(self):
        return f"<NaverNews(news_id={self.news_id}, title={self.title[:30] if self.title else 'None'}...)>"


class CrawledNews(Base):
    """
    크롤링된 뉴스 테이블
    
    뉴스 본문 데이터를 저장합니다.
    """
    
    __tablename__ = "crawled_news"
    
    # PK
    crawled_news_id = Column(BigInteger, primary_key=True, index=True)
    
    # FK - naver_news 테이블 참조 (1:1 관계)
    news_id = Column(BigInteger, ForeignKey("naver_news.news_id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # 뉴스 본문 (앱에서 summary로 사용)
    text = Column(Text, nullable=True)
    
    # 크롤링 정보
    crawled_at = Column(DateTime)
    crawler_version = Column(String(50))
    response_time_ms = Column(Integer)
    
    # 필터 정보
    filter_status = Column(String(50), default='pending')
    filter_version = Column(String(50))
    filtered_at = Column(DateTime)
    filter_reason = Column(String(100))
    
    # 타임스탬프
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    
    # 관계 설정: naver_news와 1:1 관계
    naver_news = relationship("NaverNews", back_populates="crawled_news")
    
    def __repr__(self):
        return f"<CrawledNews(crawled_news_id={self.crawled_news_id}, news_id={self.news_id})>"

class User(Base):
    """
    유저 테이블
    
    유저 정보 을 저장합니다.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    google_id = Column(String, unique=True, index=True, nullable=False)

    # 프로필 정보
    email = Column(String, nullable=False)
    nickname = Column(String)
    img_url = Column(Text)

    role = Column(String, nullable=True)
    onesignal_id = Column(String(255), nullable=True, default=None)
    # 유저정보 생성시간
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    settings = relationship("UserSettings",back_populates="user",uselist=False,cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email}, name={self.nickname})>"
    
class UserSettings(Base):
    """
    사용자 알림 및 개인 설정
    """

    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer,ForeignKey("users.id", ondelete="CASCADE"),unique=True,nullable=False)

    # 알람 푸시설정
    push = Column(Boolean, default=True)

    # 알람 푸시 세부설정
    risk_only = Column(Boolean, default=True)
    positive_only = Column(Boolean, default=False)
    interest_only = Column(Boolean, default=False)

    # 야간 방해금지모드(True시 야간금지모드, False시 해제)
    night_push_prohibit = Column(Boolean, default=False) 
    
    # 야간 방해금지 하는 시간
    dnd_start = Column(Time, default=time(23, 0, 0)) 
    dnd_finish = Column(Time, default=time(7, 0, 0))

    user = relationship("User", back_populates="settings")

    def __repr__(self):
        return f"<UserSettings(user_id={self.user_id})>"

    
class StockSummaryCache(Base):
    __tablename__ = "stock_summary_cache"

    stock_id = Column(String(20), primary_key=True, index=True)
    stock_name = Column(String(100), unique=True)
    latest_news_id = Column(Integer, nullable=True)
    summary_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 관계 설정
    news_mappings = relationship("NewsStockMapping", back_populates="stock")

class NewsStockMapping(Base):
    __tablename__ = "news_stock_mapping"

    mapping_id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(String(20), ForeignKey("stock_summary_cache.stock_id"), nullable=False)
    news_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 관계 설정
    stock = relationship("StockSummaryCache", back_populates="news_mappings")

class FilteredNews(Base):
    __tablename__ = "filtered_news"
    news_id = Column(Integer, primary_key=True, index=True)
    summary = Column(Text, nullable=True)
    refined_text = Column(Text, nullable=True)
    sentiment = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Notification(Base):
    """
    알림 테이블 (notifications)
    
    앱에서 보낸 알림 이력과 읽음 상태를 저장합니다.
    """
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.google_id", ondelete="CASCADE"), nullable=False, index=True)
    
    type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    star = Column(Boolean, default=False)
    stock_name = Column(String(255), nullable=True)
    sentiment_score = Column(Float, nullable=True)
    
    # 생성 시간 (자동 입력)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 관계 설정: User 모델과 양방향 연결
    user = relationship("User", back_populates="notifications")

    def __repr__(self):
        return f"<Notification(id={self.id}, type={self.type}, title={self.title})>"
    
class Stock(Base):
    """
    주식 종목 테이블 (stocks)
    
    종목 코드(또는 ID)와 종목명을 저장합니다.
    """
    __tablename__ = "stocks"

    # 만약 stock_id가 '005930' 같은 문자열 종목코드라면 String(20) 등으로 변경하세요.
    stock_id = Column(String(20), primary_key=True, index=True) 
    stock_name = Column(String(100), nullable=False, unique=True, index=True)

    def __repr__(self):
        return f"<Stock(stock_id={self.stock_id}, stock_name={self.stock_name})>"