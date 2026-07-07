"""
Инициализация Telegram-бота ContentExplorer.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.handlers import commands, links
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
    presenter = TelegramPresenter(settings)

    dp.include_router(commands.router)
    dp.include_router(links.setup_link_handler(orchestrator, presenter))

    # Сохраняем orchestrator в workflow_data для graceful shutdown
    dp["orchestrator"] = orchestrator

    return bot, dp, orchestrator


async def on_shutdown(dispatcher: Dispatcher) -> None:
    orchestrator: ArchiveOrchestrator | None = dispatcher.get("orchestrator")
    if orchestrator:
        await orchestrator.close()
        logger.info("ArchiveOrchestrator закрыт")