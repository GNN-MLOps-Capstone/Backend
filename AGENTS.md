# AGENTS.md

이 문서는 `/home/dobi/Backend` 저장소에서 작업하는 에이전트(사람/AI) 공통 운영 가이드입니다.

## 1) 프로젝트 요약
- 목적: Flutter 앱이 사용하는 뉴스/주식 백엔드 API 제공
- 프레임워크: FastAPI (비동기)
- DB 접근: SQLAlchemy AsyncSession
- 주요 외부 연동: KIS Open API(시세/차트, WebSocket), Google Gemini(뉴스 요약), 추천 서버(옵션)

## 2) 주요 경로
- `app/main.py`: FastAPI 엔트리포인트, 라우터 등록, lifespan 초기화
- `app/config.py`: 환경변수 기반 설정
- `app/database.py`: async engine/session, DB 의존성
- `app/models.py`: SQLAlchemy 모델
- `app/schemas.py`: 요청/응답 스키마(Pydantic)
- `app/routers/`: 도메인별 API (`news`, `stocks`, `users`, `notifications`, `watchlist`, `interactions`)
- `app/kis/`: KIS 클라이언트/토큰/변환기/WS

## 3) 로컬 실행
1. 의존성 설치
```bash
pip install -r requirements.txt
```
2. 환경변수 파일 준비
```bash
cp .env.example .env
```
3. 서버 실행
```bash
uvicorn app.main:app --reload --port 8000
```
4. 확인
- `GET /health`
- Swagger: `http://localhost:8000/docs`

## 4) Docker 실행
```bash
docker-compose up -d --build
docker-compose logs -f news-api
docker-compose down
```

## 5) 환경변수 규칙
- 필수: `DATABASE_URL`, `SECRET_KEY`, `ALGORITHM`
- KIS: `KIS_BASE_URL`, `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_WS_BASE_URL`, `KIS_WS_PATH`
- 추천: `RECOMMENDER_BASE_URL`, `RECOMMENDER_API_KEY`, `RECOMMENDER_MOCK_MODE`
- Gemini: `GEMINI_API`

주의:
- `app/config.py`에서 `secret_key`, `algorithm`은 `Field(..., env=...)`로 필수 처리됨.
- 환경변수 누락 시 앱 시작이 실패할 수 있음.

## 6) 코드 작업 원칙
- 기존 구조 유지: 라우터는 `app/routers`, 스키마는 `app/schemas.py`, 모델은 `app/models.py`.
- 신규 API 추가 시:
1. 라우터 핸들러 작성
2. Pydantic request/response 스키마 추가
3. `main.py` 라우터 등록 확인
4. `README.md` 엔드포인트 문서 갱신
- DB 관련 작업: 비동기 세션(`AsyncSession`)과 `Depends(get_db)` 패턴 유지, 기존 운영 DB 스키마(테이블/enum 이름) 임의 변경 금지
- 모델 주의사항: `ProcessStatus`/`process_status_enum` 등 기존 매핑을 깨지 않도록 유지
- 예외 처리: 외부 API 오류는 가능한 한 명시적 HTTP 에러로 변환, KIS 연동은 `KISError` 경로를 일관되게 사용

## 7) 스타일 가이드
- Python 3.11+ 호환 코드 작성
- 타입 힌트 적극 사용
- 비동기 함수 내부에서 블로킹 작업 지양
- 불필요한 대규모 리팩터링 금지 (요청 범위 내 수정)
- 주석/문서 문자열은 현재 코드베이스 톤(한국어 설명 중심)과 일관되게 작성

## 8) 검증 체크리스트
- 최소 확인:
```bash
python -m compileall app
```
- 가능하면 직접 확인:
1. 서버 기동 성공 여부
2. 변경된 엔드포인트의 정상/에러 케이스
3. `/health` 정상 응답

테스트 자동화 부재:
- 현재 저장소에 표준 테스트 스위트(pytest)가 기본 구성되어 있지 않음.
- 변경 시 수동 API 검증 결과를 PR/작업 보고에 명시할 것.

## 9) 금지/주의 사항
- 민감정보(API 키, 토큰, 실DB 접속정보) 커밋 금지
- 요청되지 않은 인프라 변경(Dockerfile/Compose 대규모 수정) 금지
- 파괴적 명령(`git reset --hard`, 무분별한 삭제) 사용 금지
- 기존 동작을 바꾸는 수정은 반드시 영향 범위/롤백 포인트를 함께 기록

## 10) 브랜치 네이밍
- 기본 형식: `<type>/<short-description>`
- 권장 type: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `hotfix`
- 예시:
- `feat/news-recommendation-fallback`
- `fix/stocks-series-date-validation`
- `docs/update-agents-guideline`

규칙:
- 소문자와 하이픈(`-`) 사용
- 한 브랜치에는 하나의 목적만 포함
- 직접 `main`에 커밋하지 않고 기능 브랜치에서 작업

## 11) 커밋 컨벤션
- 형식: `type(scope): summary`
- 권장 type: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`
- scope는 선택이지만 가능하면 명시 (`news`, `stocks`, `users`, `db`, `kis` 등)
- summary는 명령형 현재 시제로 50자 내외 유지

예시:
- `feat(news): add recommendation fallback when API fails`
- `fix(stocks): validate from_date before KIS request`
- `docs(readme): clarify docker local run steps`

추가 규칙:
- 커밋은 작고 의미 단위로 분리
- 리팩터링과 기능 변경을 한 커밋에 혼합하지 않기
- 설정값/환경변수 변경 시 본문에 영향 범위와 마이그레이션 방법 명시

## 12) PR 규칙
- PR 제목은 커밋 컨벤션과 유사하게 작성:
- 예: `fix(news): handle empty recommendation response`

본문 템플릿(권장):
1. 배경/문제
2. 변경 내용
3. 검증 방법
4. 영향 범위
5. 롤백 방법(필요 시)

체크리스트:
- `python -m compileall app` 통과
- 변경된 API 엔드포인트 수동 검증 완료
- `README.md`/문서 업데이트 필요 여부 확인
- 환경변수 추가/변경 시 `.env.example` 반영
- 민감정보 하드코딩/로그 노출 여부 점검

리뷰 규칙:
- 최소 1명 승인 후 병합
- 리뷰 코멘트 unresolved 상태로 병합 금지
- 동작 변경이 큰 경우(인증/결제/외부 API 경로)는 재현 절차를 PR에 반드시 첨부
