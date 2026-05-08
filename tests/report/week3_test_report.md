# Backend 단위 테스트 보고서 — 3주차

**기간:** 2025.04.23 ~ 2025.04.29
**작성일:** 2025.04.29
**담당:** 이상진
**결과:** ✅ 전체 통과 (71 / 71)
---

## 1. 테스트 파일 구조

tests/
├── test_kis_rest_rate_limiter.py
├── test_kis_token_manager.py (변경)
├── test_router_endpoints.py
├── test_stock_service.py (변경)
└── test_weather.py (변경)

---

## 2. 일정별 작업 내역

### 4/23 ~ 4/24 — 인증 플로우 통합 테스트

주식 시세 조회 및 종목 테마 키워드 · 연관 종목 추천 API 엔드포인트 단위 테스트 - 임베딩 데이터 없는 종목 404 케이스 포함

### 4/25 ~ 4/26 — 주식 플로우 통합 테스트

뉴스 목록 조회 및 추천 API 엔드포인트 단위 테스트

### 4/27 ~ 4/28 — 주식 플로우 통합 테스트

유저 인증 · 관심종목 CRUD · 푸시 알림 발송 · 사용자 인터랙션 로깅 API 엔드포인트 단위 테스트

---

## 3. 테스트 결과 상세

### 3-1. KIS REST 유량 제한 (`test_kis_rest_rate_limiter.py`)

#### TestKISRateLimitConfig

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 모의투자 URL은 초당 2회로 추론 | ✅ |
| 2 | 실전 URL은 초당 20회로 추론 | ✅ |
| 3 | 명시적 rate limit은 URL 추론보다 우선 | ✅ |
| 4 | 설정값 없으면 URL로 rate limit 추론 | ✅ |

**소계: 4 / 4**

#### TestSharedKISRateLimiterUsage

| # | 테스트명 | 결과 |
|---|---------|------|
| 5 | 클라이언트와 서비스가 같은 유량제한 진입점을 공유 | ✅ |

**소계: 1 / 1**

---

### 3-2. KIS 토큰 갱신 (`test_kis_token_manager.py`)

#### TestTokenManager

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 유효한 캐시 토큰 재사용 | ✅ |
| 2 | 만료 임박 토큰은 재발급 | ✅ |
| 3 | 동시 요청에도 토큰은 한 번만 발급 | ✅ |
| 4 | 토큰 발급 재시도 후 성공 | ✅ |
| 5 | 토큰 응답 필수 필드 누락 시 에러 | ✅ |

**소계: 5 / 5**

#### TestKISClientCommunication

| # | 테스트명 | 결과 |
|---|---------|------|
| 6 | 재시도 가능 에러 후 정상 응답 | ✅ |
| 7 | 요청 헤더 구성 확인 | ✅ |

**소계: 2 / 2**

---

### 3-3. 주식 서비스 / 개요 조회 (`test_stock_service.py`)

#### TestAuthDependency

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | JWT sub를 DB 조회 없이 현재 주체로 반환 | ✅ |

**소계: 1 / 1**

#### TestKISRealtimeService

| # | 테스트명 | 결과 |
|---|---------|------|
| 2 | 현재가 조회 성공 시 파싱 및 캐시 | ✅ |
| 3 | API 실패 시 만료된 캐시라도 fallback | ✅ |

**소계: 2 / 2**

#### TestStockOverviewService

| # | 테스트명 | 결과 |
|---|---------|------|
| 4 | 비정상 rt_cd는 KISError | ✅ |
| 5 | 최근 유효 일봉 포인트 반환 | ✅ |
| 6 | 현재가가 0이면 일봉으로 fallback | ✅ |
| 7 | 개요 조회는 캐시를 재사용 | ✅ |

**소계: 4 / 4**

#### TestFetchStockOverview

| # | 테스트명 | 결과 |
|---|---------|------|
| 8 | 동일한 종목 요청은 inflight 작업을 공유 | ✅ |
| 9 | 성공한 개요 조회는 캐시를 재사용 | ✅ |

