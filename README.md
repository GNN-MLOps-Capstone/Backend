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
docker network inspect crawling_news-network >/dev/null 2>&1 || docker network create crawling_news-network
# proxy-net을 함께 쓰는 환경이면 추가 생성
docker network inspect proxy-net >/dev/null 2>&1 || docker network create proxy-net

# 4. 이미지 빌드
docker compose build

# 5. 앱 실행
# 마이그레이션이 필요하면 MIGRATE_ON_STARTUP=true를 함께 지정해 1회만 실행
docker compose up -d
# 예시: MIGRATE_ON_STARTUP=true docker compose up -d

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

## 로그인 테스트

현재 사용자 API는 `POST /api/users/login`을 제외하면 모두 인증이 필요합니다. 로그인 테스트 방법은 아래 2가지입니다.

### 1. Google ID Token으로 실제 로그인 테스트

1. `.env`에 `GOOGLE_CLIENT_ID`를 설정합니다.
2. 서버 실행 후 `http://localhost:8000/static/google-login-test.html`을 브라우저로 엽니다.
3. Google 로그인 버튼을 눌러 `id_token`과 `/api/users/login` 응답을 확인합니다.
4. 필요하면 Swagger의 `POST /api/users/login`에 아래 형태로 직접 요청합니다.

```json
{
  "id_token": "GOOGLE_ID_TOKEN",
  "nickname": "optional",
  "img_url": null,
  "onesignal_id": null
}
```

5. 응답의 `access_token`을 Swagger 우측 상단 `Authorize`에 `Bearer <access_token>` 형태로 넣고 나머지 API를 테스트합니다.

### 2. 개발 전용 우회 로그인 테스트

Google 토큰 없이 백엔드만 빠르게 테스트하려면 개발 우회를 사용할 수 있습니다.

`.env` 예시:

```env
DEBUG=true
DEV_BYPASS_LOGIN=true
```

개발 우회 엔드포인트:

```bash
curl -X POST "http://localhost:8000/api/users/dev-login" \
  -H "Content-Type: application/json" \
  -d '{
    "google_id": "dev-user-1",
    "email": "dev1@example.com",
    "nickname": "dev1"
  }'
```

- `POST /api/users/dev-login`은 `DEBUG=true` 이고 `DEV_BYPASS_LOGIN=true`일 때만 동작합니다.
- Swagger 문서에는 노출하지 않았습니다.
- 응답으로 받은 `access_token`을 일반 로그인과 동일하게 사용하면 됩니다.

---

## 추가 문서

- [docs/README.md](docs/README.md): `docs` 운영 원칙과 남겨둔 문서 목록
- [docs/recommendation-logging.md](docs/recommendation-logging.md): 추천/상호작용 로깅 기준 문서
- [docs/recommender-api.md](docs/recommender-api.md): 외부 추천 서버 연동 스펙

---

## API 엔드포인트

기본 정책:
- `POST /api/users/login`을 제외한 사용자용 API는 모두 인증이 필요합니다.
- 예시 `curl`에는 생략된 경우를 제외하고 `Authorization: Bearer <access_token>` 헤더를 포함해야 합니다.

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
curl "http://localhost:8000/api/news/simple" \
  -H "Authorization: Bearer <access_token>"

# 검색
curl "http://localhost:8000/api/news/simple?search=삼성전자" \
  -H "Authorization: Bearer <access_token>"
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
curl "http://localhost:8000/api/news/simple?limit=20" \
  -H "Authorization: Bearer <access_token>"
```

---

### 뉴스 상세 조회

```
GET /api/news/{news_id}
```

예시:

```bash
curl http://localhost:8000/api/news/1 \
  -H "Authorization: Bearer <access_token>"
```

---

### 개인화 뉴스 추천 목록

```http
GET /api/news/recommendations
```

| 파라미터 | 타입    | 설명                                 | 기본값 |
| -------- | ------- | ------------------------------------ | ------ |
| user_id  | int     | 호환용 사용자 ID. 전달 시 인증 사용자와 같아야 함 | - |
| limit    | int     | 호환용 파라미터(서버는 항상 20개 고정 반환) | 20 |
| page     | int     | 무한 스크롤 페이지(1부터 시작)        | 1      |
| cursor   | string  | 다음 페이지 커서(전달 시 page 우선순위보다 높음) | - |
| request_id | string | 추천 요청 추적 ID(없으면 서버 생성)   | -      |
| screen_session_id | string | 추천 탭 세션 ID(로깅 연계용) | -      |
| app_session_id | string | 앱 세션 ID(선택)                 | -      |
| log_served | bool  | 추천 응답 DB 로깅 여부                | true   |

예시:

```bash
curl "http://localhost:8000/api/news/recommendations?page=1&screen_session_id=screen-s1" \
  -H "Authorization: Bearer <access_token>"
