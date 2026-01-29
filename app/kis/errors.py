"""
KIS API 오류 타입
"""

from __future__ import annotations


class KISError(Exception):
    """KIS 통신용 기본 오류."""

    def __init__(self, message: str, status_code: int = 500, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
