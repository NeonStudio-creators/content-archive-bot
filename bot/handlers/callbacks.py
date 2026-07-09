"""
Callback-кнопки переходника публикаций (Instagram + TikTok).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from core.orchestrator import ArchiveOrchestrator
from core.platforms import Platform
from presenter.telegram_presenter import TelegramPresenter

logger = logging.getLogger(__name__)

router = Router(name="callbacks")


def setup_callbacks(
    orchestrator: ArchiveOrchestrator,
    presenter: TelegramPresenter,
) -> Router:
    """Обработка inline-кнопок хаба публикации."""

    async def _handle_hub(
        callback: CallbackQuery,
        *,
        platform: Platform,
        mode: str,
        entity_id: str,
    ) -> None:
        if not callback.message:
            await callback.answer("Нет данных", show_alert=True)
            return

        label = presenter.PUB_MODES[mode]
        await callback.answer(f"Собираю: {label}…")

        if mode == "prof":
            detail = "посты, reels, отметки, сторис…" if platform == Platform.INSTAGRAM else "профиль автора…"
        elif mode == "aud":
            detail = "оригинальный аудиофайл…"
        else:
            detail = label.lower()

        status = await callback.message.answer(
            f"<b>{presenter.BRAND}</b>\n\n"
            f"Собираю <b>{detail}</b>…\n"
            f"<code>{entity_id}</code>",
            parse_mode="HTML",
        )

        try:
            bundle = await orchestrator.process_publication_deep(
                entity_id,
                mode,
                platform=platform,
            )
            if mode == "prof":
                if platform == Platform.TIKTOK:
                    await orchestrator._ensure_tiktok_profile_avatar(bundle)
                else:
                    await orchestrator._ensure_profile_avatar(bundle)
            if mode == "aud":
                if platform == Platform.TIKTOK:
                    audio_bytes, filename = (
                        await orchestrator.download_publication_audio(
                            bundle, platform=Platform.TIKTOK
                        )
                    )
                else:
                    audio_bytes, filename = (
                        await orchestrator.download_publication_audio(bundle)
                    )
                await presenter.send_audio_report(
                    callback.message,
                    bundle,
                    audio_bytes,
                    filename,
                )
            elif mode == "hq":
                if platform == Platform.TIKTOK:
                    await presenter.deliver_hq_video(
                        callback.message,
                        bundle,
                        download_hq=lambda b: orchestrator.download_publication_hq(
                            b, platform=Platform.TIKTOK
                        ),
                    )
                else:
                    try:
                        file_bytes, filename, meta = (
                            await orchestrator.download_publication_hq(bundle)
                        )
                        await presenter.send_hq_report(
                            callback.message,
                            bundle,
                            file_bytes,
                            filename,
                            delivered=meta,
                        )
                    except ValueError as exc:
                        await presenter.send_deep_report(
                            callback.message.bot,
                            callback.message,
                            bundle,
                            mode,
                            notice=str(exc),
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
            logger.warning("Deep %s %s %s: %s", platform.value, mode, entity_id, exc)
            await status.edit_text(f"❌ {exc}", parse_mode="HTML")
        except Exception as exc:
            logger.exception("Deep %s %s %s", platform.value, mode, entity_id)
            err = str(exc)
            if platform == Platform.INSTAGRAM and "400" in err and "Bad Request" in err:
                err = (
                    "Instagram отклонил запрос (400). "
                    "Проверьте /session — нужны свежие sessionid и csrftoken."
                )
            await status.edit_text(f"❌ {err}", parse_mode="HTML")

    @router.callback_query(F.data.startswith("p:"))
    async def handle_instagram_hub(callback: CallbackQuery) -> None:
        if not callback.data:
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
        await _handle_hub(
            callback,
            platform=Platform.INSTAGRAM,
            mode=mode,
            entity_id=shortcode,
        )

    @router.callback_query(F.data.startswith("t:"))
    async def handle_tiktok_hub(callback: CallbackQuery) -> None:
        if not callback.data:
            await callback.answer("Нет данных", show_alert=True)
            return
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("Неверный формат", show_alert=True)
            return
        _, mode, video_id = parts
        if mode not in presenter.PUB_MODES:
            await callback.answer("Неизвестный режим", show_alert=True)
            return
        await _handle_hub(
            callback,
            platform=Platform.TIKTOK,
            mode=mode,
            entity_id=video_id,
        )

    return router