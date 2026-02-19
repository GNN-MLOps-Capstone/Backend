"""
==============================================================================
설정 관리 모듈 (config.py)
==============================================================================

이 파일은 서버의 모든 설정을 관리합니다.
설정값들은 환경변수(.env 파일)에서 가져오거나, 기본값을 사용합니다.

설정 우선순위:
    1. 환경변수 (예: export DATABASE_URL="...")  <- 가장 높은 우선순위
    2. .env 파일에 적힌 값
    3. 코드에 적힌 기본값                        <- 가장 낮은 우선순위

예시:
    - 로컬 개발: 기본값(SQLite) 사용
    - 프로덕션: 환경변수로 PostgreSQL 주소 설정

==============================================================================
"""

from pydantic_settings import BaseSettings  # 설정 관리 라이브러리
from functools import lru_cache  # 캐싱 기능 (설정을 한 번만 읽어옴)


class Settings(BaseSettings):
    """
    애플리케이션 설정 클래스
    
    이 클래스에 정의된 변수들은 자동으로 환경변수에서 값을 가져옵니다.
    환경변수 이름은 대소문자를 구분하지 않습니다.
    
    예: database_url -> DATABASE_URL 환경변수에서 값을 가져옴
    """
    
    # =========================================================================
    # 데이터베이스 설정
    # =========================================================================
    # 
    # DATABASE_URL 형식:
    #   - SQLite:     sqlite+aiosqlite:///./파일명.db
    #   - PostgreSQL: postgresql+asyncpg://사용자:비밀번호@호스트:포트/DB이름
    #   - MySQL:      mysql+aiomysql://사용자:비밀번호@호스트:포트/DB이름
    #
    # 기본값은 SQLite (로컬 파일 DB) - 개발/테스트용으로 편리함
    # 
    database_url: str = "sqlite+aiosqlite:///./news.db"
    
    # =========================================================================
    # 서버 설정
    # =========================================================================
    #
    # host: 서버가 어떤 IP에서 요청을 받을지
    #   - "0.0.0.0" = 모든 IP에서 접근 가능 (Docker에서 필수)
    #   - "127.0.0.1" = 로컬에서만 접근 가능
    #
    # port: 서버가 사용할 포트 번호
    #   - 기본값 8000번 (http://localhost:8000)
    #
    # debug: 디버그 모드 활성화 여부
    #   - True = 상세한 에러 메시지, 코드 변경시 자동 재시작
    #   - False = 프로덕션용 (에러 메시지 최소화)
    #
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    
    # =========================================================================
    # CORS 설정 (Cross-Origin Resource Sharing)
    # =========================================================================
    #
    # CORS는 "다른 주소에서 오는 요청을 허용할지" 결정합니다.
    # 
    # 예: Flutter 앱(localhost:51151)에서 API 서버(localhost:8000)로 요청할 때
    #     서로 다른 포트이므로 CORS 설정이 필요합니다.
    #
    # 쉼표로 구분하여 여러 주소를 허용할 수 있습니다.
    #
    cors_origins: str = "http://localhost:3000,http://localhost:51151"

    # =========================================================================
    # KIS Open API 설정
    # =========================================================================
    #
    # KIS_BASE_URL:
    #   - 실전: https://openapi.koreainvestment.com:9443
    #   - 모의: https://openapivts.koreainvestment.com:29443
    #
    kis_base_url: str = "https://openapi.koreainvestment.com:9443"
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_timeout: float = 10.0
    series_cache_bypass_cooldown_seconds: float = 30.0
    # KIS WS:
    #   - 실전: ws://ops.koreainvestment.com:21000
    #   - 모의: ws://ops.koreainvestment.com:31000
    kis_ws_base_url: str = "ws://ops.koreainvestment.com:21000"
    kis_ws_path: str = "/tryitout"
    
    class Config:
        """
        Pydantic 설정 클래스
        
        env_file: 환경변수를 읽어올 파일 경로
        env_file_encoding: 파일 인코딩 (한글 지원을 위해 utf-8 사용)
        """
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()  # 이 함수의 결과를 캐싱 (매번 파일을 읽지 않고 한 번만 읽음)
def get_settings() -> Settings:
    """
    설정 객체를 가져오는 함수
    
    @lru_cache() 덕분에 처음 호출될 때만 Settings()를 생성하고,
    이후에는 캐시된 값을 반환합니다. (성능 최적화)
    
    사용법:
        settings = get_settings()
        print(settings.database_url)  # DB 주소 출력
    
    Returns:
        Settings: 설정 객체
    """
    return Settings()
