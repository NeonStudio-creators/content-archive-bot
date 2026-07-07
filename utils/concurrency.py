"""
Пул параллельных запросов с ограничением потоков.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import TypeVar

T = TypeVar("T")


async def run_bounded(
    coros: list[Coroutine[None, None, T]],
    *,
    limit: int,
) -> list[T | BaseException]:
    """Запускает корутины с лимитом параллелизма."""
    sem = asyncio.Semaphore(limit)
    results: list[T | BaseException] = []

    async def _wrap(coro: Coroutine[None, None, T]) -> None:
        async with sem:
            try:
                results.append(await coro)
            except BaseException as exc:
                results.append(exc)

    await asyncio.gather(*[_wrap(c) for c in coros])
    return results


async def first_success(
    factories: list[Callable[[], Awaitable[T | None]]],
) -> T | None:
    """Возвращает первый успешный непустой результат, отменяя остальные."""
    tasks = [asyncio.create_task(fn()) for fn in factories]
    try:
        for finished in asyncio.as_completed(tasks):
            try:
                result = await finished
                if result is not None:
                    return result
            except Exception:
                continue
        return None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)