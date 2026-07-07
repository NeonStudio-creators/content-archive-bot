"""
TelegramPresenter — форматирование ArchiveBundle для отправки в Telegram.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import TYPE_CHECKING

from aiogram.types import BufferedInputFile, InputMediaPhoto, InputMediaVideo

from core.models import ArchiveBundle, EntityType

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from config import Settings


class TelegramPresenter:
    """Красивый и понятный вывод: текст + медиа + JSON-дамп."""

    TYPE_EMOJI = {
        EntityType.PROFILE: "👤",
        EntityType.PUBLICATION: "📸",
        EntityType.STORY: "📖",
        EntityType.HIGHLIGHT: "⭐",
        EntityType.COLLECTION: "📁",
        EntityType.UNKNOWN: "🔗",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def format_summary(self, bundle: ArchiveBundle) -> str:
        """Краткая текстовая сводка для первого сообщения."""
        m = bundle.metadata
        emoji = self.TYPE_EMOJI.get(bundle.resolved_type, "🔗")
        lines = [
            f"{emoji} <b>ContentExplorer — архив собран</b>",
            "",
            f"<b>Тип:</b> {bundle.resolved_type.value}",
            f"<b>ID:</b> <code>{m.entity_id or '—'}</code>",
        ]

        if m.username:
            lines.append(f"<b>Аккаунт:</b> @{m.username}")
        if m.display_name:
            lines.append(f"<b>Имя:</b> {m.display_name}")
        if m.title:
            lines.append(f"<b>Заголовок:</b> {m.title}")
        if m.biography:
            bio = m.biography[:200] + "…" if len(m.biography) > 200 else m.biography
            lines.append(f"<b>Описание:</b> {bio}")
        if m.description and m.description != m.biography:
            desc = m.description[:300] + "…" if len(m.description) > 300 else m.description
            lines.append(f"<b>Текст:</b> {desc}")

        lines.append("")
        stats = []
        if m.follower_count is not None:
            stats.append(f"👥 {m.follower_count:,} подписчиков")
        if m.following_count is not None:
            stats.append(f"➡️ {m.following_count:,} подписок")
        if m.publication_count is not None:
            stats.append(f"📷 {m.publication_count:,} публикаций")
        if m.like_count is not None:
            stats.append(f"❤️ {m.like_count:,}")
        if m.comment_count is not None:
            stats.append(f"💬 {m.comment_count:,}")
        if m.view_count is not None:
            stats.append(f"👁 {m.view_count:,}")
        if stats:
            lines.append(" | ".join(stats))

        if m.is_verified:
            lines.append("✅ Верифицирован")
        if m.is_private:
            lines.append("🔒 Приватный аккаунт")
        if m.location:
            lines.append(f"📍 {m.location}")
        if m.tags:
            lines.append(f"<b>Теги:</b> {' '.join(m.tags[:15])}")

        lines.extend([
            "",
            f"<b>Медиа:</b> {len(bundle.media)} файлов",
            f"<b>Связи:</b> {len(bundle.relations)}",
            f"<b>Активность:</b> {len(bundle.activity)} записей",
        ])

        if bundle.collection_stats:
            stat_str = ", ".join(f"{k}: {v}" for k, v in bundle.collection_stats.items())
            lines.append(f"<b>Статистика сбора:</b> {stat_str}")

        lines.append(f"\n🔗 <a href=\"{bundle.source_url}\">Источник</a>")
        return "\n".join(lines)

    def format_media_links(self, bundle: ArchiveBundle, limit: int = 20) -> str:
        """Прямые ссылки на медиа-файлы."""
        if not bundle.media:
            return ""

        lines = ["<b>📎 Прямые ссылки на медиа:</b>", ""]
        for i, asset in enumerate(bundle.media[:limit], 1):
            type_icon = "🎬" if asset.media_type == "video" else "🖼"
            lines.append(f"{i}. {type_icon} <a href=\"{asset.url}\">{asset.id or 'media'}</a>")
            if asset.caption:
                cap = asset.caption[:80] + "…" if len(asset.caption) > 80 else asset.caption
                lines.append(f"   <i>{cap}</i>")

        if len(bundle.media) > limit:
            lines.append(f"\n… и ещё {len(bundle.media) - limit} (см. JSON)")
        return "\n".join(lines)

    def format_relations(self, bundle: ArchiveBundle, limit: int = 15) -> str | None:
        if not bundle.relations:
            return None
        lines = ["<b>🔗 Связи и контекст:</b>", ""]
        for rel in bundle.relations[:limit]:
            lines.append(
                f"• [{rel.relation_type}] {rel.target_label}"
                + (f" (id: {rel.target_id})" if rel.target_id else "")
            )
        if len(bundle.relations) > limit:
            lines.append(f"… +{len(bundle.relations) - limit}")
        return "\n".join(lines)

    def format_activity(self, bundle: ArchiveBundle, limit: int = 10) -> str | None:
        if not bundle.activity:
            return None
        lines = ["<b>💬 Активность:</b>", ""]
        for act in bundle.activity[:limit]:
            actor = f"@{act.actor}" if act.actor else "?"
            content = (act.content or "")[:120]
            lines.append(f"• {actor}: {content}")
        if len(bundle.activity) > limit:
            lines.append(f"… +{len(bundle.activity) - limit}")
        return "\n".join(lines)

    def build_json_dump(self, bundle: ArchiveBundle) -> bytes:
        """Полный JSON-дамп для скачивания."""
        payload = bundle.to_dict()
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return text.encode("utf-8")

    async def send_archive(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
    ) -> None:
        """Отправляет полный архив: сводка → медиа → связи → JSON."""
        # 1. Сводка
        await message.answer(
            self.format_summary(bundle),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

        # 2. Медиа-альбом (первые N файлов)
        await self._send_media_album(bot, message, bundle)

        # 3. Прямые ссылки
        links_text = self.format_media_links(bundle)
        if links_text:
            # Telegram лимит 4096 символов
            if len(links_text) > 4000:
                links_text = links_text[:4000] + "\n…"
            await message.answer(links_text, parse_mode="HTML", disable_web_page_preview=True)

        # 4. Связи
        rel_text = self.format_relations(bundle)
        if rel_text:
            await message.answer(rel_text, parse_mode="HTML")

        # 5. Активность
        act_text = self.format_activity(bundle)
        if act_text:
            await message.answer(act_text, parse_mode="HTML")

        # 6. JSON-дамп как файл
        json_bytes = self.build_json_dump(bundle)
        if len(json_bytes) > 1024:  # отправляем только если есть данные
            filename = f"archive_{bundle.resolved_type.value}_{bundle.metadata.entity_id or 'data'}.json"
            doc = BufferedInputFile(json_bytes, filename=filename)
            await message.answer_document(
                doc,
                caption="📦 Полный JSON-дамп архива",
            )

    async def _send_media_album(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
    ) -> None:
        """Отправляет медиа-группу (до 10 элементов за раз)."""
        media_items = [
            m for m in bundle.media
            if m.url and m.url.startswith("http")
        ][: self.settings.max_media_per_message]

        if not media_items:
            return

        album: list[InputMediaPhoto | InputMediaVideo] = []
        for asset in media_items:
            if asset.media_type == "video":
                album.append(
                    InputMediaVideo(
                        media=asset.url,
                        caption=(asset.caption or "")[:200] or None,
                    )
                )
            else:
                album.append(
                    InputMediaPhoto(
                        media=asset.url,
                        caption=(asset.caption or "")[:200] or None,
                    )
                )

        try:
            await bot.send_media_group(chat_id=message.chat.id, media=album)
        except Exception:
            # Fallback: отправляем ссылки, если Telegram не может загрузить медиа
            fallback = "\n".join(f"• {m.url}" for m in media_items[:5])
            await message.answer(
                f"<b>⚠️ Медиа недоступно для превью.</b>\n{fallback}",
                parse_mode="HTML",
            )

    async def send_error(self, message: Message, error: str) -> None:
        await message.answer(f"❌ <b>Ошибка:</b> {error}", parse_mode="HTML")

    async def send_processing(self, message: Message, url: str) -> Message:
        return await message.answer(
            f"⏳ <b>Собираю архив…</b>\n<code>{url}</code>\n"
            f"<i>Тихий режим — это может занять время</i>",
            parse_mode="HTML",
        )