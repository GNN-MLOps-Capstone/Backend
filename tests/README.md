# Backend 테스트 실행 가이드

`tests/` 아래 테스트를 처음 실행하는 사람을 위한 빠른 안내입니다. 아래 순서대로 따라가면 현재 구성된 테스트를 바로 실행할 수 있습니다.

## 테스트 파일 구성

- `tests/test_models.py`: `User`, `UserSettings`, `Watchlist`, `Stock` 모델의 생성, 기본값, 제약조건을 검증합니다.
- `tests/test_schemas.py`: 로그인/유저 응답/설정/관심종목 관련 Pydantic 스키마 검증을 확인합니다.
- `tests/test_kis.py`: `KISError`, `TTLCache`, `KISClient.request()`의 기본 에러 처리와 재시도 판단을 검증합니다.
- `tests/test_kis_token_manager.py`: KIS 토큰 재사용, 만료 임박 갱신, 재시도, 요청 헤더 구성을 검증합니다.
- `tests/test_kis_transformers.py`: KIS 시세 응답의 개요/분봉/일봉 변환 로직을 검증합니다.
- `tests/test_stock_service.py`: 현재가 조회, 캐시 fallback, 일봉 fallback, 개요 캐시 재사용을 검증합니다.

## 사전 준비

먼저 어떤 셸에서 실행하는지 확인합니다.

- `macOS/Linux`: 아래 bash 예시를 거의 그대로 사용하면 됩니다.
- `WSL`: 아래 bash 예시를 거의 그대로 사용하면 됩니다.
- `Windows Git Bash`: bash 문법은 비슷하지만 보통 `python3` 대신 `python`, `source .venv/bin/activate` 대신 `source .venv/Scripts/activate`를 사용합니다.

### macOS/Linux

1. `Backend` 디렉터리로 이동합니다.

```bash
cd Backend
```

2. 완전 새 환경이라면 가상환경을 만들고 활성화합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. 패키지 설치 도구를 먼저 최신 상태로 맞춥니다.

```bash
python3 -m pip install --upgrade pip setuptools wheel
```

4. 기본 의존성을 설치합니다.

```bash
python3 -m pip install -r requirements.txt
```

5. 테스트 라이브러리를 별도로 설치합니다.

현재 `requirements.txt`에는 앱 실행용 패키지는 포함되어 있지만, 테스트 실행에 필요한 `pytest`, `pytest-asyncio`, `respx`는 기본 설치 목록에 포함되어 있지 않습니다.

```bash
python3 -m pip install pytest pytest-asyncio respx
```

6. 테스트용 환경변수를 준비합니다.

```bash
export DEBUG=false
export SECRET_KEY=test-secret
export ALGORITHM=HS256
export GOOGLE_CLIENT_ID=test-google
```

필수 값은 최소한 `DEBUG`, `SECRET_KEY`, `ALGORITHM`, `GOOGLE_CLIENT_ID` 입니다.

### Windows Git Bash

Git Bash에서도 bash 문법 자체는 비슷합니다. 다만 파이썬 실행기와 가상환경 활성화 경로는 아래처럼 맞추는 편이 안전합니다.

```bash
cd Backend
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install pytest pytest-asyncio respx

export DEBUG=false
export SECRET_KEY=test-secret
export ALGORITHM=HS256
export GOOGLE_CLIENT_ID=test-google
```

### WSL

WSL bash는 `macOS/Linux` 예시를 거의 그대로 사용하면 됩니다.

## `plain pytest`가 실패할 수 있는 이유

현재 설정 로딩은 `DEBUG` 값을 `bool`로 해석합니다. 셸에 `DEBUG=release` 같은 값이 잡혀 있으면 `plain pytest` 실행 시 설정 파싱 단계에서 실패할 수 있습니다.

문제 예시:

- `pytest -q`
- 셸 환경에 `DEBUG=release`가 이미 설정됨
- `app/config.py`의 `debug: bool` 파싱 실패

우회 방법:

- 테스트 실행 시 `DEBUG=false`를 명시해서 덮어씁니다.
- 또는 현재 셸에서 `unset DEBUG` 후 실행합니다.

실무에서는 아래처럼 실행 명령 앞에 환경변수를 같이 붙이는 방식이 가장 안전합니다.

```bash
DEBUG=false SECRET_KEY=test-secret ALGORITHM=HS256 GOOGLE_CLIENT_ID=test-google pytest -q
```

이 방식은 `Windows Git Bash`에서도 그대로 사용할 수 있습니다.

## 실행 명령

- `macOS/Linux`: 아래 명령을 그대로 사용하면 됩니다.
- `WSL`: 아래 명령을 그대로 사용하면 됩니다.
- `Windows Git Bash`: 아래 `pytest` 명령 형식도 그대로 사용 가능합니다. 다만 사전 준비 단계에서는 `python3` 대신 `python`, 가상환경 활성화는 `source .venv/Scripts/activate`를 사용합니다.

전체 테스트 실행:

```bash
DEBUG=false SECRET_KEY=test-secret ALGORITHM=HS256 GOOGLE_CLIENT_ID=test-google \
pytest tests/test_models.py tests/test_schemas.py tests/test_kis.py \
tests/test_kis_token_manager.py tests/test_kis_transformers.py tests/test_stock_service.py -q
```

새로 추가된 테스트만 실행:

```bash
DEBUG=false SECRET_KEY=test-secret ALGORITHM=HS256 GOOGLE_CLIENT_ID=test-google \
pytest tests/test_kis_token_manager.py tests/test_kis_transformers.py tests/test_stock_service.py -q
```

파일 하나만 실행 예시:

```bash
DEBUG=false SECRET_KEY=test-secret ALGORITHM=HS256 GOOGLE_CLIENT_ID=test-google \
pytest tests/test_kis_token_manager.py -q
```

## 기대 결과

현재 기준으로 전체 테스트가 정상 동작하면 아래와 비슷한 결과를 기대할 수 있습니다.

```text
84 passed
```

환경에 따라 Pydantic deprecation warning 이 함께 출력될 수 있지만, 테스트 실패와는 별개입니다.

## 자주 만나는 문제

- `pip command not found`
  - 현재 셸에서 `pip`를 직접 찾지 못하는 상태입니다.
  - 가상환경을 먼저 활성화하고, 설치 명령은 `python3 -m pip ...` 형태로 실행하면 가장 안전합니다.

- `pytest: command not found`
  - 가상환경이 비활성화됐거나, 테스트 라이브러리 설치 단계가 빠진 경우가 많습니다.
  - `source .venv/bin/activate` 후 `python3 -m pip install pytest pytest-asyncio respx`를 다시 실행합니다.
  - Windows Git Bash라면 `source .venv/Scripts/activate`와 `python -m pip install pytest pytest-asyncio respx`를 사용합니다.

- `ValidationError` 또는 설정 로딩 실패
  - `DEBUG=false`, `SECRET_KEY`, `ALGORITHM`, `GOOGLE_CLIENT_ID`가 빠졌는지 확인합니다.

- `DEBUG=release` 때문에 시작 전에 실패
  - `unset DEBUG` 후 다시 실행하거나, 실행 명령 앞에 `DEBUG=false`를 붙입니다.

- `ModuleNotFoundError: No module named ...`
  - 필요한 라이브러리가 아직 설치되지 않았다는 뜻입니다.
  - 먼저 `cd Backend`로 이동했는지 확인한 뒤 `python3 -m pip install -r requirements.txt`와 `python3 -m pip install pytest pytest-asyncio respx`를 다시 실행합니다.
  - Windows Git Bash에서는 같은 순서로 실행하되 `python3 -m pip` 대신 `python -m pip`를 사용합니다.

- 일부 경고가 출력됨
  - 현재 테스트는 통과해도 Pydantic 관련 deprecation warning 이 보일 수 있습니다.