**소계: 2 / 2**

---

### 3-4. 라우터 엔드포인트 (`test_router_endpoints.py`)

#### TestStockRouterEndpoints — 주식 라우터

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 주식 개요 조회 — mocked KIS 시세 사용 | ✅ |
| 2 | 연관 종목 — 공출현 추천 반환 | ✅ |
| 3 | 연관 종목 — 임베딩 데이터 없음 → 404 | ✅ |
| 4 | 테마 키워드 — 랭킹 키워드 반환 | ✅ |
| 5 | 테마 키워드 — 키워드 데이터 없음 → 404 | ✅ |

**소계: 5 / 5**

#### TestNewsRouterEndpoints — 뉴스 라우터

| # | 테스트명 | 결과 |
|---|---------|------|
| 6 | 뉴스 목록 — 최근 필터링 뉴스 반환 | ✅ |
| 7 | 뉴스 추천 — 아이템 반환 및 서빙 로그 저장 | ✅ |

**소계: 2 / 2**

#### TestUserWatchlistNotificationInteractionEndpoints — 유저 · 관심종목 · 알림 · 인터랙션

| # | 테스트명 | 결과 |
|---|---------|------|
| 8 | 로그인 — 토큰 발급 및 OneSignal ID 저장 | ✅ |
| 9 | 로그인 후 프로필 · 설정 조회 플로우 | ✅ |
| 10 | 관심종목 CRUD — mocked KIS 시세 사용 | ✅ |
| 11 | 알림 생성 · 목록 · 읽음 · 토글 · 삭제 플로우 | ✅ |
| 12 | 변동성 푸시 — OneSignal 호출 및 알림 DB 저장 | ✅ |
| 13 | 인터랙션 수집 및 중복 감지 | ✅ |
| 14 | 인터랙션 — mismatched user_id 거부 | ✅ |

**소계: 7 / 7**

---

### 3-5. 감성 날씨 / AI 트렌드 (`test_weather.py`)

#### TestGetWeather — 날씨 로직

| # | 테스트명 | 결과 |
|---|---------|------|
| 1 | 주가 급락 · 감성 없음 → THUNDERSTORM | ✅ |
| 2 | 주가 -5% · 감성 없음 → THUNDERSTORM | ✅ |
| 3 | 주가 소폭 하락 · 감성 없음 → RAINY | ✅ |
| 4 | 주가 -1% · 감성 없음 → RAINY | ✅ |
| 5 | 주가 보합 · 감성 없음 → CLOUDY | ✅ |
| 6 | 주가 소폭 상승 · 감성 없음 → PARTLY_CLOUDY | ✅ |
| 7 | 주가 +5% · 감성 없음 → SUNNY | ✅ |
| 8 | 주가 급등 · 감성 없음 → SUNNY | ✅ |
| 9 | 주가 None · 감성 없음 → CLOUDY | ✅ |
| 10 | 주가 보합 · 감성 긍정 → PARTLY_CLOUDY | ✅ |
| 11 | 주가 보합 · 감성 부정 → RAINY | ✅ |
| 12 | 주가 소폭 상승 · 감성 긍정 → SUNNY | ✅ |
| 13 | 주가 소폭 하락 · 감성 부정 → THUNDERSTORM | ✅ |
| 14 | 주가 급락 · 감성 긍정 → RAINY | ✅ |
| 15 | 주가 급등 · 감성 부정 → PARTLY_CLOUDY | ✅ |
| 16 | 감성 0 중립 처리 | ✅ |
| 17 | 주가 경계값 -1 | ✅ |
| 18 | 주가 경계값 +1 | ✅ |
| 19 | 주가 경계값 -5 | ✅ |
| 20 | 주가 경계값 +5 | ✅ |
| 21 | 주가 0.99 보합 | ✅ |
| 22 | 주가 -0.99 보합 | ✅ |

