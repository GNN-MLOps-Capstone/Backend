# 추천 서버 API 간단 스펙

이 문서는 `Backend` 서버가 호출하는 추천 API의 최소 계약입니다.

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
  "user_id": "test-user",
  "limit": 20
}
```

- `user_id`: string (필수)
- `limit`: int (필수, 보통 1~100)

## 3) 권장 응답 형식 (추천)

```json
{
  "items": [
    { "news_id": 101, "score": 0.93, "reason": "최근 관심 종목 연관" },
    { "news_id": 205, "score": 0.88, "reason": "유사 사용자 클릭 패턴" }
  ]
}
```

각 item:
- `news_id`: int (필수)
- `score`: number (선택)
- `reason`: string (선택)

## 4) 허용되는 다른 응답 형식

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

1. `user_id`, `limit`를 받아 추천 결과 생성
2. `news_id` 리스트를 응답
3. 응답 시간은 5초 이내 권장
4. 빈 결과도 허용 (`items: []`)
5. 결과 순서는 추천 우선순위 순으로 반환

## 8) 빠른 로컬 테스트

```bash
curl -X POST "http://localhost:9000/recommend/news" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test-user","limit":5}'
```
