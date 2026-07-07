"""
«Тихий» режим — контроль частоты запросов.
"""

from __future__ import annotations

import asyncio
import time


class QuietRateLimiter:
    """
    Контроль частоты + параллельные потоки.
    До max_concurrent запросов одновременно, с паузой delay_sec на слот.
    """

    def __init__(self, delay_sec: float = 1.5, max_concurrent: int = 4) -> None:
        self._delay = delay_sec
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def wait(self) -> None:
        async with self._semaphore:
            await asyncio.sleep(self._delay)