**소계: 22 / 22**

#### TestGetStockWeather — 종목 날씨 API

| # | 테스트명 | 결과 |
|---|---------|------|
| 23 | stock_id로 CLOUDY 반환 | ✅ |
| 24 | stock_id로 SUNNY 반환 | ✅ |
| 25 | 종목 없으면 → 404 | ✅ |
| 26 | stock_id · stock_name 둘 다 없으면 → ValueError | ✅ |
| 27 | stock_name으로 조회 성공 | ✅ |
| 28 | stock_name 중복이면 → 400 | ✅ |
| 29 | change_rate 없으면 감성만으로 날씨 결정 | ✅ |
| 30 | KIS error를 HTTPException으로 변환 | ✅ |

**소계: 8 / 8**

#### TestGetAiTrends — AI 트렌드

| # | 테스트명 | 결과 |
|---|---------|------|
| 31 | 뉴스 없으면 빈 리스트 반환 | ✅ |
| 32 | top3 반환 | ✅ |
| 33 | rank 순서 정확 | ✅ |
| 34 | top_n 개수 제한 | ✅ |
| 35 | 감성 데이터 없는 종목 0점 처리 | ✅ |
| 36 | 반환 필드 구조 검증 | ✅ |

**소계: 6 / 6**

---

## 4. 전체 집계

| 분류 | 테스트 파일 | 전체 | 통과 | 실패 | 소요 시간 |
|------|-----------|------|------|------|-----------|
| KIS REST 유량 제한 | `test_kis_rest_rate_limiter.py` | 5 | 5 | 0 | 2.59s |
| KIS 토큰 갱신 | `test_kis_token_manager.py` | 7 | 7 | 0 | 4.09s |
| 주식 서비스 / 개요 조회 | `test_stock_service.py` | 9 | 9 | 0 | 4.12s |
| 라우터 엔드포인트 | `test_router_endpoints.py` | 14 | 14 | 0 | 4.23s |
| 감성 날씨 / AI 트렌드 | `test_weather.py` | 36 | 36 | 0 | 3.33s |
| **합계** | | **71** | **71** | **0** | **18.36s** |

---

## 5. 구현 변경 사항

### 5-1. `app/kis/rest_rate_limiter.py` — KIS REST 공용 유량 제한기 추가

- 프로세스 공용 rate limiter 도입
- 이벤트 루프별 상태 분리로 테스트 간 충돌 방지
- `KISClient`, `KISService`가 동일한 유량 제한 진입점을 사용하도록 변경

### 5-2. `app/config.py` — KIS REST 유량 제한 설정 정리

- `kis_max_requests_per_second`를 선택값으로 변경
- `resolved_kis_rest_max_requests_per_second` 추가
- KIS base URL 기준 기본 제한값 추론
  - 모의투자 URL: **2 rps**
  - 실전 URL: **20 rps**
  - 알 수 없는 URL: **2 rps**

### 5-3. `app/services/stock_service.py` — 주식 개요 조회 안정화

- 개요 조회 캐시 TTL 조정
- 동일 종목 동시 요청 시 inflight task 공유
- 개요 조회 동시성 제한 추가
- KIS 요청 timeout/retry 처리 보강
- 현재가가 0일 때 최근 유효 일봉 데이터로 fallback

### 5-4. `app/routers/stocks.py` — 주식 라우터 정리

- 라우터 내부의 주식 개요 직접 조회 로직을 서비스 레이어로 위임
- DB user 객체가 필요 없는 인증 경로에서 `get_current_subject` 사용
- 날씨 조회 내부 함수에서 불필요한 `current_user` 인자 제거

### 5-5. `app/routers/users.py` — 인증 의존성 분리

- JWT subject만 필요한 엔드포인트를 위해 `get_current_subject` 추가
- `get_current_user`는 subject 해석 후 DB user 조회만 담당하도록 정리