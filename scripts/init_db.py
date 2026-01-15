#!/usr/bin/env python3
"""
==============================================================================
데이터베이스 초기화 스크립트 (init_db.py)
==============================================================================

이 스크립트는 CSV 파일의 뉴스 데이터를 DB에 넣어줍니다.

사용 목적:
    1. 처음 서버를 세팅할 때 초기 데이터 삽입
    2. 테스트 데이터 준비
    3. 크롤링된 CSV 데이터를 DB로 이관

실행 방법:
    # backend 폴더에서 실행
    python scripts/init_db.py
    
    # CSV 파일 경로 직접 지정
    python scripts/init_db.py --csv-path /path/to/data.csv

실행 순서:
    1. DB 테이블 생성 (없으면)
    2. 기존 뉴스 데이터 삭제
    3. CSV 파일 읽기
    4. 데이터 DB에 삽입
    5. 결과 확인

주의:
    이 스크립트를 실행하면 기존 뉴스 데이터가 모두 삭제됩니다!

==============================================================================
"""

import asyncio  # 비동기 실행용
import sys
import os
from pathlib import Path

# =============================================================================
# 경로 설정
# =============================================================================
# 이 스크립트가 scripts/ 폴더 안에 있으므로,
# 상위 폴더(backend/)를 Python 경로에 추가해야
# app 패키지를 import할 수 있습니다.
#
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd  # CSV 파일 읽기용
from sqlalchemy import select, delete  # SQL 쿼리 빌더

# app 패키지에서 필요한 것들 import
from app.database import engine, AsyncSessionLocal, Base
from app.models import News


# =============================================================================
# 테이블 초기화 함수
# =============================================================================
async def init_database():
    """
    데이터베이스 테이블 생성
    
    models.py에 정의된 모든 테이블을 DB에 생성합니다.
    이미 있는 테이블은 건너뜁니다 (데이터 손실 없음).
    """
    async with engine.begin() as conn:
        # create_all: 모든 테이블 생성 (없는 것만)
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Database tables created")


# =============================================================================
# 기존 데이터 삭제 함수
# =============================================================================
async def clear_news_table():
    """
    news 테이블의 모든 데이터 삭제
    
    새 데이터를 넣기 전에 기존 데이터를 지웁니다.
    
    주의: 이 함수를 실행하면 모든 뉴스가 삭제됩니다!
    """
    async with AsyncSessionLocal() as session:
        # DELETE FROM news;
        await session.execute(delete(News))
        await session.commit()  # 변경사항 확정
    print("[OK] Cleared existing news data")


# =============================================================================
# CSV 데이터 로드 함수
# =============================================================================
async def load_csv_to_db(csv_path: str):
    """
    CSV 파일을 읽어서 DB에 저장
    
    Parameters:
        csv_path: CSV 파일 경로
    
    CSV 파일 형식 (컬럼명):
        - crawled_news_id: 크롤링 ID
        - title: 제목
        - pub_date: 발행일
        - text: 본문
        - tfidf_keywords: 키워드
        - matched_stock_title: 종목명(제목)
        - matched_stock_text: 종목명(본문)
        - 신문사: 신문사
        - summary: 요약
        - pn: 감성 (긍정/부정)
    """
    
    # =========================================================================
    # CSV 파일 읽기
    # =========================================================================
    print(f"[INFO] Reading CSV file: {csv_path}")
    df = pd.read_csv(csv_path)
    
    print(f"[INFO] Found {len(df)} news articles")
    
    # =========================================================================
    # 컬럼명 매핑
    # =========================================================================
    # CSV 컬럼명 -> DB 컬럼명 변환
    # 예: '신문사' -> 'press', 'pn' -> 'sentiment'
    #
    column_mapping = {
        'crawled_news_id': 'crawled_news_id',
        'title': 'title',
        'pub_date': 'pub_date',
        'text': 'text',
        'tfidf_keywords': 'tfidf_keywords',
        'matched_stock_title': 'matched_stock_title',
        'matched_stock_text': 'matched_stock_text',
        '신문사': 'press',      # 한글 -> 영어
        'summary': 'summary',
        'pn': 'sentiment',      # pn -> sentiment
    }
    
    # 컬럼명 변경
    df = df.rename(columns=column_mapping)
    
    # =========================================================================
    # 데이터 삽입
    # =========================================================================
    async with AsyncSessionLocal() as session:
        inserted_count = 0
        
        # DataFrame의 각 행을 순회
        for _, row in df.iterrows():
            # News 객체 생성
            news = News(
                crawled_news_id=int(row.get('crawled_news_id', 0)),
                title=str(row.get('title', '')),
                pub_date=str(row.get('pub_date', '')),
                text=str(row.get('text', '')),
                tfidf_keywords=str(row.get('tfidf_keywords', '')),
                matched_stock_title=str(row.get('matched_stock_title', '')),
                matched_stock_text=str(row.get('matched_stock_text', '')),
                press=str(row.get('press', '')),
                summary=str(row.get('summary', '')),
                sentiment=str(row.get('sentiment', '')),
            )
            
            # 세션에 추가 (아직 DB에 저장 안 됨)
            session.add(news)
            inserted_count += 1
            
            # -----------------------------------------------------------------
            # 배치 커밋
            # -----------------------------------------------------------------
            # 100개마다 한 번씩 DB에 저장
            # 한 번에 모든 데이터를 저장하면 메모리 문제가 생길 수 있음
            #
            if inserted_count % 100 == 0:
                await session.commit()
                print(f"  [PROGRESS] Inserted {inserted_count} articles...")
        
        # 마지막 남은 데이터 저장
        await session.commit()
        print(f"[OK] Successfully inserted {inserted_count} news articles")


