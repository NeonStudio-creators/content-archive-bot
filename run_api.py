"""
Только HTTP API для стороннего сервиса (без Telegram-бота).

  python run_api.py

Railway Web Service:
  RUN_MODE=api
  startCommand = python run_api.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiohttp import web

from api.server import start_api_server
from config import Settings, log_config_status
from core.orchestrator import ArchiveOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("content-explorer-api")


async def main() -> None:
    log_config_status()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    if not settings.api_enabled:
        logger.error("API_ENABLED=false — нечего запускать")
        sys.exit(1)

    orchestrator = ArchiveOrchestrator(settings)
    await orchestrator.token_refresher.startup()
    orchestrator.token_refresher.start_background()

    runner: web.AppRunner | None = None
    try:
        runner = await start_api_server(
            orchestrator,
            settings,
            host=settings.api_host,
            port=settings.api_port,
        )
        logger.info("API-only режим. Остановка: Ctrl+C")
        await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Остановка API…")
    finally:
        await orchestrator.token_refresher.stop_background()
        if runner is not None:
            await runner.cleanup()
        await orchestrator.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass