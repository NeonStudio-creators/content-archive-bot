"""
«Тихий» режим — контроль частоты запросов.
"""

from __future__ import annotations

import asyncio
import time


class QuietRateLimiter:
    """
    Минимизирует нагрузку на платформу:
    гарантирует паузу между последовательными запросами.
    """

    def __init__(self, delay_sec: float = 1.5) -> None:
        self._delay = delay_sec
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Ждёт, если с прошлого запроса прошло меньше delay_sec."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if elapsed < self._delay:
                await asyncio.sleep(self._delay - elapsed)
            self._last_request_at = time.monotonic()