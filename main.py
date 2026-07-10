"""
ContentExplorer — Telegram-бот для глубокой архивации цифрового контента.

Быстрый старт:
  1. cp .env.example .env   (заполнить токены)
  2. pip install -r requirements.txt
  3. python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiohttp import web
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError

from api.server import start_api_server
from bot.app import create_bot, register_shutdown, register_startup
from config import Settings, log_config_status, secrets_hint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("content-explorer")


async def main() -> None:
    log_config_status()

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        logger.error(
            "Задайте TELEGRAM_BOT_TOKEN и SESSION_TOKEN в %s",
            secrets_hint(),
        )
        sys.exit(1)

    if settings.run_mode == "api":
        logger.error("RUN_MODE=api — запускайте: python run_api.py")
        sys.exit(1)

    bot, dp, orchestrator = create_bot(settings)
    register_startup(dp, orchestrator)
    register_shutdown(dp, orchestrator)

    api_runner: web.AppRunner | None = None
    if settings.api_enabled and settings.run_mode == "both":
        api_runner = await start_api_server(
            orchestrator,
            settings,
            host=settings.api_host,
            port=settings.api_port,
        )

    logger.info("ContentExplorer запущен. Ожидаю сообщения…")
    try:
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error(
            "Неверный TELEGRAM_BOT_TOKEN — проверьте токен от @BotFather в Variables"
        )
        sys.exit(1)
    except TelegramAPIError as exc:
        logger.error("Ошибка Telegram API: %s", exc)
        sys.exit(1)
    except Exception:
        logger.exception("Критическая ошибка при polling")
        sys.exit(1)
    finally:
        if api_runner is not None:
            await api_runner.cleanup()
        await orchestrator.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())