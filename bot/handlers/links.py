"""
Обработка ссылок в сообщениях пользователя.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import Message

from core.fetcher import LinkResolver
from core.orchestrator import ArchiveOrchestrator
from presenter.telegram_presenter import TelegramPresenter

logger = logging.getLogger(__name__)

router = Router(name="links")


def setup_link_handler(
    orchestrator: ArchiveOrchestrator,
    presenter: TelegramPresenter,
) -> Router:
    """Привязывает зависимости к роутеру."""

    @router.message()
    async def handle_message(message: Message) -> None:
        if not message.text:
            return

        urls = LinkResolver.extract_urls(message.text)
        if not urls:
            return

        for url in urls:
            resolved = LinkResolver.resolve(url)
            if resolved is None:
                await presenter.send_error(
                    message,
                    f"Ссылка не распознана: {url}",
                )
                continue

            status_msg = await presenter.send_processing(message, url)

            try:
                bundle = await orchestrator.process_url(
                    LinkResolver.clean_url(url)
                )
                await presenter.send_archive(message.bot, message, bundle)
                await status_msg.delete()
            except ValueError as exc:
                logger.warning("ValueError для %s: %s", url, exc)
                await status_msg.edit_text(
                    f"❌ {exc}",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.exception("Ошибка обработки %s", url)
                err = str(exc)
                if "400" in err and "Bad Request" in err:
                    err = (
                        "Instagram отклонил запрос (400). "
                        "Проверьте /session — нужны свежие sessionid и csrftoken "
                        "из одного браузера (F12 → Cookies → instagram.com)."
                    )
                await status_msg.edit_text(
                    f"❌ {err}",
                    parse_mode="HTML",
                )

    return router