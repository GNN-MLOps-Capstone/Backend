# Interaction Event Field Guide

PR6에서 추가한 상호작용 로깅(`POST /api/interactions/events`)의 이벤트 타입별 필수 필드와 집계 반영 규칙을 정리합니다.

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
- `user_id`: 사용자 식별자
- `event_ts_client`: 클라이언트 이벤트 시각(없으면 서버 시각 사용)

## 2. 집계 테이블 반영 규칙

- `screen_*` 이벤트: `screen_sessions` upsert
- `content_*` 이벤트: `content_sessions` upsert
- `recommendation_impression`: `recommendation_feedback`의 impression 카운트 증가
- `content_open`: `recommendation_feedback.clicked = true` 및 `clicked_at` 반영
- `content_leave`: `recommendation_feedback.dwell_ms`, `completed_read` 반영

## 3. out-of-order 이벤트 보정

모바일 배치 전송 특성상 이벤트 순서가 바뀔 수 있으므로 다음을 허용합니다.

- `screen_heartbeat`/`screen_leave`가 먼저 와도 `screen_session_id`로 최소 세션 생성 후 반영
- `content_heartbeat`/`content_leave`가 먼저 와도 `news_id`가 있으면 최소 콘텐츠 세션 생성
- `news_id` 없는 콘텐츠 이벤트는 체류 집계가 불가능해 스킵

## 4. 중복/경합 처리 정책

- 1차 방어: `event_id` 존재 여부 조회 후 중복이면 `duplicated` 카운트
- 2차 방어: 동시 요청 경합으로 인한 unique 충돌은 `IntegrityError`로 중복 처리
- 실패 시 전체 배치가 아닌 해당 이벤트만 건너뛰고 다음 이벤트 처리

## 5. 체류시간/완독 기준

- `dwell_ms`는 음수 방지 및 과대 집계 방지를 위해 `0ms ~ 10분`으로 제한
- `completed_read`는 `dwell_ms >= 15,000ms`(15초) 기준

## 6. 추천 응답 출처(source) 값

`GET /api/news/recommendations` 응답의 `source`는 아래 의미를 갖습니다.

- `recommender`: 외부 추천 서버 정상 응답
- `mock`: `RECOMMENDER_MOCK_MODE=true` 강제 목업
- `mock_page`: 페이지네이션 미연결 상태에서 2페이지 이상 요청 시 목업 오프셋 사용
- `mock_fallback`: 외부 추천 실패/빈 응답 시 자동 대체
