"""
KIS REST API 전역 유량 제한기
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
import weakref

from app.config import Settings


@dataclass
class _LoopState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    request_timestamps: deque[float] = field(default_factory=deque)


class KISRestRateLimiter:
    """
    프로세스 내에서 KIS REST 호출을 공용으로 제한합니다.

    런타임은 단일 이벤트 루프를 사용하므로 실제 서버에서는 전역 limiter처럼 동작하고,
    테스트에서는 이벤트 루프별 상태를 분리해 충돌을 피합니다.
    """

    def __init__(self) -> None:
        self._states_by_loop: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            _LoopState,
        ] = weakref.WeakKeyDictionary()

    def _get_loop_state(self) -> _LoopState:
        loop = asyncio.get_running_loop()
        state = self._states_by_loop.get(loop)
        if state is None:
            state = _LoopState()
            self._states_by_loop[loop] = state
        return state

    async def acquire(self, settings: Settings) -> None:
        max_rps = int(settings.resolved_kis_rest_max_requests_per_second or 0)
        if max_rps <= 0:
            return

        window_seconds = 1.0
        state = self._get_loop_state()

        while True:
            async with state.lock:
                now = time.monotonic()
                cutoff = now - window_seconds
                while (
                    state.request_timestamps
                    and state.request_timestamps[0] <= cutoff
                ):
                    state.request_timestamps.popleft()

                if len(state.request_timestamps) < max_rps:
                    state.request_timestamps.append(now)
                    return

                oldest = state.request_timestamps[0]
                sleep_seconds = max((oldest + window_seconds) - now, 0.001)
            await asyncio.sleep(sleep_seconds)


_shared_kis_rest_rate_limiter = KISRestRateLimiter()


async def acquire_kis_rest_rate_limit_slot(settings: Settings) -> None:
    await _shared_kis_rest_rate_limiter.acquire(settings)
