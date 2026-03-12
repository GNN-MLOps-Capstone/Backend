# Agent Recommendation Session Runbook

에이전트나 운영자가 API 호출만으로 추천 화면 세션을 재현하며 로그를 남길 때 따라야 할 절차와 제약을 정리합니다.

목표:
- `interaction_events`에 화면 진입, 추천 요청/응답, 뉴스 진입/이탈, 화면 이탈 로그를 남깁니다.
- `recommendation_serves`는 `GET /api/news/recommendations` 호출을 통해 자연스럽게 적재합니다.
- 실제 앱 세션처럼 각 이벤트를 하나의 `screen_session_id`, `request_id`, `content_session_id` 흐름으로 연결합니다.

## 1. 전제 조건

- `user_id`는 반드시 실제 `users.id` 값이어야 합니다.
- 추천 조회에 사용할 사용자에 대해 DB FK가 유효해야 합니다.
- `GET /api/news/recommendations` 호출 시 `log_served=true`여야 `recommendation_serves`가 저장됩니다.
- `screen_session_id`, `request_id`, `content_session_id`, `event_id`는 모두 호출자가 직접 관리해야 합니다.
- `event_id`는 이벤트마다 유일해야 하며 재사용하면 중복으로 무시됩니다.

## 2. 핵심 원칙

- `recommendation_serves`는 직접 insert 하지 않습니다.
- 반드시 추천 API 응답에서 받은 `request_id`, `news_id`를 후속 이벤트에 사용합니다.
- `content_open`과 `content_leave`는 같은 `content_session_id`를 사용합니다.
- `content_leave`에도 가능하면 `content_open`과 같은 `news_id`를 함께 넣습니다.
- `screen_view`와 `screen_leave`는 같은 `screen_session_id`를 사용합니다.
- 같은 `request_id`에 대해 임의의 다른 `news_id`를 섞어 넣지 않습니다.
- 추천 응답에 없는 `news_id`로 `content_open`을 보내지 않습니다.

## 3. 권장 실행 순서

### 3-1. 추천 화면 진입

먼저 `screen_view`를 적재합니다.

필수 값:
- `event_id`
- `user_id`
- `event_type=screen_view`
- `screen_session_id`

예시:

```json
{
  "events": [
    {
      "event_id": "evt-screen-view-001",
      "user_id": 1,
      "event_type": "screen_view",
      "screen_session_id": "screen-session-001"
    }
  ]
}
```

### 3-2. 추천 요청/응답 스냅샷 생성

다음으로 `GET /api/news/recommendations`를 호출합니다.

권장 쿼리 파라미터:
- `user_id`
- `limit` 선택 가능하지만 서버는 항상 20개를 반환
- `page`
- `screen_session_id`
- `app_session_id` 선택
- `log_served=true`

이 호출 결과로 아래 값을 확보합니다.

- `request_id`
- `items[].news_id`
- `items[].path`
- `page`
- `served_count`

주의:
- 이 시점에 `recommendation_serves`가 적재됩니다.
- 같은 `request_id + page` 조합은 유니크 제약으로 중복 저장되지 않습니다.
- 테스트 목적이면 `request_id`를 매번 새로 생성하거나 서버 생성값을 그대로 사용합니다.

### 3-3. 추천 요청/응답 이벤트 적재

추천 API 조회 자체만으로 `interaction_events`의 추천 요청/응답 이벤트는 자동 저장되지 않습니다.
필요하면 아래 두 이벤트를 별도로 보냅니다.

- `recommendation_request`
- `recommendation_response`

필수 값:
- `event_id`
- `user_id`
- `event_type`
- `request_id`
- `screen_session_id`

권장:
- `page`도 같이 넣어두면 후처리 추적이 쉬워집니다.

### 3-4. 노출 판단

현재 운영 기준으로 추천 목록 노출 여부는 `recommendation_serves`를 기준으로 판단합니다.

주의:
- `recommendation_impression`은 서버가 아직 수용하지만 현재 기본 시나리오에서는 전송하지 않습니다.
- 별도 실험이나 정밀 노출 분석이 필요할 때만 추가 사용을 검토합니다.

### 3-5. 뉴스 상세 진입

사용자가 특정 추천 뉴스를 눌렀다고 가정하고 `content_open`을 적재합니다.

필수 값:
- `event_id`
- `user_id`
- `event_type=content_open`
- `content_session_id`
- `request_id`
- `news_id`

권장:
- 추천 응답에 포함된 `news_id` 중 하나를 선택합니다.
- `content_session_id`는 뉴스 상세 체류 전체 구간 동안 유지합니다.

### 3-6. 뉴스 상세 이탈

같은 `content_session_id`로 `content_leave`를 적재합니다.

권장:
- 중간 체류가 필요하면 `content_heartbeat`를 1회 이상 넣을 수 있습니다.
- `news_id`도 함께 넣어 `content_open`과 같은 기사로 연결되게 합니다.
- `content_open` 없이 `content_leave`만 단독으로 보내지 않습니다.

### 3-7. 추천 화면 이탈

마지막으로 같은 `screen_session_id`로 `screen_leave`를 적재합니다.

권장:
- 화면 체류를 표현하려면 중간에 `screen_heartbeat`를 넣을 수 있습니다.
- 화면 진입 없이 `screen_leave`만 보내지 않습니다.

## 4. 최소 유효 시나리오

아래 순서를 지키면 가장 단순한 정상 세션을 재현할 수 있습니다.

1. `screen_view`
2. `GET /api/news/recommendations?log_served=true`
3. `recommendation_request`
4. `recommendation_response`
5. `content_open`
6. `content_leave`
7. `screen_leave`

## 5. 금지 사항

- 존재하지 않는 `user_id` 사용 금지
- 추천 응답 이전에 임의 `request_id`로 `content_open` 전송 금지
- 추천 응답에 없는 `news_id` 사용 금지
- 동일 `event_id` 재사용 금지
- 같은 `request_id + page`로 `recommendation_serves` 중복 적재 시도 금지
- `log_served=false`로 호출한 뒤 `recommendation_serves` 적재를 기대하는 행위 금지

## 6. 운영 팁

- 테스트 자동화나 수동 재현 모두 `event_id` 생성 규칙을 미리 정해 두는 편이 좋습니다.
- 장애 조사 목적이면 `screen_session_id`, `request_id`, `content_session_id`를 로그 메시지에도 함께 남기는 편이 좋습니다.
- `source`가 `mock` 또는 `mock_fallback`일 수 있으므로, 외부 추천 결과를 검증하는 세션인지 목업 세션인지 구분해서 기록해야 합니다.
- 추천 결과 개수가 0이면 `content_open` 시나리오는 진행하지 않는 것이 자연스럽습니다.

## 7. 예시 세션 식별자 설계

- `screen_session_id`: `screen-{timestamp}-{user_id}`
- `content_session_id`: `content-{timestamp}-{news_id}`
- `event_id`: `evt-{step}-{timestamp}-{seq}`

핵심은 포맷 자체보다 한 세션 내 연결성과 전체 유일성입니다.
