# News API Backend

> 실시간 뉴스 API 서버 - 캡스톤 프로젝트

Flutter 앱에서 뉴스 데이터를 가져오기 위한 REST API 서버입니다.
원격 PostgreSQL 데이터베이스에 연결하여 크롤링된 뉴스 데이터를 제공합니다.

---

## 폴더 구조

```
backend/
├── app/                    # 메인 애플리케이션 코드
│   ├── __init__.py         # 패키지 선언
│   ├── main.py             # 서버 시작점 (FastAPI 앱)
│   ├── config.py           # 설정 관리 (DB 주소, 포트 등)
│   ├── database.py         # DB 연결 및 세션 관리
│   ├── kis/                # KIS Open API 연동 모듈
│   │   ├── __init__.py
│   │   ├── cache.py         # TTL 캐시
│   │   ├── client.py        # HTTP 클라이언트 래퍼
│   │   ├── errors.py        # KIS 오류 타입
│   │   ├── token_manager.py # 접근토큰 관리
│   │   └── transformers.py  # 응답 변환기
│   ├── models.py           # DB 테이블 정의
│   ├── schemas.py          # API 요청/응답 형식 정의
│   └── routers/            # API 엔드포인트
│       ├── __init__.py
│       ├── news.py         # 뉴스 관련 API
│       └── stocks.py       # 주식 관련 API
├── Dockerfile              # Docker 이미지 설정
├── docker-compose.yml      # Docker Compose 설정
├── entrypoint.sh           # 컨테이너 시작 스크립트
├── requirements.txt        # Python 패키지 목록
├── .env                    # 환경 변수 (Git 제외)
├── .env.example            # 환경 변수 예시
└── README.md               # 이 파일
```

---

## 빠른 시작

### 방법 1: 로컬 실행 (개발용)

```bash
# 1. backend 폴더로 이동
cd Backend

# 2. 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

# 3. 패키지 설치
pip install -r requirements.txt

# 4. 환경 변수 설정 (.env 파일 생성)
cp .env.example .env
# .env 파일에서 DATABASE_URL 설정

# 5. 마이그레이션 적용
alembic -c alembic.ini upgrade head

# 6. 서버 실행
uvicorn app.main:app --reload --port 8000
```

### 방법 2: Docker 실행 (권장)

```bash
# 1. Backend 폴더로 이동
cd Backend

# 2. 환경 변수 설정
cp .env.example .env
# .env 파일에서 DATABASE_URL 설정

# 3. 외부 Docker 네트워크 준비(최초 1회)
docker network create crawling_news-network
# proxy-net을 함께 쓰는 환경이면 추가 생성
docker network create proxy-net

# 4. Docker Compose로 실행
docker compose up -d --build

# 5. 마이그레이션 적용
docker compose exec news-api alembic -c alembic.ini upgrade head

# 6. 로그 확인
docker compose logs -f news-api

# 7. 중지
docker compose down
```

### Alembic 마이그레이션 명령어

```bash
# 최신 스키마로 업그레이드
alembic -c alembic.ini upgrade head

# 현재 리비전 확인
alembic -c alembic.ini current

# 신규 리비전 생성(자동감지)
alembic -c alembic.ini revision --autogenerate -m "describe-change"
```

---

## API 접속

서버 실행 후 아래 주소로 접속:

| 주소                         | 설명                  |
| ---------------------------- | --------------------- |
| http://localhost:8000        | API 서버              |
| http://localhost:8000/docs   | API 문서 (Swagger UI) |
| http://localhost:8000/redoc  | API 문서 (ReDoc)      |
| http://localhost:8000/health | 서버 상태 확인        |

---

## API 엔드포인트

### 뉴스 목록 조회

```
GET /api/news
```

| 파라미터  | 타입   | 설명                  | 기본값 |
| --------- | ------ | --------------------- | ------ |
| page      | int    | 페이지 번호           | 1      |
| page_size | int    | 페이지당 개수         | 20     |
| search    | string | 검색어                | -      |
| sentiment | string | 감성 필터 (긍정/부정) | -      |
| press     | string | 신문사 필터           | -      |

예시:

```bash
# 전체 목록
curl http://localhost:8000/api/news

# 검색
curl "http://localhost:8000/api/news?search=삼성전자"

# 긍정 뉴스만
curl "http://localhost:8000/api/news?sentiment=긍정"
```

---

### 간단한 뉴스 목록 (앱용)

```
GET /api/news/simple
```

| 파라미터 | 타입   | 설명        | 기본값 |
| -------- | ------ | ----------- | ------ |
| limit    | int    | 가져올 개수 | 50     |
| search   | string | 검색어      | -      |

예시:

```bash
curl "http://localhost:8000/api/news/simple?limit=20"
```

