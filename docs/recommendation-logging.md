# 추천/로깅 연동 가이드

추천 화면 연동 시 필요한 최소 규칙만 정리한 기준 문서입니다.

대상 API:
- `GET /api/news/recommendations`
- `POST /api/interactions/events`

## 1. 핵심 흐름

1. 추천 화면 진입
2. 추천 API 호출
3. 뉴스 클릭/체류/이탈 로깅
4. 추천 화면 이탈

추천 응답 스냅샷은 `GET /api/news/recommendations` 호출 시 `recommendation_serves`에 저장됩니다.
원본 이벤트 로그는 `POST /api/interactions/events`로 `interaction_events`에 저장됩니다.

## 2. 세션/식별자 규칙

- `user_id`: 실제 `users.id`
- `app_session_id`: 앱 실행 단위
- `screen_session_id`: 추천 화면 진입~이탈 동안 동일
- `content_session_id`: 기사 상세 진입~이탈 동안 동일
- `request_id`: 같은 추천 스크롤 체인(page 1, 2, 3...)에서 유지 권장
- `event_id`: 이벤트 1건당 고유값(UUID 권장)

## 3. 추천 API 호출 규칙

주요 쿼리 파라미터:
- `user_id` 필수
- `page` 기본 1
- `request_id` 선택, 없으면 서버 생성
- `screen_session_id` 권장
- `app_session_id` 선택
- `log_served` 기본 `true`

응답에서 확인할 필드:
- `request_id`
- `page`
- `served_count`
- `source`
- `logged`
- `items[].news_id`
- `items[].path`

운영 규칙:
- `log_served=true`여야 `recommendation_serves`가 저장됩니다.
- 추천 목록 노출 여부는 현재 `recommendation_serves`를 기준으로 판단합니다.
- 같은 `request_id + page` 조합은 중복 저장되지 않습니다.

## 4. 이벤트 타입별 필수 필드

| event_type | 필수 필드 |
| --- | --- |
| `screen_view` | `event_id`, `user_id`, `screen_session_id` |
| `screen_heartbeat` | `event_id`, `user_id`, `screen_session_id` |
| `screen_leave` | `event_id`, `user_id`, `screen_session_id` |
| `recommendation_request` | `event_id`, `user_id`, `request_id`, `screen_session_id` |
| `recommendation_response` | `event_id`, `user_id`, `request_id`, `screen_session_id` |
| `scroll_depth` | `event_id`, `user_id`, `screen_session_id`, `scroll_depth` |
| `content_open` | `event_id`, `user_id`, `content_session_id`, `news_id`, `request_id` |
| `content_heartbeat` | `event_id`, `user_id`, `content_session_id` |
| `content_leave` | `event_id`, `user_id`, `content_session_id` |

공통 권장 필드:
- `event_ts_client`
- `app_session_id`
- `page`
- `position`

추가 규칙:
- `content_open`에는 `request_id`와 `news_id`가 모두 반드시 필요합니다.
- `content_leave`에도 같은 `news_id`를 함께 넣는 것을 권장합니다.
- `recommendation_impression`은 현재 기본 운영 흐름에서 사용하지 않습니다.

## 5. 최소 정상 시나리오

1. `screen_view`
2. `GET /api/news/recommendations?log_served=true`
3. `recommendation_request`
4. `recommendation_response`
5. `content_open`
6. `content_leave`
7. `screen_leave`

주의:
- 추천 응답에 없는 `news_id`로 `content_open`을 보내지 않습니다.
- 존재하지 않는 `user_id`를 사용하지 않습니다.
- 동일 `event_id`를 재사용하면 중복으로 무시됩니다.

## 6. 전송 전략

- 이벤트는 배치 전송할 수 있지만, 한 요청에 500개를 초과하면 서버가 `413`으로 거절합니다.
- 권장 배치 크기는 3~20건이며, 큰 큐는 500개 이하로 분할해서 전송합니다.
- 앱 background 전환이나 종료 시에는 남은 큐를 즉시 flush 하는 편이 안전합니다.

## 7. 예시 payload

```json
{
  "events": [
    {
      "event_id": "8f3c1a96-0b2a-4d7a-b9d4-2f2d4f5db3a1",
      "user_id": 1,
      "event_type": "screen_view",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-r1"
    },
    {
      "event_id": "9c7f0c1d-9a1b-4f6d-8c95-1bb132ec4b27",
      "user_id": 1,
      "event_type": "content_open",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "content_session_id": "content-c1",
      "request_id": "req-r1",
      "news_id": 101,
      "position": 1
    },
    {
      "event_id": "f1c2e7ab-2f6e-4b33-a78f-7dfb6f8f41c9",
      "user_id": 1,
      "event_type": "content_leave",
      "app_session_id": "app-s1",
      "content_session_id": "content-c1",
      "news_id": 101
    },
    {
      "event_id": "2b6d62de-c42f-4e3f-bb7a-b6b0d460e3af",
      "user_id": 1,
      "event_type": "screen_leave",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1"
    }
  ]
}
```
