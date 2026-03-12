# 추천 서버 API 스펙

이 문서는 Backend가 호출하는 외부 추천 서버의 최소 계약입니다.

## 1) 엔드포인트

- Method: `POST`
- Path: `/recommend/news`
- Content-Type: `application/json`

기본적으로 Backend는 아래 URL로 호출합니다.

- `RECOMMENDER_BASE_URL + RECOMMENDER_NEWS_PATH`
- 기본 path: `/recommend/news`

## 2) 요청 바디

```json
{
  "user_id": 1,
  "limit": 20,
  "cursor": "opaque-next-cursor"
}
```

- `user_id`: int (`users.id`, 필수)
- `limit`: int (필수, 보통 1~100)
- `cursor`: string (선택, 다음 페이지 요청 시 전달)

## 3) 권장 응답 형식

```json
{
  "request_id": "req-123",
  "items": [
    { "news_id": 101, "path": "A1" },
    { "news_id": 205, "path": "B2" }
  ],
  "next_cursor": "opaque-next-cursor-2"
}
```

응답 최상위:
- `request_id`: string (선택)
- `next_cursor`: string|null (선택)

각 item:
- `news_id`: int (필수)
- `path`: string (권장, 서빙 경로 코드)

## 4) 호환 응답 형식

Backend는 아래 형식도 처리합니다.

1. `{"news_ids": [101, 205, 999]}`
2. `[{"news_id": 101}, {"news_id": 205}]`
3. `[101, 205, 999]`

## 5) 인증(선택)

- `RECOMMENDER_API_KEY`가 설정된 경우 Backend는 아래 헤더를 보냅니다.
- `Authorization: Bearer <RECOMMENDER_API_KEY>`

추천 서버는 이 헤더를 검증하도록 구현할 수 있습니다.

## 6) 에러 처리 규칙

- 성공: `200`
- 실패: `4xx` 또는 `5xx`
- Backend는 실패 시 내부 fallback(mock)로 전환할 수 있습니다.

## 7) 구현 체크리스트

1. `user_id`, `limit`, `cursor`를 받아 추천 결과 생성
2. `news_id` 리스트를 응답
3. 응답 시간은 5초 이내 권장
4. 빈 결과도 허용 (`items: []`)
5. 결과 순서는 추천 우선순위 순으로 반환
6. 다음 페이지가 있으면 `next_cursor` 반환

## 8) 빠른 로컬 테스트

```bash
curl -X POST "http://localhost:9000/recommend/news" \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"limit":5,"cursor":"opaque-next-cursor"}'
```

주의:
- 위 `localhost:9000`은 로컬 테스트 예시입니다.
- `.env.example`의 기본 `RECOMMENDER_BASE_URL`은 `http://recommend-api:8000`이므로, `localhost:9000` 대신 현재 로컬에서 실제로 열린 호스트/포트를 사용해야 합니다.