---

### 뉴스 상세 조회

```
GET /api/news/{news_id}
```

예시:

```bash
curl http://localhost:8000/api/news/1
```

---

### 개인화 뉴스 추천 목록

```
GET /api/news/recommendations
```

| 파라미터 | 타입    | 설명                                 | 기본값 |
| -------- | ------- | ------------------------------------ | ------ |
| user_id  | string  | 추천 대상 사용자 ID                  | -      |
| limit    | int     | 가져올 추천 뉴스 개수                 | 20     |
| page     | int     | 무한 스크롤 페이지(1부터 시작)        | 1      |
| cursor   | string  | 다음 페이지 커서(전달 시 page 우선순위보다 높음) | - |
| request_id | string | 추천 요청 추적 ID(없으면 서버 생성)   | -      |
| screen_session_id | string | 추천 탭 세션 ID(로깅 연계용) | -      |
| app_session_id | string | 앱 세션 ID(선택)                 | -      |
| log_served | bool  | 추천 응답 DB 로깅 여부                | true   |

예시:

```bash
curl "http://localhost:8000/api/news/recommendations?user_id=test-user&limit=10&page=1&screen_session_id=screen-s1"
```

응답 예시:

```json
{
  "user_id": "test-user",
  "request_id": "req-5f8b0d...",
  "source": "recommender",
  "page": 1,
  "next_cursor": "eyJ2IjoxLCJwYWdlIjoyLCJvZmZzZXQiOjEwLCJsaW1pdCI6MTB9",
  "served_count": 1,
  "logged": true,
  "items": [
    {
      "news_id": 1,
      "title": "기사 제목",
      "summary": "기사 요약",
      "pub_date": "2026-02-25T12:34:56",
      "score": null,
      "reason": null
    }
  ]
}
```

---

### 추천 로그 수집 (탭/뉴스 체류시간)

```
POST /api/interactions/events
POST /api/interactions/finalize-timeouts
```

- `events`에는 아래 `event_type`을 사용합니다.
- 추천 탭: `screen_view`, `screen_heartbeat`, `screen_leave`
- 뉴스 상세: `content_open`, `content_heartbeat`, `content_leave`
- 추천 요청/응답: `recommendation_request`, `recommendation_response`
- 추천 노출/스크롤: `recommendation_impression`, `scroll_depth`

예시:

```bash
curl -X POST "http://localhost:8000/api/interactions/events" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {
        "event_id": "evt-1",
        "user_id": "user-1",
        "event_type": "screen_view",
        "screen_session_id": "screen-s1",
        "request_id": "req-1"
      },
      {
        "event_id": "evt-2",
        "user_id": "user-1",
        "event_type": "recommendation_impression",
        "screen_session_id": "screen-s1",
        "request_id": "req-1",
        "news_id": 101,
        "position": 1,
        "page": 1
      },
      {
        "event_id": "evt-2-1",
        "user_id": "user-1",
        "event_type": "scroll_depth",
        "screen_session_id": "screen-s1",
        "request_id": "req-1",
        "scroll_depth": 62.5,
        "page": 1
      },
      {
        "event_id": "evt-3",
        "user_id": "user-1",
        "event_type": "content_open",
        "screen_session_id": "screen-s1",
        "content_session_id": "content-c1",
        "news_id": 101,
        "position": 3
      },
      {
        "event_id": "evt-4",
        "user_id": "user-1",
        "event_type": "content_leave",
        "content_session_id": "content-c1"
      },
      {
        "event_id": "evt-5",
        "user_id": "user-1",
        "event_type": "screen_leave",
        "screen_session_id": "screen-s1"
      }
    ]
  }'
```

추천 목록 로깅은 `GET /api/news/recommendations`의 `log_served=true`(기본값)로도 자동 저장됩니다.
저장 테이블:
- `recommendation_serves`: 요청 단위(요청 ID, 페이지, source, served_count)
- `recommendation_serve_items`: 응답된 뉴스 ID/position 목록
- `recommendation_feedback`: 추천 학습용 하이브리드 피드백(노출/클릭/체류)

`POST /api/interactions/events` 응답 필드:
- `feedback_updated`: 하이브리드 피드백 upsert 건수

타임아웃 세션 종료 처리 예시:

```bash
curl -X POST "http://localhost:8000/api/interactions/finalize-timeouts?grace_seconds=30"
```

---

### 뉴스 통계

```
GET /api/news/stats/summary
```

응답 예시:

```json
{
  "total": 1000,
  "by_sentiment": {"긍정": 600, "부정": 300, "중립": 100},
  "by_press_top10": {"한경": 200, "조선일보": 150, ...}
}
```

---

## 주식 시세 (KIS Open API)

### 현재가 요약 (상단 카드)