# =============================================================================
# 데이터 검증 함수
# =============================================================================
async def verify_data():
    """
    삽입된 데이터 확인
    
    처음 3개의 뉴스를 출력하여 데이터가 제대로 들어갔는지 확인합니다.
    """
    async with AsyncSessionLocal() as session:
        # 처음 3개 뉴스 조회
        result = await session.execute(select(News).limit(3))
        news_items = result.scalars().all()
        
        print("\n[INFO] Sample data verification:")
        for news in news_items:
            print(f"  - [{news.sentiment}] {news.title[:50]}...")


# =============================================================================
# 메인 함수
# =============================================================================
async def main(csv_path: str = None):
    """
    메인 실행 함수
    
    Parameters:
        csv_path: CSV 파일 경로 (없으면 기본 경로 사용)
    """
    # =========================================================================
    # CSV 파일 경로 설정
    # =========================================================================
    # 인자로 경로가 주어지지 않으면 기본 경로 사용
    # 기본 경로: ../App/lib/dummy_data.csv
    #
    if csv_path is None:
        csv_path = str(
            Path(__file__).parent.parent.parent / "App" / "lib" / "dummy_data.csv"
        )
    
    # 파일 존재 확인
    if not os.path.exists(csv_path):
        print(f"[ERROR] CSV file not found: {csv_path}")
        sys.exit(1)  # 에러 코드 1로 종료
    
    print("[START] Starting database initialization...\n")
    
    # =========================================================================
    # 실행 순서
    # =========================================================================
    
    # 1. 테이블 생성
    await init_database()
    
    # 2. 기존 데이터 삭제
    await clear_news_table()
    
    # 3. CSV 데이터 로드
    await load_csv_to_db(csv_path)
    
    # 4. 결과 확인
    await verify_data()
    
    print("\n[DONE] Database initialization complete!")


# =============================================================================
# 스크립트 실행
# =============================================================================
# 이 파일을 직접 실행할 때만 아래 코드가 실행됩니다.
# 다른 파일에서 import하면 실행되지 않습니다.
#
if __name__ == "__main__":
    import argparse
    
    # -------------------------------------------------------------------------
    # 명령줄 인자 파싱
    # -------------------------------------------------------------------------
    # argparse: 명령줄 인자를 쉽게 처리하는 라이브러리
    #
    # 사용법:
    #   python scripts/init_db.py                      # 기본 경로 사용
    #   python scripts/init_db.py --csv-path /path/to/file.csv  # 경로 지정
    #
    parser = argparse.ArgumentParser(
        description="Initialize database with CSV data"
    )
    parser.add_argument(
        "--csv-path",
        type=str,
        help="Path to CSV file (default: ../App/lib/dummy_data.csv)"
    )
    args = parser.parse_args()
    
    # -------------------------------------------------------------------------
    # 비동기 함수 실행
    # -------------------------------------------------------------------------
    # asyncio.run(): 비동기 함수를 동기 환경에서 실행
    #
    asyncio.run(main(args.csv_path))
