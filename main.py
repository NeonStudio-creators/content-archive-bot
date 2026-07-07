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

from aiogram import Dispatcher

from bot.app import create_bot, on_shutdown
from config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("content-explorer")


async def main() -> None:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    bot, dp, orchestrator = create_bot(settings)

    dp.shutdown.register(on_shutdown)

    logger.info("ContentExplorer запущен. Ожидаю сообщения…")
    try:
        await dp.start_polling(bot, orchestrator=orchestrator)
    finally:
        await orchestrator.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())