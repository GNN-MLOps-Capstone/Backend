# Interaction Event Field Guide

상호작용 로깅(`POST /api/interactions/events`)의 이벤트 타입별 필수 필드와 저장 규칙을 정리합니다.

## 1. 이벤트 타입별 필수 필드

| event_type | 필수 필드 | 비고 |
| --- | --- | --- |
| `screen_view` | `event_id`, `user_id`, `screen_session_id` | 추천 화면 세션 시작 |
| `screen_heartbeat` | `event_id`, `user_id`, `screen_session_id` | 화면 체류 갱신 |
| `screen_leave` | `event_id`, `user_id`, `screen_session_id` | 화면 세션 종료 |
| `content_open` | `event_id`, `user_id`, `content_session_id`, `news_id`, `request_id` | 기사 상세 진입(클릭) |
| `content_heartbeat` | `event_id`, `user_id`, `content_session_id` | 기사 체류 갱신 |
| `content_leave` | `event_id`, `user_id`, `content_session_id` | 기사 세션 종료 |
| `recommendation_request` | `event_id`, `user_id`, `request_id`, `screen_session_id` | 추천 요청 추적 |
| `recommendation_response` | `event_id`, `user_id`, `request_id`, `screen_session_id` | 추천 응답 추적 |
| `recommendation_impression` | `event_id`, `user_id`, `request_id`, `screen_session_id`, `news_id`, `position` | 노출 집계 |
| `scroll_depth` | `event_id`, `user_id`, `screen_session_id`, `scroll_depth` | 스크롤 깊이 기록 |

기본 공통 필드:
- `event_id`: 멱등 처리용 고유 키(중복 이벤트 차단)
- `user_id`: 사용자 식별자 (`users.id`와 FK로 연결되는 정수값)
- `event_ts_client`: 클라이언트 이벤트 시각(없으면 서버 시각 사용)
- `event_type`: DB enum(`interaction_event_type_enum`) 값만 허용

## 2. 저장 규칙

- 모든 이벤트는 `interaction_events`에 append-only로 저장됩니다.
- 서버는 이벤트 적재 시점에 세션/피드백 집계를 수행하지 않습니다.
- 추천 성과 집계(노출/클릭/체류/완독)는 `interaction_events`와 `recommendation_serves`를 기준으로 Airflow 등 배치에서 처리합니다.
- `GET /api/news/recommendations`의 `log_served=true`일 때는 `recommendation_serves`에 추천 응답 스냅샷이 저장됩니다.

## 3. recommendation_serves 저장 형식

`recommendation_serves`는 요청 단위 스냅샷 테이블이며, 응답 아이템 목록은 `served_items` JSONB 컬럼에 저장합니다.

예시:

```json
[
  {"news_id": 101, "position": 1, "path": "A1"},
  {"news_id": 205, "position": 2, "path": "B2"}
]
```

규칙:
- 각 item은 `news_id`, `position`, `path`를 가집니다.
- `served_count`는 `served_items` 길이와 일치해야 합니다.
- `source`는 요청 단위 대표 출처, `path`는 아이템 단위 서빙 경로입니다.

## 4. 중복/경합 처리 정책

- 1차 방어: `event_id` 존재 여부 조회 후 중복이면 `duplicated` 카운트
- 2차 방어: 동시 요청 경합으로 인한 unique 충돌은 `IntegrityError`로 중복 처리
- 실패 시 전체 배치가 아닌 해당 이벤트만 건너뛰고 다음 이벤트 처리

## 5. 추천 응답 출처(source) 값

`GET /api/news/recommendations` 응답의 `source`는 아래 의미를 갖습니다.

- `recommender`: 외부 추천 서버 정상 응답
- `mock`: `RECOMMENDER_MOCK_MODE=true` 강제 목업
- `mock_fallback`: 외부 추천 실패/빈 응답 시 자동 대체
