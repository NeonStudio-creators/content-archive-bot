"""
Инициализация Telegram-бота ContentExplorer.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.handlers import callbacks, commands, links
from config import Settings
from core.orchestrator import ArchiveOrchestrator
from presenter.telegram_presenter import TelegramPresenter

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> tuple[Bot, Dispatcher, ArchiveOrchestrator]:
    """Фабрика: Bot + Dispatcher + Orchestrator."""
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    orchestrator = ArchiveOrchestrator(settings)
    presenter = TelegramPresenter(
        settings,
        fetcher=orchestrator.fetcher,
        tiktok_fetcher=orchestrator.tiktok_fetcher,
    )

    dp.include_router(commands.setup_commands(orchestrator))
    dp.include_router(callbacks.setup_callbacks(orchestrator, presenter))
    dp.include_router(links.setup_link_handler(orchestrator, presenter))

    # Сохраняем orchestrator в workflow_data для graceful shutdown
    dp["orchestrator"] = orchestrator

    return bot, dp, orchestrator


def register_shutdown(dp: Dispatcher, orchestrator: ArchiveOrchestrator) -> None:
    """Регистрирует graceful shutdown (aiogram 3 вызывает handler без аргументов)."""

    async def _on_shutdown() -> None:
        await orchestrator.close()
        logger.info("ArchiveOrchestrator закрыт")

    dp.shutdown.register(_on_shutdown)