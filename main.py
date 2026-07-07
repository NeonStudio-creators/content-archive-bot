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

from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError

from bot.app import create_bot, register_shutdown
from config import Settings, log_config_status

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
            "Задайте Variables в панели деплоя: TELEGRAM_BOT_TOKEN, SESSION_TOKEN"
        )
        sys.exit(1)

    bot, dp, orchestrator = create_bot(settings)
    register_shutdown(dp, orchestrator)

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
        await orchestrator.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())