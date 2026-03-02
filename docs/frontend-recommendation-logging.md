# 프론트 전달용: 추천/로깅 연동 가이드

이 문서는 Flutter 프론트에서 추천 화면 로깅을 백엔드와 맞추기 위한 구현 가이드입니다.

## 1. 목표 흐름

1. 유저 추천 탭 진입
2. 추천 API 호출
3. 추천 목록 렌더링(노출 로그)
4. 스크롤/무한스크롤 탐색 로그
5. 뉴스 클릭 + 체류시간 로그
6. 화면 이탈까지 로그

백엔드는 이벤트를 받아 다음 2층으로 저장합니다.

1. 원본 이벤트 로그
- `interaction_events` (append-only)

2. 추천 학습용 피드백 집계
- `recommendation_feedback` (impression/click/dwell 자동 upsert)

## 2. 사용 API

1. 추천 목록 조회
- `GET /api/news/recommendations`

2. 이벤트 배치 수집
- `POST /api/interactions/events`

3. 타임아웃 세션 종료(서버/배치 작업에서 호출)
- `POST /api/interactions/finalize-timeouts`

## 3. 세션/ID 규칙

1. `app_session_id`
- 앱 실행 단위로 1개 생성

2. `screen_session_id`
- 추천 탭 진입 시 생성, 추천 탭 이탈 시 종료

3. `content_session_id`
- 뉴스 상세 진입 시 생성, 상세 이탈 시 종료

4. `request_id`
- 추천 요청 단위 ID
- 첫 페이지 호출 시 생성(또는 서버 자동 생성값 사용)
- 같은 무한스크롤 체인(page 1,2,3...)에서는 동일 `request_id` 유지 권장

5. `event_id`
- 이벤트 1건당 고유 UUID
- 네트워크 재전송 시 같은 `event_id` 재사용 가능(중복 저장 방지)

## 4. 추천 API 호출 규칙

`GET /api/news/recommendations` 쿼리:

- `user_id` (필수)
- `limit` (기본 20)
- `page` (기본 1, 무한스크롤 페이지)
- `request_id` (선택, 미전달 시 서버 생성)
- `screen_session_id` (권장, 로깅 연계)
- `app_session_id` (선택)
- `log_served` (기본 true, 추천 목록 DB 자동 로깅)

응답 주요 필드:

- `request_id`: 이후 이벤트 전송 시 그대로 사용
- `page`: 응답 페이지
- `served_count`: 실제 반환 개수
- `logged`: 추천 목록 자동 로깅 성공 여부

## 5. 이벤트 타입 정의

### 5.1 추천 탭 세션

1. `screen_view`
- 추천 탭 진입 시 1회

2. `screen_heartbeat`
- 추천 탭 체류 중 5~15초 간격

3. `screen_leave`
- 추천 탭 이탈 시 1회

### 5.2 추천 요청/응답

1. `recommendation_request`
- 추천 API 호출 직전
- 필수: `request_id`, `screen_session_id`

2. `recommendation_response`
- 추천 API 성공 응답 직후
- 필수: `request_id`, `screen_session_id`
- 권장: `page`

### 5.3 추천 목록 노출/스크롤

1. `recommendation_impression`
- 아이템이 실제 화면에 노출될 때
- 필수: `request_id`, `screen_session_id`, `news_id`, `position`
- 권장: `page`

2. `scroll_depth`
- 스크롤 깊이 변화 시(예: 25/50/75/100% 구간 진입)
- 필수: `screen_session_id`, `scroll_depth`
- 권장: `request_id`, `page`

### 5.4 뉴스 상세 체류

1. `content_open`
- 상세 진입 시
- 필수: `content_session_id`, `news_id`

2. `content_heartbeat`
- 상세 체류 중 5~15초 간격

3. `content_leave`
- 상세 이탈 시

## 6. 이벤트 공통 필드

권장 공통 필드:

- `event_id`
- `user_id`
- `event_type`
- `event_ts_client` (UTC ISO8601)
- `app_session_id`
- `screen_session_id`
- `request_id`

선택 필드:

- `content_session_id`
- `news_id`
- `position`
- `page`
- `scroll_depth`
- `device_id`

## 7. 구현 순서(프론트 체크리스트)

1. 추천 탭 진입 시 `screen_session_id` 생성 + `screen_view` enqueue
2. `request_id` 생성 후 `recommendation_request` enqueue
3. `GET /api/news/recommendations` 호출
4. 응답 수신 후 `recommendation_response` enqueue
5. 목록 렌더링하면서 최초 노출 아이템 `recommendation_impression` enqueue
6. 스크롤 시 구간별 `scroll_depth` enqueue
7. 하단 도달 시 `page += 1`로 추가 호출(같은 `request_id` 유지)
8. 아이템 클릭 시 `content_session_id` 생성 + `content_open` enqueue
9. 상세 체류 heartbeat 전송, 이탈 시 `content_leave` enqueue
10. 추천 탭 종료 시 `screen_leave` enqueue
11. 배치 전송: `POST /api/interactions/events`

## 8. 전송 전략

1. 큐 기반 배치 권장
- 3~20건 단위 또는 2~5초 주기 flush

2. 앱 생명주기 flush
- background 진입/종료 시 즉시 flush

3. 재시도 정책
- 실패 시 exponential backoff
- 중복 전송 허용(서버가 `event_id`로 중복 제거)

## 9. 예시 payload

```json
{
  "events": [
    {
      "event_id": "evt-001",
      "user_id": "user-1",
      "event_type": "screen_view",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-r1",
      "event_ts_client": "2026-03-02T10:00:00Z"
    },
    {
      "event_id": "evt-002",
      "user_id": "user-1",
      "event_type": "recommendation_request",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-r1",
      "page": 1
    },
    {
      "event_id": "evt-003",
      "user_id": "user-1",
      "event_type": "recommendation_response",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-r1",
      "page": 1
    },
    {
      "event_id": "evt-004",
      "user_id": "user-1",
      "event_type": "recommendation_impression",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-r1",
      "news_id": 101,
      "position": 1,
      "page": 1
    },
    {
      "event_id": "evt-005",
      "user_id": "user-1",
      "event_type": "scroll_depth",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-r1",
      "page": 1,
      "scroll_depth": 62.5
    },
    {
      "event_id": "evt-006",
      "user_id": "user-1",
      "event_type": "content_open",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "content_session_id": "content-c1",
      "request_id": "req-r1",
      "news_id": 101,
      "position": 1
    },
    {
      "event_id": "evt-007",
      "user_id": "user-1",
      "event_type": "content_leave",
      "app_session_id": "app-s1",
      "content_session_id": "content-c1"
    },
    {
      "event_id": "evt-008",
      "user_id": "user-1",
      "event_type": "screen_leave",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1"
    }
  ]
}
```

## 10. 주의사항

1. `content_open`에 `news_id`가 없으면 400 에러입니다.
2. `recommendation_request/response`는 `request_id`, `screen_session_id`가 필요합니다.
3. `recommendation_impression`은 `request_id`, `screen_session_id`, `news_id`, `position`이 필요합니다.
4. `scroll_depth`는 `screen_session_id`, `scroll_depth`가 필요합니다.
5. `event_ts_client`는 가능하면 UTC로 전송하세요.
6. `POST /api/interactions/events` 응답의 `feedback_updated`는 추천 피드백 반영 건수입니다.