```

- 인증 필수 엔드포인트입니다.
- `RECOMMENDER_MOCK_MODE=false`에서 2페이지 이상 조회할 때는 반드시 직전 응답의 `next_cursor`를 전달해야 합니다.

응답 예시:

```json
{
  "user_id": 1,
  "request_id": "req-5f8b0d...",
  "source": "recommender",
  "page": 1,
  "next_cursor": "eyJ2IjoxLCJwYWdlIjoyLCJvZmZzZXQiOjEwLCJsaW1pdCI6MTB9",
  "served_count": 20,
  "logged": true,
  "items": [
    {
      "news_id": 1,
      "title": "기사 제목",
      "summary": "기사 요약",
      "pub_date": "2026-02-25T12:34:56",
      "path": "A1"
    }
  ]
}
```

---

### 추천 로그 수집 (탭/뉴스 체류시간)

```http
POST /api/interactions/events
```

- `events`에는 아래 `event_type`을 사용합니다.
- 추천 탭: `screen_view`, `screen_heartbeat`, `screen_leave`
- 뉴스 상세: `content_open`, `content_heartbeat`, `content_leave`
- 추천 요청/응답: `recommendation_request`, `recommendation_response`
- 추천 스크롤: `scroll_depth`

현재 운영 가이드 기준으로는 추천 목록 노출은 `recommendation_serves`로 판단하므로 `recommendation_impression`은 기본적으로 사용하지 않습니다.

예시:

```bash
curl -X POST "http://localhost:8000/api/interactions/events" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "events": [
      {
        "event_id": "evt-1",
        "user_id": 1,
        "event_type": "screen_view",
        "screen_session_id": "screen-s1",
        "request_id": "req-1"
      },
      {
        "event_id": "evt-2",
        "user_id": 1,
        "event_type": "scroll_depth",
        "screen_session_id": "screen-s1",
        "request_id": "req-1",
        "scroll_depth": 62.5,
        "page": 1
      },
      {
        "event_id": "evt-3",
        "user_id": 1,
        "event_type": "content_open",
        "screen_session_id": "screen-s1",
        "content_session_id": "content-c1",
        "request_id": "req-1",
        "news_id": 101,
        "position": 3
      },
      {
        "event_id": "evt-4",
        "user_id": 1,
        "event_type": "content_leave",
        "content_session_id": "content-c1",
        "news_id": 101
      },
      {
        "event_id": "evt-5",
        "user_id": 1,
        "event_type": "screen_leave",
        "screen_session_id": "screen-s1"
      }
    ]
  }'
```

추천 목록 로깅은 `GET /api/news/recommendations`의 `log_served=true`(기본값)로도 자동 저장됩니다.
저장 테이블:
- `recommendation_serves`: 요청 단위(요청 ID, 페이지, source, served_count, `served_items`)
- `interaction_events`: 추천 요청/응답/스크롤/콘텐츠 이벤트 원본 로그

현재 운영 권장안:
- `content_open`에는 `request_id`와 `news_id`를 반드시 포함
- `content_leave`에도 같은 `news_id`를 함께 포함

`POST /api/interactions/events` 응답 필드:
- `accepted`: 저장된 이벤트 수
- `duplicated`: `event_id` 중복으로 스킵된 이벤트 수

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
curl http://localhost:8000/api/stocks/005930/overview \
  -H "Authorization: Bearer <access_token>"
```

### 기간별 시세 (그래프용)

```
GET /api/stocks/{code}/series?range=1d|1w|1m
```

예시:

```bash
# 당일 분봉 (기본 5분 간격으로 변환)
curl "http://localhost:8000/api/stocks/005930/series?range=1d" \
  -H "Authorization: Bearer <access_token>"

# 최근 1주 일봉
curl "http://localhost:8000/api/stocks/005930/series?range=1w" \
  -H "Authorization: Bearer <access_token>"

# 최근 1달 일봉
curl "http://localhost:8000/api/stocks/005930/series?range=1m" \
  -H "Authorization: Bearer <access_token>"
```

### 실시간 현재가 (WebSocket)

```
GET ws://localhost:8000/api/stocks/ws/current?code=005930
```

- WebSocket도 인증이 필요합니다.
- `Authorization: Bearer <access_token>` 헤더 또는 `?access_token=<access_token>` 쿼리로 토큰을 전달합니다.

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
SECRET_KEY=change-me
ALGORITHM=HS256
GOOGLE_CLIENT_ID=실제_구글_OAuth_Web_Client_ID
DEV_BYPASS_LOGIN=false

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
> `GOOGLE_CLIENT_ID`는 Google 로그인 검증에 사용하는 OAuth Client ID입니다.
> `DEV_BYPASS_LOGIN=true`는 개발용 우회 로그인 테스트에서만 사용하세요.

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
