# Backend 사용 테이블 정리

기준: `app/models.py` + `app/routers/*`

이 문서는 **현재 Backend 코드가 참조/사용하는 테이블만** 정리합니다.

## 1) 뉴스 도메인

1. `naver_news`
- 용도: 뉴스 메타데이터 원본
- 사용: `news` 라우터(목록/상세/추천 후보 조회)

2. `crawled_news`
- 용도: 뉴스 본문/요약 텍스트
- 사용: `news` 라우터(목록/상세 응답 조합)

3. `filtered_news`
- 용도: 정제된 뉴스/감성 데이터
- 사용: `watchlist` 라우터

## 2) 사용자/알림 도메인

1. `users`
- 용도: 사용자 기본 정보
- 사용: `users`, `notifications`

2. `user_settings`
- 용도: 사용자 설정
- 사용: `users`

3. `notifications`
- 용도: 알림 저장/읽음/중요/삭제
- 사용: `notifications`

## 3) 워치리스트/요약 도메인

1. `stock_summary_cache`
- 용도: 종목 요약 캐시
- 사용: `news`, `watchlist`

2. `news_stock_mapping`
- 용도: 종목-뉴스 매핑
- 사용: `news`, `watchlist`

3. `stocks`
- 용도: 종목 기본 정보
- 사용: `watchlist`

## 4) 추천/로깅 도메인

1. `interaction_events`
- 용도: 상호작용 원본 이벤트 로그(append-only)
- 사용: `interactions`

2. `recommendation_serves`
- 용도: 추천 응답 단위 스냅샷 로그(`request_id`, `page`, `served_items`)
- 사용: `news` 추천 API

## 5) Alembic 관련

1. `alembic_version`
- 용도: Alembic 리비전 버전 관리
- 생성: Alembic 실행 시 자동 생성

## 6) 참고

현재 `models.py` 기준 선언 테이블은 아래 11개입니다.

- `naver_news`
- `crawled_news`
- `users`
- `user_settings`
- `stock_summary_cache`
- `news_stock_mapping`
- `filtered_news`
- `notifications`
- `stocks`
- `interaction_events`
- `recommendation_serves`
