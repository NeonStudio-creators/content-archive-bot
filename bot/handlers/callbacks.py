"""
Callback-кнопки переходника публикаций.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from core.orchestrator import ArchiveOrchestrator
from presenter.telegram_presenter import TelegramPresenter

logger = logging.getLogger(__name__)

router = Router(name="callbacks")


def setup_callbacks(
    orchestrator: ArchiveOrchestrator,
    presenter: TelegramPresenter,
) -> Router:
    """Обработка inline-кнопок хаба публикации."""

    @router.callback_query(F.data.startswith("p:"))
    async def handle_publication_hub(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer("Нет данных", show_alert=True)
            return

        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("Неверный формат", show_alert=True)
            return

        _, mode, shortcode = parts
        if mode not in presenter.PUB_MODES:
            await callback.answer("Неизвестный режим", show_alert=True)
            return

        label = presenter.PUB_MODES[mode]
        await callback.answer(f"Собираю: {label}…")

        if mode == "prof":
            detail = "посты, reels, отметки, сторис…"
        elif mode == "aud":
            detail = "оригинальный аудиофайл…"
        else:
            detail = label.lower()
        status = await callback.message.answer(
            f"<b>{presenter.BRAND}</b>\n\n"
            f"Собираю <b>{detail}</b>…\n"
            f"<code>{shortcode}</code>",
            parse_mode="HTML",
        )

        try:
            bundle = await orchestrator.process_publication_deep(
                shortcode,
                mode,
            )
            if mode == "prof":
                await orchestrator._ensure_profile_avatar(bundle)
            if mode == "aud":
                audio_bytes, filename = (
                    await orchestrator.download_publication_audio(bundle)
                )
                await presenter.send_audio_report(
                    callback.message,
                    bundle,
                    audio_bytes,
                    filename,
                )
            else:
                await presenter.send_deep_report(
                    callback.message.bot,
                    callback.message,
                    bundle,
                    mode,
                )
            await status.delete()
        except ValueError as exc:
            logger.warning("Deep %s %s: %s", mode, shortcode, exc)
            await status.edit_text(f"❌ {exc}", parse_mode="HTML")
        except Exception as exc:
            logger.exception("Deep %s %s", mode, shortcode)
            err = str(exc)
            if "400" in err and "Bad Request" in err:
                err = (
                    "Instagram отклонил запрос (400). "
                    "Проверьте /session — нужны свежие sessionid и csrftoken."
                )
            await status.edit_text(f"❌ {err}", parse_mode="HTML")

    return router