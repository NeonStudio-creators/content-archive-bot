"""
TelegramPresenter — форматирование ArchiveBundle для отправки в Telegram.
Сообщение 1: полный отчёт. Сообщение 2: JSON-файл.
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from aiogram.types import BufferedInputFile

from core.models import ArchiveBundle, EntityType

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from config import Settings

TG_MAX_LENGTH = 4096


class TelegramPresenter:
    """Красивый вывод: один текст + один JSON-файл."""

    TYPE_EMOJI = {
        EntityType.PROFILE: "👤",
        EntityType.PUBLICATION: "📸",
        EntityType.STORY: "📖",
        EntityType.HIGHLIGHT: "⭐",
        EntityType.COLLECTION: "📁",
        EntityType.UNKNOWN: "🔗",
    }

    TYPE_LABEL = {
        EntityType.PROFILE: "Профиль",
        EntityType.PUBLICATION: "Публикация",
        EntityType.STORY: "История",
        EntityType.HIGHLIGHT: "Хайлайт",
        EntityType.COLLECTION: "Коллекция",
        EntityType.UNKNOWN: "Объект",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _esc(text: str | None, limit: int | None = None) -> str:
        if not text:
            return ""
        safe = html.escape(text)
        if limit and len(safe) > limit:
            return safe[: limit - 1] + "…"
        return safe

    @staticmethod
    def _sep(char: str = "─", width: int = 28) -> str:
        return char * width

    def format_full_report(self, bundle: ArchiveBundle) -> str:
        """Единое оформленное сообщение со всеми данными."""
        m = bundle.metadata
        emoji = self.TYPE_EMOJI.get(bundle.resolved_type, "🔗")
        label = self.TYPE_LABEL.get(bundle.resolved_type, "Объект")

        lines: list[str] = [
            f"{emoji} <b>CONTENT EXPLORER</b>",
            self._sep("━"),
            f"<b>{label}</b>  ·  <code>{self._esc(m.entity_id or '—')}</code>",
            "",
        ]

        # ── Основная информация ──
        info: list[str] = []
        if m.username:
            info.append(f"👤 @{self._esc(m.username)}")
        if m.display_name:
            info.append(f"📛 {self._esc(m.display_name)}")
        if m.title:
            info.append(f"🏷 {self._esc(m.title)}")
        if m.is_verified:
            info.append("✅ Верифицирован")
        if m.is_private:
            info.append("🔒 Приватный")
        if m.location:
            info.append(f"📍 {self._esc(m.location)}")
        if info:
            lines.append("\n".join(info))
            lines.append("")

        # ── Статистика ──
        stats: list[str] = []
        if m.follower_count is not None:
            stats.append(f"👥 <b>{m.follower_count:,}</b> подп.")
        if m.following_count is not None:
            stats.append(f"➡️ <b>{m.following_count:,}</b> подписок")
        if m.publication_count is not None:
            stats.append(f"📷 <b>{m.publication_count:,}</b> постов")
        if m.like_count is not None:
            stats.append(f"❤️ <b>{m.like_count:,}</b>")
        if m.comment_count is not None:
            stats.append(f"💬 <b>{m.comment_count:,}</b>")
        if m.view_count is not None:
            stats.append(f"👁 <b>{m.view_count:,}</b>")
        if stats:
            lines.append(self._sep())
            lines.append("📊 <b>Статистика</b>")
            lines.append("  ".join(stats))
            lines.append("")

        # ── Текст / описание ──
        if m.biography:
            lines.append(self._sep())
            lines.append("📝 <b>Био</b>")
            lines.append(f"<i>{self._esc(m.biography, 400)}</i>")
            lines.append("")
        if m.description and m.description != m.biography:
            lines.append(self._sep())
            lines.append("💬 <b>Текст публикации</b>")
            lines.append(f"<i>{self._esc(m.description, 500)}</i>")
            lines.append("")
        if m.tags:
            lines.append(f"🏷 {' '.join(self._esc(t) for t in m.tags[:12])}")
            lines.append("")

        # ── Медиа ──
        if bundle.media:
            lines.append(self._sep())
            lines.append(f"🖼 <b>Медиа</b>  ({len(bundle.media)} файлов)")
            for i, asset in enumerate(bundle.media[:15], 1):
                icon = "🎬" if asset.media_type == "video" else "🖼"
                name = self._esc(asset.id or f"file_{i}")
                lines.append(f"  {i}. {icon} <a href=\"{asset.url}\">{name}</a>")
                if asset.caption:
                    lines.append(f"     <i>{self._esc(asset.caption, 80)}</i>")
            if len(bundle.media) > 15:
                lines.append(f"  <i>… ещё {len(bundle.media) - 15} в JSON</i>")
            lines.append("")

        # ── Связи ──
        if bundle.relations:
            lines.append(self._sep())
            lines.append(f"🔗 <b>Связи</b>  ({len(bundle.relations)})")
            for rel in bundle.relations[:10]:
                lines.append(
                    f"  • <code>{self._esc(rel.relation_type)}</code> "
                    f"{self._esc(rel.target_label)}"
                )
            if len(bundle.relations) > 10:
                lines.append(f"  <i>… +{len(bundle.relations) - 10}</i>")
            lines.append("")

        # ── Активность ──
        if bundle.activity:
            lines.append(self._sep())
            lines.append(f"💬 <b>Активность</b>  ({len(bundle.activity)})")
            for act in bundle.activity[:8]:
                actor = f"@{self._esc(act.actor)}" if act.actor else "?"
                lines.append(f"  • {actor}: <i>{self._esc(act.content, 100)}</i>")
            if len(bundle.activity) > 8:
                lines.append(f"  <i>… +{len(bundle.activity) - 8}</i>")
            lines.append("")

        # ── Итог сбора ──
        lines.append(self._sep())
        summary_parts = [
            f"📎 {len(bundle.media)} медиа",
            f"🔗 {len(bundle.relations)} связей",
            f"💬 {len(bundle.activity)} записей",
        ]
        if bundle.collection_stats:
            for k, v in bundle.collection_stats.items():
                summary_parts.append(f"{k}: {v}")
        lines.append("📦 <b>Итог:</b> " + " · ".join(summary_parts))
        lines.append(f"🌐 <a href=\"{bundle.source_url}\">Открыть источник</a>")

        text = "\n".join(lines)
        if len(text) > TG_MAX_LENGTH:
            text = text[: TG_MAX_LENGTH - 20] + "\n\n<i>…обрезано, см. JSON</i>"
        return text

    def build_json_dump(self, bundle: ArchiveBundle) -> bytes:
        payload = bundle.to_dict()
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return text.encode("utf-8")

    async def send_archive(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
    ) -> None:
        """Сообщение 1 — полный отчёт. Сообщение 2 — JSON-файл."""
        # 1. Всё в одном сообщении
        await message.answer(
            self.format_full_report(bundle),
            parse_mode="HTML",
            disable_web_page_preview=False,
        )

        # 2. JSON-файл
        json_bytes = self.build_json_dump(bundle)
        filename = (
            f"archive_{bundle.resolved_type.value}_"
            f"{bundle.metadata.entity_id or 'data'}.json"
        )
        doc = BufferedInputFile(json_bytes, filename=filename)
        await message.answer_document(
            doc,
            caption="📄 <b>Полный JSON-дамп архива</b>",
            parse_mode="HTML",
        )

    async def send_error(self, message: Message, error: str) -> None:
        await message.answer(
            f"❌ <b>Ошибка</b>\n{self._sep()}\n{self._esc(error)}",
            parse_mode="HTML",
        )

    async def send_processing(self, message: Message, url: str) -> Message:
        return await message.answer(
            f"⏳ <b>Собираю архив…</b>\n"
            f"{self._sep()}\n"
            f"🔗 <code>{self._esc(url)}</code>\n\n"
            f"<i>Тихий режим — подождите</i>",
            parse_mode="HTML",
        )