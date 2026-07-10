"""
Обработка ссылок в сообщениях пользователя.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import Message

from core.link_resolver import LinkResolver
from core.models import EntityType
from core.platforms import Platform
from api.stats_service import StatsService
from config import secrets_hint
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

            platform_labels = {
                Platform.TIKTOK: "TikTok",
                Platform.YOUTUBE: "YouTube",
                Platform.TELEGRAM: "Telegram",
                Platform.INSTAGRAM: "Instagram",
            }
            platform_label = platform_labels.get(
                resolved.platform, "Instagram"
            )
            if resolved.entity_type == EntityType.PUBLICATION:
                status_msg = await message.answer(
                    f"<b>{presenter.BRAND}</b>\n\n"
                    f"Загружаю публикацию ({platform_label})…\n"
                    f"<blockquote><code>{url}</code></blockquote>",
                    parse_mode="HTML",
                )
            else:
                status_msg = await presenter.send_processing(
                    message, url, platform=platform_label
                )

            try:
                clean = LinkResolver.clean_url(url)
                if resolved.platform == Platform.TELEGRAM:
                    result = await StatsService(orchestrator).fetch(clean)
                    if not result.ok:
                        raise ValueError(result.error or "Не удалось получить статистику")
                    stats = result.stats.to_dict()
                    lines = [
                        f"<b>{presenter.BRAND}</b> · Telegram",
                        "",
                        f"<b>{result.display_name or result.username}</b>",
                        f"<code>{result.url}</code>",
                        "",
                    ]
                    if stats.get("followers") is not None:
                        lines.append(f"Подписчики · <b>{stats['followers']:,}</b>")
                    if stats.get("views") is not None:
                        lines.append(f"Просмотры · <b>{stats['views']:,}</b>")
                    if stats.get("aggregate_views") is not None:
                        lines.append(
                            f"Сумма просмотров (последние посты) · "
                            f"<b>{stats['aggregate_views']:,}</b>"
                        )
                    if stats.get("forwards") is not None:
                        lines.append(f"Пересылки · <b>{stats['forwards']:,}</b>")
                    if stats.get("reactions") is not None:
                        lines.append(f"Реакции · <b>{stats['reactions']:,}</b>")
                    await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
                    continue
                if resolved.entity_type == EntityType.PUBLICATION:
                    bundle = await orchestrator.process_publication_quick(clean)
                    await presenter.send_publication_hub(
                        message.bot,
                        message,
                        bundle,
                        platform=resolved.platform,
                    )
                else:
                    bundle = await orchestrator.process_url(clean)
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
                if resolved.platform == Platform.TIKTOK:
                    if "WAF" in err or "mirror" in err.lower() or "SESSION" in err:
                        err = (
                            f"{err} "
                            f"Добавьте TIKTOK_SESSION_TOKEN в {secrets_hint()} "
                            "(cookie sessionid с tiktok.com, как SESSION_TOKEN для IG)."
                        )
                elif resolved.platform == Platform.YOUTUBE:
                    if "YOUTUBE" in err.upper() or "cookie" in err.lower() or "SESSION" in err:
                        err = (
                            f"{err} "
                            f"Добавьте YOUTUBE_SESSION_TOKEN в {secrets_hint()} "
                            "(cookies SID, SAPISID с youtube.com)."
                        )
                elif "400" in err and "Bad Request" in err:
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