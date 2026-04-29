# Backend 단위 테스트 보고서 — 1~2주차

**기간:** 2026.04.02 ~ 2026.04.08 , 2026.4.12~2026.04.13
**작성일:** 2026.04.14
**담당:** 하성우
**결과:** ✅ 전체 통과 (102 / 102)

---

## 1. 환경 셋업

| 항목 | 내용 |
|------|------|
| 테스트 프레임워크 | `pytest 8.4.2` |
| 비동기 지원 | `pytest-asyncio 1.3.0` |
| 커버리지 측정 | `pytest-cov 7.0.0` |
| HTTP 모킹 | `respx 0.22.0` |
| 내부 함수 모킹 | `unittest.mock` (Python 내장) |
| 테스트 DB | SQLite 인메모리 (`aiosqlite`) |
| 실행 환경 | Docker (Python 3.11.15) |

### 주요 셋업 이슈 및 해결

| 이슈 | 원인 | 해결 |
|------|------|------|
| `SQLite JSONB` 미지원 에러 | `RecommendationServe.served_items`가 PostgreSQL 전용 `JSONB` 타입 | `conftest.py`에서 `JSONB`를 SQLite 호환 `Text`로 패치 |
| Pydantic v1 deprecated 문법 경고 | `Field(env=...)`, `class Config` 사용 | `SettingsConfigDict`, `model_config = ConfigDict(...)` 로 교체 |

---

## 2. 테스트 파일 구조

```text
tests/
├── test_models.py      # DB 모델 제약조건 검증
├── test_schemas.py     # Pydantic 스키마 검증
├── test_kis.py         # KIS API 에러 처리 및 캐시 레이어
└── test_weather.py     # 감성 날씨 산출 및 AI 트렌드
```

---

## 3. 테스트 결과 상세

### 3-1. 데이터 모델 테스트

#### User / UserSettings (`test_models.py`)

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 유저 생성 성공 | ✅ |
| 2 | created_at 자동 설정 | ✅ |
| 3 | google_id unique 제약 | ✅ |
| 4 | google_id 필수 | ✅ |
| 5 | email 필수 | ✅ |
| 6 | onesignal_id nullable | ✅ |
| 7 | __repr__ 형식 확인 | ✅ |
| 8 | UserSettings 기본값 확인 | ✅ |
| 9 | dnd 시간 기본값 | ✅ |
| 10 | 설정값 변경 | ✅ |
| 11 | user_id unique 제약 | ✅ |

**소계: 11 / 11**

#### Watchlist / Stock (`test_models.py`)

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 관심종목 추가 | ✅ |
| 2 | 관심종목 중복 제약 (uq_watchlist_user_stock) | ✅ |
| 3 | created_at 자동 설정 | ✅ |
| 4 | 여러 종목 추가 | ✅ |
| 5 | __repr__ 형식 확인 | ✅ |
| 6 | 종목 생성 | ✅ |
| 7 | nullable 필드 확인 | ✅ |

**소계: 7 / 7**

---

### 3-2. Pydantic 스키마 테스트 (`test_schemas.py`)

#### UserLoginRequest / DevLoginRequest

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 정상 요청 | ✅ |
| 2 | 전체 필드 | ✅ |
| 3 | id_token 필수 | ✅ |
| 4 | onesignal_id 최대길이 (255자 초과 불가) | ✅ |
| 5 | onesignal_id 255자 허용 | ✅ |
| 6 | google_id 빈문자열 불가 | ✅ |
| 7 | google_id 최대길이 초과 | ✅ |
| 8 | email 필수 | ✅ |
| 9 | email 최소길이 | ✅ |
| 10 | optional 필드 | ✅ |

**소계: 10 / 10**

#### UserResponse / UserUpdateRequest / SettingResponse / WatchlistAddRequest

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | UserResponse 정상 응답 | ✅ |
| 2 | UserResponse ORM 호환 (from_attributes) | ✅ |
| 3 | UserResponse 필수 필드 누락 | ✅ |
| 4 | UserUpdateRequest 모두 optional | ✅ |
| 5 | UserUpdateRequest 부분 업데이트 | ✅ |
| 6 | UserUpdateRequest dnd 시간 파싱 | ✅ |
| 7 | UserUpdateRequest 잘못된 bool 타입 | ✅ |
| 8 | SettingResponse 정상 응답 | ✅ |
| 9 | SettingResponse ORM 호환 | ✅ |
| 10 | WatchlistAddRequest 정상 요청 | ✅ |
| 11 | WatchlistAddRequest code 필수 | ✅ |
| 12 | WatchlistAddRequest 다양한 종목코드 | ✅ |

**소계: 12 / 12** (스키마 전체: **23 / 23**)

---

### 3-3. KIS API 에러 처리 및 캐시 테스트 (`test_kis.py`)

#### KISError

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 기본 생성 | ✅ |
| 2 | 전체 필드 | ✅ |
| 3 | Exception 상속 | ✅ |

**소계: 3 / 3**

