# News API Backend

> 실시간 뉴스 API 서버 - 캡스톤 프로젝트

Flutter 앱에서 뉴스 데이터를 가져오기 위한 REST API 서버입니다.

---

## 폴더 구조

```
backend/
├── app/                    # 메인 애플리케이션 코드
│   ├── __init__.py         # 패키지 선언
│   ├── main.py             # 서버 시작점 (FastAPI 앱)
│   ├── config.py           # 설정 관리 (DB 주소, 포트 등)
│   ├── database.py         # DB 연결 및 세션 관리
│   ├── models.py           # DB 테이블 정의
│   ├── schemas.py          # API 요청/응답 형식 정의
│   └── routers/            # API 엔드포인트
│       ├── __init__.py
│       └── news.py         # 뉴스 관련 API
├── scripts/                # 유틸리티 스크립트
│   └── init_db.py          # DB 초기화 (CSV -> DB)
├── data/                   # 데이터 저장 폴더
│   └── news.db             # SQLite DB 파일 (자동 생성)
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

# 4. DB 초기화 (CSV 데이터 로드)
python scripts/init_db.py

# 5. 서버 실행
uvicorn app.main:app --reload --port 8000
```

### 방법 2: Docker 실행 (권장)

```bash
# 1. Backend 폴더로 이동
cd Backend

# 2. Docker Compose로 실행
docker-compose up -d --build

# 3. 로그 확인
docker-compose logs -f news-api

# 4. 중지
docker-compose down
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

## 환경 설정

### 환경 변수 (.env)

```env
# 데이터베이스 연결 정보
DATABASE_URL=sqlite+aiosqlite:///./news.db

# 서버 설정
HOST=0.0.0.0
PORT=8000
DEBUG=True

# CORS 허용 출처 (쉼표로 구분)
CORS_ORIGINS=http://localhost:3000,http://localhost:51151
```

### 실제 서버 DB 연결하기

PostgreSQL이나 MySQL에 연결하려면 `DATABASE_URL`을 변경하세요:

```env
# PostgreSQL
DATABASE_URL=postgresql+asyncpg://사용자:비밀번호@서버주소:5432/DB이름

# MySQL
DATABASE_URL=mysql+aiomysql://사용자:비밀번호@서버주소:3306/DB이름
```

---

## 개발 가이드

### 새로운 API 추가하기

1. `app/schemas.py`에 요청/응답 스키마 정의
2. `app/routers/news.py`에 엔드포인트 추가
3. 필요시 `app/models.py`에 DB 모델 수정

### DB 모델 변경 후

```bash
# DB 파일 삭제 후 재생성
rm data/news.db
python scripts/init_db.py
```

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
| SQLite     | 경량 DB (개발용)            |
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

### Q: DB 초기화가 안 돼요

```bash
# CSV 파일 경로 확인
ls ../App/lib/dummy_data.csv

# 직접 경로 지정
python scripts/init_db.py --csv-path /path/to/file.csv
```

### Q: Docker에서 DB가 초기화 안 돼요

```bash
# data 폴더 권한 확인
chmod -R 777 data/

# 볼륨 삭제 후 재시작
docker-compose down -v
docker-compose up -d --build
```
