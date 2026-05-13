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
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Text,
    DateTime,
    Integer,
    ForeignKey,
    Float,
    Boolean,
    Time,
    UniqueConstraint,
    Date,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class InteractionEventType(str, enum.Enum):
    """상호작용 이벤트 타입"""
    screen_view = "screen_view"
    screen_heartbeat = "screen_heartbeat"
    screen_leave = "screen_leave"
    content_open = "content_open"
    content_heartbeat = "content_heartbeat"
    content_leave = "content_leave"
    recommendation_request = "recommendation_request"
    recommendation_response = "recommendation_response"
    recommendation_impression = "recommendation_impression"
    scroll_depth = "scroll_depth"


# PostgreSQL에 이미 존재하는 enum 타입과 연결 (create_type=False)
ProcessStatusEnum = PG_ENUM(
    ProcessStatus,
    name='process_status_enum',
    create_type=False,  # DB에 이미 존재하므로 생성하지 않음
)

InteractionEventTypeEnum = PG_ENUM(
    InteractionEventType,
    name="interaction_event_type_enum",
    create_type=False,
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


class NewsDomainMapping(Base):
    """
    뉴스 도메인과 언론사명 매핑 테이블
    """

    __tablename__ = "news_domain_mapping"

    domain = Column(String(255), primary_key=True)
    news_company_name = Column(String(255), nullable=False)


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
    # 마지막 로그인
    last_login = Column(DateTime(timezone=True), server_default=func.now())

    settings = relationship("UserSettings",back_populates="user",uselist=False,cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    watchlist_items = relationship("Watchlist", back_populates="user", cascade="all, delete-orphan")

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
    extractor_version = Column(String(50), nullable=True)
    weight = Column(Float, nullable=True, default=1.0)
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
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Keyword(Base):
    __tablename__ = "keywords"

    keyword_id = Column(Integer, primary_key=True, index=True)
    word = Column(String(100), nullable=False, unique=True, index=True)


class NewsKeywordMapping(Base):
    __tablename__ = "news_keyword_mapping"
    __table_args__ = (
        UniqueConstraint("news_id", "keyword_id", name="uq_news_keyword_mapping_news_keyword"),
    )

    mapping_id = Column(Integer, primary_key=True, index=True)
    news_id = Column(BigInteger, ForeignKey("naver_news.news_id", ondelete="CASCADE"), nullable=False, index=True)
    keyword_id = Column(Integer, ForeignKey("keywords.keyword_id", ondelete="CASCADE"), nullable=False)
    extractor_version = Column(String(50), nullable=True)
    weight = Column(Float, nullable=True, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Stock(Base):
    """
    종목 정보 테이블

    종목명, 업종, AI 요약 등 종목 메타데이터를 저장합니다.
    """
    __tablename__ = "stocks"

    stock_id = Column(String(20), primary_key=True, index=True)
    stock_name = Column(String(200), nullable=True)
    industry = Column(String(200), nullable=True)
    summary_text = Column(Text, nullable=True)
    market_cap = Column(BigInteger, nullable=True, index=True)

    watchlist_items = relationship("Watchlist", back_populates="stock")

    def __repr__(self):
        return f"<Stock(stock_id={self.stock_id}, stock_name={self.stock_name})>"


class Alias(Base):
    __tablename__ = "aliases"
    __table_args__ = (UniqueConstraint("alias_name", name="uq_aliases_alias_name"),)

    alias_id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(String(20), ForeignKey("stocks.stock_id", ondelete="CASCADE"), nullable=False, index=True)
    alias_name = Column(String(100), nullable=False)

class Notification(Base):
    """
    알림 테이블 (notifications)
    
    앱에서 보낸 알림 이력과 읽음 상태를 저장합니다.
    """
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("onesignal_notification_id", "user_id", name="uq_notification_onesignal_user"),
        UniqueConstraint("user_id", "stock_name", "type", "date_kst", name="uq_notification_daily"),
    )

    id = Column(Integer, primary_key=True, index=True)
    onesignal_notification_id = Column(String(255), nullable=False, index=True)
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
    date_kst = Column(Date, nullable=False, index=True)

    # 관계 설정: User 모델과 양방향 연결
    user = relationship("User", back_populates="notifications")

    def __repr__(self):
        return f"<Notification(id={self.id}, type={self.type}, title={self.title})>"


class Watchlist(Base):
    """
    관심종목 테이블
    """
    __tablename__ = "watchlist"

    __table_args__ = (UniqueConstraint("user_id", "stock_id", name="uq_watchlist_user_stock"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(String(20), ForeignKey("stocks.stock_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    stock = relationship("Stock", back_populates="watchlist_items")
    user = relationship("User", back_populates="watchlist_items")

    def __repr__(self):
        return f"<Watchlist(id={self.id}, user_id={self.user_id}, stock_id={self.stock_id})>"


class InteractionEvent(Base):
    """
    추천/콘텐츠 상호작용 원본 이벤트 로그 (append-only)
    """

    __tablename__ = "interaction_events"

    id = Column(BigInteger, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    device_id = Column(String(255), nullable=True)
    app_session_id = Column(String(255), nullable=True, index=True)
    event_type = Column(InteractionEventTypeEnum, nullable=False, index=True)
    screen_session_id = Column(String(64), nullable=True, index=True)
    content_session_id = Column(String(64), nullable=True, index=True)
    news_id = Column(BigInteger, nullable=True, index=True)
    request_id = Column(String(128), nullable=True, index=True)
    position = Column(Integer, nullable=True)
    page = Column(Integer, nullable=True)
    scroll_depth = Column(Float, nullable=True)
    event_ts_client = Column(DateTime(timezone=True), nullable=True)
    event_ts_server = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RecommendationServe(Base):
    """
    추천 목록 응답 단위 로그
    """

    __tablename__ = "recommendation_serves"
    __table_args__ = (
        UniqueConstraint("request_id", "page", name="uq_recommendation_serves_request_page"),
    )

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(128), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    screen_session_id = Column(String(64), nullable=True, index=True)
    app_session_id = Column(String(255), nullable=True, index=True)
    source = Column(String(50), nullable=False)
    page = Column(Integer, nullable=False, default=1)
    limit = Column(Integer, nullable=False)
    served_count = Column(Integer, nullable=False, default=0)
    is_mock = Column(Boolean, nullable=False, default=False)
    served_items = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OnboardingTheme(Base):
    """온보딩 투자 테마"""
    __tablename__ = "onboarding_themes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    display_order = Column(Integer, nullable=False, default=0)

    categories = relationship(
        "OnboardingThemeCategory",
        back_populates="theme",
        cascade="all, delete-orphan",
        order_by="OnboardingThemeCategory.id",
    )


class OnboardingThemeCategory(Base):
    """온보딩 테마별 DB 카테고리 매핑"""
    __tablename__ = "onboarding_theme_categories"

    id = Column(Integer, primary_key=True, index=True)
    theme_id = Column(
        Integer,
        ForeignKey("onboarding_themes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_name = Column(String(200), nullable=False)  # stocks.industry 값과 매핑

    theme = relationship("OnboardingTheme", back_populates="categories")


class UserOnboardingKeyword(Base):
    """사용자 온보딩 관심 키워드"""
    __tablename__ = "user_onboarding_keywords"
    __table_args__ = (
        UniqueConstraint("user_id", "keyword_id", name="uk_user_onboarding_keyword"),
        Index("idx_uok_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.keyword_id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    original_keyword = Column(String(50), nullable=False)

    user = relationship("User")
    keyword = relationship("Keyword")


class RecommendationNewsPathMetrics(Base):
    """
    추천 경로별 뉴스 체류 메트릭 집계 테이블
    """

    __tablename__ = "recommendation_news_path_metrics"

    bucket_start = Column(DateTime, primary_key=True, nullable=False, index=True)
    bucket_end = Column(DateTime, primary_key=True, nullable=False, index=True)
    news_id = Column(BigInteger, primary_key=True, index=True)
    path = Column(String(16), primary_key=True, nullable=False)
    dwell_event_count = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
