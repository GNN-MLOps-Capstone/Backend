"""
==============================================================================
라우터 패키지 (routers/)
==============================================================================

이 폴더는 API 엔드포인트들을 담고 있습니다.

라우터(Router)란?
    관련된 API 엔드포인트들을 그룹으로 묶은 것입니다.
    
    예시:
    ├── news.py      -> 뉴스 관련 API (/api/news/...)
    ├── users.py     -> 사용자 관련 API (/api/users/...)  [예시]
    └── stocks.py    -> 주식 관련 API (/api/stocks/...)   [예시]

라우터를 분리하는 이유:
    1. 코드 정리: 관련 기능끼리 모아두면 찾기 쉬움
    2. 유지보수: 한 파일이 너무 길어지는 것 방지
    3. 협업: 팀원별로 다른 라우터 담당 가능

현재 구현된 라우터:
    - news.py: 뉴스 API
        - GET /api/news         -> 뉴스 목록 (페이지네이션)
        - GET /api/news/simple  -> 뉴스 목록 (앱용, 간단)
        - GET /api/news/{id}    -> 뉴스 상세
        - GET /api/news/stats/summary -> 뉴스 통계
    - stocks.py: 주식 API
        - GET /api/stocks/{code}/overview
        - GET /api/stocks/{code}/series?range=1d|1w|1m
        - WS  /api/stocks/ws/current?code={code}

==============================================================================
"""