```
GET /api/stocks/{code}/overview
```

예시:

```bash
curl http://localhost:8000/api/stocks/005930/overview
```

### 기간별 시세 (그래프용)

```
GET /api/stocks/{code}/series?range=1d|1w|1m
```

예시:

```bash
# 당일 분봉 (기본 5분 간격으로 변환)
curl "http://localhost:8000/api/stocks/005930/series?range=1d"

# 최근 1주 일봉
curl "http://localhost:8000/api/stocks/005930/series?range=1w"

# 최근 1달 일봉
curl "http://localhost:8000/api/stocks/005930/series?range=1m"
```

### 실시간 현재가 (WebSocket)

```
GET ws://localhost:8000/api/stocks/ws/current?code=005930
```

응답 예시:

```json
{
  "code": "005930",
  "time": "103015",
  "price": 152100,
  "change": 0.0,
  "change_rate": 0.0,
  "open": 154900,
  "high": 156400,
  "low": 151500,
  "volume": 20285661,
  "trading_value": 3106296973350
}
```

---

## 환경 설정

### 환경 변수 (.env)

```env
# 데이터베이스 연결 정보 (PostgreSQL)
DATABASE_URL=postgresql+asyncpg://사용자:비밀번호@서버주소:5432/DB이름

# 서버 설정
HOST=0.0.0.0
PORT=8000
DEBUG=True

# CORS 허용 출처 (쉼표로 구분)
CORS_ORIGINS=http://localhost:3000,http://localhost:51151

# KIS Open API
KIS_BASE_URL=https://openapi.koreainvestment.com:9443
KIS_APP_KEY=발급받은_APP_KEY
KIS_APP_SECRET=발급받은_APP_SECRET
KIS_WS_BASE_URL=ws://ops.koreainvestment.com:21000
KIS_WS_PATH=/tryitout
```

> KIS APP_KEY/APP_SECRET은 반드시 backend/.env에서만 관리하세요. (클라이언트 노출 금지)

### 데이터베이스 연결

이 API는 **원격 PostgreSQL 데이터베이스**에 연결하여 크롤링된 뉴스 데이터를 제공합니다.

```env
# PostgreSQL 연결 예시
DATABASE_URL=postgresql+asyncpg://data_user:password@example.com:5432/news_db
```

> **Note**: `.env` 파일은 Git에서 제외됩니다. `.env.example`을 참고하여 설정하세요.

---

## 개발 가이드

### 새로운 API 추가하기

1. `app/schemas.py`에 요청/응답 스키마 정의
2. `app/routers/news.py`에 엔드포인트 추가
3. 필요시 `app/models.py`에 DB 모델 수정

### DB 모델 변경 시 주의사항

원격 PostgreSQL DB의 기존 테이블(`naver_news`, `crawled_news`)을 사용합니다.
모델 변경 시 원격 DB 스키마와 일치하는지 확인하세요.

---

## Docker 명령어 모음

```bash
# 빌드 후 실행
docker-compose up -d --build

# 로그 실시간 확인
docker-compose logs -f news-api

# 컨테이너 접속
docker exec -it news-api /bin/bash

# 중지
docker-compose down

# 볼륨 포함 전체 삭제 (데이터 삭제됨!)
docker-compose down -v
```

---

## 기술 스택

| 기술       | 설명                        |
| ---------- | --------------------------- |
| FastAPI    | 고성능 비동기 웹 프레임워크 |
| SQLAlchemy | Python ORM                  |
| Pydantic   | 데이터 검증                 |
| PostgreSQL | 프로덕션 데이터베이스       |
| asyncpg    | PostgreSQL 비동기 드라이버  |
| Docker     | 컨테이너화                  |

---

## Flutter 앱 연동

Flutter 앱에서 이 API를 사용하려면:

1. `lib/services/news_api_service.dart` 파일 확인
2. `_baseUrl`을 서버 주소로 설정
3. `NewsApiService.getNewsList()` 등 메서드 사용

```dart
// Flutter에서 사용 예시
final news = await NewsApiService.getNewsList(limit: 20);
```

---

## 문제 해결

### Q: 서버가 시작되지 않아요

```bash
# 포트 충돌 확인
lsof -i :8000

# 다른 포트로 실행
uvicorn app.main:app --port 8001
```

### Q: DB 연결이 안 돼요

```bash
# .env 파일에 DATABASE_URL이 올바르게 설정되었는지 확인
cat .env

# asyncpg 설치 확인
pip install asyncpg
```

### Q: Docker에서 DB 연결이 안 돼요

```bash
# .env 파일 확인
cat .env

# 컨테이너 재빌드
docker-compose down
docker-compose up -d --build

# 로그 확인
docker-compose logs -f news-api
```
