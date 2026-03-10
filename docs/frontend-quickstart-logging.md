# 프론트 초간단 가이드 (복붙용)

목표: 추천 화면에서 로그를 보내고, 체류시간/이탈까지 기록되게 하기

## 1) 먼저 추천 API 호출

`GET /api/news/recommendations` 호출할 때 아래만 넣어주세요.

- `user_id` (`users.id` 정수값)
- `screen_session_id` (추천 화면 들어올 때 만든 값)
- `app_session_id` (앱 실행 시 만든 값)
- `page` (처음 1)
- `request_id` (처음 생성해서 이후 같은 값 계속 사용)

응답 오면 뉴스 목록 렌더링.

## 2) 이벤트는 이 4개만 먼저 보내도 충분

1. 화면 진입: `screen_view`
2. 기사 진입: `content_open`
3. 기사 이탈: `content_leave`
4. 화면 이탈: `screen_leave`

전송 API: `POST /api/interactions/events`

## 3) 필수 규칙 (중요)

1. `event_id`는 이벤트마다 고유값(UUID) 사용
2. `screen_session_id`는 화면 진입~이탈 동안 같은 값
3. `content_session_id`는 기사 진입~이탈 동안 같은 값
4. `content_open`에는 `news_id` 반드시 포함
5. 한 번에 여러 이벤트를 `events` 배열로 배치 전송 가능

## 4) 바로 쓰는 예시 payload

```json
{
  "events": [
    {
      "event_id": "8f3c1a96-0b2a-4d7a-b9d4-2f2d4f5db3a1",
      "user_id": 1,
      "event_type": "screen_view",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "request_id": "req-s1"
    },
    {
      "event_id": "9c7f0c1d-9a1b-4f6d-8c95-1bb132ec4b27",
      "user_id": 1,
      "event_type": "content_open",
      "app_session_id": "app-s1",
      "screen_session_id": "screen-s1",
      "content_session_id": "content-c1",
      "request_id": "req-s1",
      "news_id": 101,
      "position": 1
    },
    {
      "event_id": "f1c2e7ab-2f6e-4b33-a78f-7dfb6f8f41c9",
      "user_id": 1,
      "event_type": "content_leave",
      "app_session_id": "app-s1",
      "content_session_id": "content-c1"
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

## 5) 나중에 여유되면 추가할 이벤트

- `recommendation_impression` (목록 노출)
- `scroll_depth` (스크롤 깊이)
- `recommendation_request`, `recommendation_response`

이건 2차 작업으로 붙여도 됩니다.
