"""
TelegramPresenter — форматирование ArchiveBundle для Telegram.
Сообщение 1: видео/фото сверху + полный отчёт в caption/тексте.
Сообщение 2: JSON-файл.
"""

from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING

from aiogram.types import BufferedInputFile

from core.models import ArchiveBundle, EntityType, MediaAsset

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from config import Settings

TG_MAX_LENGTH = 4096
TG_CAPTION_MAX = 1024
INSTAGRAM_BASE = "https://www.instagram.com"


class TelegramPresenter:
    """Красивый вывод: медиа сверху + один отчёт + JSON."""

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
        self._ig_base = settings.platform_base_url.rstrip("/")

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

    def _profile_url(self, username: str) -> str:
        return f"{self._ig_base}/{username.strip('/')}/"

    def _profile_link(
        self,
        username: str | None,
        label: str | None = None,
    ) -> str:
        if not username:
            return ""
        text = label or f"@{username}"
        return f'<a href="{self._profile_url(username)}">{self._esc(text)}</a>'

    def _actor_link(self, username: str | None) -> str:
        if not username:
            return "?"
        return self._profile_link(username, f"@{username}")

    def _relation_label(self, rel_type: str, label: str) -> str:
        """Ссылка на профиль, если target похож на username."""
        if re.fullmatch(r"[A-Za-z0-9_.]+", label) and rel_type in {
            "tagged_user",
            "cohost",
            "related_profile",
            "liker",
            "tagged_in",
        }:
            return self._profile_link(label, f"@{label}")
        if re.fullmatch(r"[A-Za-z0-9_-]+", label) and rel_type in {
            "publication",
            "reel",
            "saved_publication",
        }:
            return f'<a href="{self._ig_base}/p/{label}/">{self._esc(label)}</a>'
        return self._esc(label)

    def _pick_preview_media(self, bundle: ArchiveBundle) -> MediaAsset | None:
        """Видео приоритетно — будет сверху сообщения."""
        valid = [m for m in bundle.media if m.url and m.url.startswith("http")]
        if not valid:
            return None
        for asset in valid:
            if asset.media_type == "video":
                return asset
        return valid[0]

    def format_full_report(self, bundle: ArchiveBundle) -> str:
        m = bundle.metadata
        emoji = self.TYPE_EMOJI.get(bundle.resolved_type, "🔗")
        label = self.TYPE_LABEL.get(bundle.resolved_type, "Объект")

        lines: list[str] = [
            f"{emoji} <b>CONTENT EXPLORER</b>",
            self._sep("━"),
            f"<b>{label}</b>  ·  <code>{self._esc(m.entity_id or '—')}</code>",
            "",
        ]

        info: list[str] = []
        if m.username:
            info.append(f"👤 {self._profile_link(m.username)}")
        if m.display_name:
            if m.username:
                info.append(
                    f"📛 {self._profile_link(m.username, m.display_name)}"
                )
            else:
                info.append(f"📛 {self._esc(m.display_name)}")
        if m.title:
            info.append(f"🏷 <code>{self._esc(m.title)}</code>")
        if m.is_verified:
            info.append("✅ Верифицирован")
        if m.is_private:
            info.append("🔒 Приватный")
        if m.location:
            info.append(f"📍 {self._esc(m.location)}")
        if m.external_url:
            info.append(f'🌐 <a href="{m.external_url}">Внешняя ссылка</a>')
        if info:
            lines.extend(info)
            lines.append("")

        stats: list[str] = []
        if m.follower_count is not None:
            stats.append(f"👥 <b>{m.follower_count:,}</b>")
        if m.following_count is not None:
            stats.append(f"➡️ <b>{m.following_count:,}</b>")
        if m.publication_count is not None:
            stats.append(f"📷 <b>{m.publication_count:,}</b>")
        if m.like_count is not None:
            stats.append(f"❤️ <b>{m.like_count:,}</b>")
        if m.comment_count is not None:
            stats.append(f"💬 <b>{m.comment_count:,}</b>")
        if m.view_count is not None:
            stats.append(f"👁 <b>{m.view_count:,}</b>")

        owner_extra = m.raw_fields or {}
        if owner_extra.get("owner_followers"):
            lines.append(self._sep())
            lines.append("👤 <b>Автор (расширено)</b>")
            if m.username:
                lines.append(f"  {self._profile_link(m.username)}")
            lines.append(
                f"  👥 {owner_extra['owner_followers']:,} подп. · "
                f"📷 {owner_extra.get('owner_posts', '?')} постов"
            )
            if owner_extra.get("owner_bio"):
                lines.append(f"  <i>{self._esc(owner_extra['owner_bio'], 200)}</i>")
            lines.append("")

        if stats:
            lines.append(self._sep())
            lines.append("📊 <b>Статистика</b>")
            lines.append("  ".join(stats))
            lines.append("")

        if m.biography:
            lines.append(self._sep())
            lines.append("📝 <b>Био</b>")
            lines.append(f"<i>{self._esc(m.biography, 500)}</i>")
            lines.append("")
        if m.description and m.description != m.biography:
            lines.append(self._sep())
            lines.append("💬 <b>Текст</b>")
            lines.append(f"<i>{self._esc(m.description, 600)}</i>")
            lines.append("")
        if m.tags:
            lines.append(f"🏷 {' '.join(self._esc(t) for t in m.tags[:20])}")
            lines.append("")
        mentions = owner_extra.get("mentions") or []
        if mentions:
            lines.append(
                "📢 "
                + " ".join(self._profile_link(u) for u in mentions[:10])
            )
            lines.append("")

        if bundle.media:
            lines.append(self._sep())
            lines.append(f"🖼 <b>Медиа</b> ({len(bundle.media)})")
            for i, asset in enumerate(bundle.media[:20], 1):
                icon = "🎬" if asset.media_type == "video" else "🖼"
                dur = (
                    f" · {asset.duration_sec:.0f}s"
                    if asset.duration_sec
                    else ""
                )
                lines.append(
                    f"  {i}. {icon} <a href=\"{asset.url}\">"
                    f"{self._esc(asset.id or f'file_{i}')}</a>{dur}"
                )
            if len(bundle.media) > 20:
                lines.append(f"  <i>+{len(bundle.media) - 20} в JSON</i>")
            lines.append("")

        if bundle.relations:
            lines.append(self._sep())
            lines.append(f"🔗 <b>Связи</b> ({len(bundle.relations)})")
            for rel in bundle.relations[:15]:
                lines.append(
                    f"  • <code>{self._esc(rel.relation_type)}</code> "
                    f"{self._relation_label(rel.relation_type, rel.target_label)}"
                )
            if len(bundle.relations) > 15:
                lines.append(f"  <i>+{len(bundle.relations) - 15}</i>")
            lines.append("")

        comments = [a for a in bundle.activity if a.activity_type == "comment"]
        likes = [a for a in bundle.activity if a.activity_type == "like"]

        if comments:
            lines.append(self._sep())
            lines.append(f"💬 <b>Комментарии</b> ({len(comments)})")
            for act in comments[:12]:
                likes_n = act.extra.get("likes", 0)
                suffix = f" ❤️{likes_n}" if likes_n else ""
                lines.append(
                    f"  • {self._actor_link(act.actor)}: "
                    f"<i>{self._esc(act.content, 120)}</i>{suffix}"
                )
            if len(comments) > 12:
                lines.append(f"  <i>+{len(comments) - 12}</i>")
            lines.append("")

        if likes:
            lines.append(self._sep())
            lines.append(f"❤️ <b>Лайки</b> ({len(likes)})")
            like_links = [
                self._actor_link(a.actor) for a in likes[:15] if a.actor
            ]
            lines.append("  " + " · ".join(like_links))
            if len(likes) > 15:
                lines.append(f"  <i>+{len(likes) - 15}</i>")
            lines.append("")

        lines.append(self._sep())
        parts = [
            f"📎 {len(bundle.media)} медиа",
            f"🔗 {len(bundle.relations)} связей",
            f"💬 {len(bundle.activity)} записей",
        ]
        for k, v in (bundle.collection_stats or {}).items():
            parts.append(f"{k}: {v}")
        lines.append("📦 <b>Итог:</b> " + " · ".join(parts))
        lines.append(f'🌐 <a href="{bundle.source_url}">Источник</a>')

        text = "\n".join(lines)
        limit = TG_MAX_LENGTH
        if len(text) > limit:
            text = text[: limit - 25] + "\n\n<i>…полный дамп в JSON ↓</i>"
        return text

    @staticmethod
    def _fit_caption(text: str) -> str:
        if len(text) <= TG_CAPTION_MAX:
            return text
        return text[: TG_CAPTION_MAX - 25] + "\n\n<i>…полный отчёт в JSON ↓</i>"

    def build_json_dump(self, bundle: ArchiveBundle) -> bytes:
        payload = bundle.to_dict()
        payload["metadata"]["raw_fields"] = bundle.metadata.raw_fields
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return text.encode("utf-8")

    async def send_archive(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
    ) -> None:
        report = self.format_full_report(bundle)
        preview = self._pick_preview_media(bundle)

        # Сообщение 1: видео/фото сверху + отчёт
        if preview:
            caption = self._fit_caption(report)
            try:
                if preview.media_type == "video":
                    await message.answer_video(
                        video=preview.url,
                        caption=caption,
                        parse_mode="HTML",
                    )
                else:
                    await message.answer_photo(
                        photo=preview.url,
                        caption=caption,
                        parse_mode="HTML",
                    )
            except Exception:
                await message.answer(
                    report,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
        else:
            await message.answer(
                report,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )

        # Сообщение 2: JSON
        json_bytes = self.build_json_dump(bundle)
        filename = (
            f"archive_{bundle.resolved_type.value}_"
            f"{bundle.metadata.entity_id or 'data'}.json"
        )
        await message.answer_document(
            BufferedInputFile(json_bytes, filename=filename),
            caption="📄 <b>Полный JSON-дамп</b>",
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
            f"<i>Параллельный сбор · подождите</i>",
            parse_mode="HTML",
        )