#### TTLCache

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | set 후 get 성공 | ✅ |
| 2 | 없는 키는 None | ✅ |
| 3 | TTL 만료 후 None | ✅ |
| 4 | TTL 0 이하 저장 안 됨 | ✅ |
| 5 | 덮어쓰기 | ✅ |
| 6 | 여러 키 독립적 | ✅ |
| 7 | 다양한 값 타입 | ✅ |

**소계: 7 / 7**

#### KISClient._is_retriable_error

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | TimeoutException 재시도 가능 | ✅ |
| 2 | RequestError 재시도 가능 | ✅ |
| 3 | 429 재시도 가능 | ✅ |
| 4 | 500 재시도 가능 | ✅ |
| 5 | 503 재시도 가능 | ✅ |
| 6 | EGW00201 재시도 가능 | ✅ |
| 7 | 400 재시도 불가 | ✅ |
| 8 | 401 재시도 불가 | ✅ |
| 9 | 일반 Exception 재시도 불가 | ✅ |

**소계: 9 / 9**

#### KISClient.request (respx + unittest.mock)

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 정상 응답 (rt_cd=0) | ✅ |
| 2 | HTTP 401 → KISError 발생 | ✅ |
| 3 | HTTP 500 → KISError 발생 | ✅ |
| 4 | rt_cd 비정상 → KISError 발생 | ✅ |
| 5 | JSON 파싱 실패 → KISError(502) | ✅ |
| 6 | base_url 미설정 → KISError(500) | ✅ |
| 7 | 타임아웃 → KISError(502) | ✅ |

**소계: 7 / 7**

---

### 3-4. 감성 날씨 산출 및 AI 트렌드 테스트 (`test_weather.py`)

#### get_weather() — 순수 함수

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 주가 급락 + 감성 없음 → THUNDERSTORM | ✅ |
| 2 | 주가 -5% + 감성 없음 → THUNDERSTORM | ✅ |
| 3 | 주가 소폭하락 + 감성 없음 → RAINY | ✅ |
| 4 | 주가 -1% + 감성 없음 → RAINY | ✅ |
| 5 | 주가 보합 + 감성 없음 → CLOUDY | ✅ |
| 6 | 주가 소폭상승 + 감성 없음 → PARTLY_CLOUDY | ✅ |
| 7 | 주가 +5% + 감성 없음 → SUNNY | ✅ |
| 8 | 주가 급등 + 감성 없음 → SUNNY | ✅ |
| 9 | 주가 None + 감성 없음 → CLOUDY | ✅ |
| 10 | 주가 보합 + 감성 긍정 → PARTLY_CLOUDY | ✅ |
| 11 | 주가 보합 + 감성 부정 → RAINY | ✅ |
| 12 | 주가 소폭상승 + 감성 긍정 → SUNNY | ✅ |
| 13 | 주가 소폭하락 + 감성 부정 → THUNDERSTORM | ✅ |
| 14 | 주가 급락 + 감성 긍정 → RAINY | ✅ |
| 15 | 주가 급등 + 감성 부정 → PARTLY_CLOUDY | ✅ |
| 16 | 감성 0.0 → 중립 처리 | ✅ |
| 17 | 경계값 -1% | ✅ |
| 18 | 경계값 +1% | ✅ |
| 19 | 경계값 -5% | ✅ |
| 20 | 경계값 +5% | ✅ |
| 21 | 주가 0.99% → 보합 | ✅ |
| 22 | 주가 -0.99% → 보합 | ✅ |

**소계: 22 / 22**

#### get_stock_weather() — DB + KIS 모킹

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | stock_id로 CLOUDY 반환 | ✅ |
| 2 | stock_id로 SUNNY 반환 | ✅ |
| 3 | 종목 없으면 404 | ✅ |
| 4 | stock_id, stock_name 둘 다 없으면 ValueError | ✅ |
| 5 | stock_name으로 조회 성공 | ✅ |
| 6 | stock_name 중복이면 400 | ✅ |
| 7 | change_rate 없으면 감성만으로 날씨 결정 | ✅ |

**소계: 7 / 7**

#### get_ai_trends() — DB 모킹

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 뉴스 없으면 빈 리스트 | ✅ |
| 2 | top3 반환 | ✅ |
| 3 | rank 순서 정확 | ✅ |
| 4 | top_n 개수 제한 | ✅ |
| 5 | 감성 데이터 없는 종목 0점 처리 | ✅ |
| 6 | 반환 필드 구조 검증 | ✅ |

**소계: 6 / 6**

---

## 4. 전체 집계

| 분류 | 테스트 파일 | 전체 | 통과 | 실패 |
|------|-----------|------|------|------|
| DB 모델 | test_models.py | 18 | 18 | 0 |
| Pydantic 스키마 | test_schemas.py | 23 | 23 | 0 |
| KIS API / 캐시 | test_kis.py | 26 | 26 | 0 |
| 감성 날씨 / AI 트렌드 | test_weather.py | 35 | 35 | 0 |
| **합계** | | **102** | **102** | **0** |

---

## 5. 실행 환경 검증

| 환경 | Python 버전 | 결과 |
|------|------------|------|
| Docker (Linux) | 3.11.15 | ✅ 102 passed |