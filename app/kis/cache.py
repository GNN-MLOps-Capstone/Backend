"""
짧은 수명의 KIS 응답을 위한 간단한 메모리 TTL 캐시
"""

from __future__ import annotations

from typing import Any, Optional
import asyncio
import time


class TTLCache:
    def __init__(self, cleanup_interval_seconds: float = 30.0, max_cleanup_per_run: int = 200) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._max_cleanup_per_run = max_cleanup_per_run
        self._next_cleanup_at = 0.0

    async def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        async with self._lock:
            self._cleanup_expired_locked(now)
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        expires_at = time.monotonic() + ttl_seconds
        async with self._lock:
            self._store[key] = (expires_at, value)
            self._cleanup_expired_locked(time.monotonic())

    def _cleanup_expired_locked(self, now: float) -> None:
        if now < self._next_cleanup_at:
            return

        removed = 0
        for key, (expires_at, _) in list(self._store.items()):
            if expires_at <= now:
                self._store.pop(key, None)
                removed += 1
                if removed >= self._max_cleanup_per_run:
                    break

        self._next_cleanup_at = now + self._cleanup_interval_seconds
