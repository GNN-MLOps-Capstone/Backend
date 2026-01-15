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
from sqlalchemy import Column, BigInteger, String, Text, DateTime, Integer, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import relationship

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
