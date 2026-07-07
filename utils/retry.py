"""
Утилиты повторных попыток с экспоненциальной задержкой.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP-коды, при которых имеет смысл повторить запрос
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    backoff_sec: float = 2.0,
    label: str = "request",
) -> T:
    """
    Выполняет асинхронную функцию с ретраями.
    Экспоненциальный backoff: backoff * 2^attempt.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except aiohttp.ClientResponseError as exc:
            last_error = exc
            if exc.status not in RETRYABLE_STATUS or attempt >= max_retries:
                raise
            wait = backoff_sec * (2**attempt)
            logger.warning(
                "%s: HTTP %s, retry %d/%d через %.1fs",
                label,
                exc.status,
                attempt + 1,
                max_retries,
                wait,
            )
            await asyncio.sleep(wait)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt >= max_retries:
                raise
            wait = backoff_sec * (2**attempt)
            logger.warning(
                "%s: %s, retry %d/%d через %.1fs",
                label,
                type(exc).__name__,
                attempt + 1,
                max_retries,
                wait,
            )
            await asyncio.sleep(wait)

    raise RuntimeError(f"{label} failed after retries") from last_error