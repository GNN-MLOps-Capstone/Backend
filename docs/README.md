# Docs Guide

`docs`는 운영/연동에 직접 필요한 문서만 유지합니다.

## 유지 문서

1. `recommendation-logging.md`
- 추천 API 호출 규칙과 상호작용 이벤트 로깅 기준 문서

2. `recommender-api.md`
- Backend가 호출하는 외부 추천 서버의 최소 계약

## 정리 원칙

- API 자체 설명은 Swagger(`GET /docs`)와 `README.md`를 우선합니다.
- DB 테이블 목록처럼 코드에서 바로 확인 가능한 내용은 별도 문서로 중복 관리하지 않습니다.
- 프론트/운영/에이전트용으로 흩어진 중복 가이드는 하나의 기준 문서로 통합합니다.
