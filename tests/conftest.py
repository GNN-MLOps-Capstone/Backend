import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_env_bootstrap() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ.setdefault("SECRET_KEY", "test-secret-key")
    os.environ.setdefault("ALGORITHM", "HS256")
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    os.environ.setdefault("DEBUG", "true")
