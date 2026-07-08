"""
TelegramPresenter — чистое оформление отчётов ContentExplorer.
Видео сверху + отчёт + JSON. Длинные тексты — в blockquote.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from core.models import ArchiveBundle, EntityType, MediaAsset
from utils import telegram_html as th

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from config import Settings

logger = logging.getLogger(__name__)

TG_MAX_LENGTH = 4096
TG_CAPTION_MAX = 1024

_SEP = "───────────────"


class TelegramPresenter:
    """Минималистичное оформление отчётов для Telegram."""

    BRAND = "ContentExplorer"
    VERSION = "1.1.0"

    TYPE_LABEL = {
        EntityType.PROFILE: "Профиль",
        EntityType.PUBLICATION: "Публикация",
        EntityType.STORY: "Сторис",
        EntityType.HIGHLIGHT: "Актуальное",
        EntityType.COLLECTION: "Коллекция",
        EntityType.UNKNOWN: "Контент",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ig_base = settings.platform_base_url.rstrip("/")

    @staticmethod
    def _kv(key: str, value: str) -> str:
        return f"{th.esc(key)} · {value}"

    @staticmethod
    def _section(title: str) -> str:
        return f"\n<b>{th.esc(title)}</b>\n{_SEP}\n"

    @staticmethod
    def _quote(text: str, *, max_len: int = 500) -> str:
        if not text or not text.strip():
            return ""
        body = th.esc(text.strip()[:max_len])
        if len(text.strip()) > max_len:
            body += "…"
        return f"<blockquote>{body}</blockquote>\n"

    def _header(self, bundle: ArchiveBundle) -> str:
        label = self.TYPE_LABEL.get(bundle.resolved_type, "Контент")
        return f"<b>{self.BRAND}</b>\n{th.esc(label)}\n"

    def _footer(self) -> str:
        return f"\n<i>{self.BRAND} · v{self.VERSION}</i>"

    def _profile_url(self, username: str) -> str:
        return f"{self._ig_base}/{username.strip('/')}/"

    def _profile_link(self, username: str | None, label: str | None = None) -> str:
        if not username:
            return "—"
        text = label or f"@{username}"
        return (
            f'<a href="{th.href(self._profile_url(username))}">{th.esc(text)}</a>'
        )

    def _actor_link(self, username: str | None) -> str:
        if not username:
            return "—"
        return self._profile_link(username, f"@{username}")

    @staticmethod
    def _format_bitrate(bps: int | float | None) -> str:
        if not bps:
            return "—"
        mbps = bps / 1_000_000
        return f"{mbps:.2f} Мбит/с" if mbps >= 1 else f"{bps / 1000:.0f} Кбит/с"

    def _build_keyboard(self, bundle: ArchiveBundle) -> InlineKeyboardMarkup | None:
        buttons: list[InlineKeyboardButton] = []
        m = bundle.metadata

        if m.username:
            buttons.append(
                InlineKeyboardButton(
                    text="Автор",
                    url=self._profile_url(m.username),
                )
            )
        if bundle.source_url:
            label = "Видео" if any(
                a.media_type == "video" for a in bundle.media
            ) else "Пост"
            buttons.append(
                InlineKeyboardButton(text=label, url=bundle.source_url)
            )

        if not buttons:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[buttons])

    def _quality_lines(self, asset: MediaAsset) -> list[str]:
        if asset.media_type != "video":
            return []

        e = asset.extra
        lines: list[str] = []

        res = e.get("resolution") or (
            f"{asset.width}×{asset.height}"
            if asset.width and asset.height
            else None
        )
        if res:
            lines.append(self._kv("Разрешение", f"<b>{th.esc(str(res))}</b>"))

        fps = e.get("fps")
        if fps:
            lines.append(self._kv("FPS", f"<b>{fps}</b>"))

        if asset.duration_sec:
            lines.append(self._kv("Длительность", f"<b>{asset.duration_sec:.1f} с</b>"))

        codec = e.get("video_codec") or (
            ", ".join(e["codecs"]) if e.get("codecs") else None
        )
        if codec:
            lines.append(self._kv("Кодек", f"<code>{th.esc(str(codec))}</code>"))

        if e.get("audio_codec"):
            lines.append(
                self._kv("Аудиокодек", f"<code>{th.esc(e['audio_codec'])}</code>")
            )
        elif e.get("has_audio") is not None:
            lines.append(
                self._kv("Звук", f"<b>{'да' if e['has_audio'] else 'нет'}</b>")
            )

        if e.get("bandwidth_bps"):
            lines.append(
                self._kv("Битрейт", f"<b>{self._format_bitrate(e['bandwidth_bps'])}</b>")
            )

        if e.get("number_of_qualities"):
            lines.append(
                self._kv("Варианты качества", f"<b>{e['number_of_qualities']}</b>")
            )

        if e.get("aspect_ratio"):
            lines.append(self._kv("Соотношение", f"<b>{e['aspect_ratio']}</b>"))

        if e.get("product_type"):
            lines.append(
                self._kv("Формат", f"<b>{th.esc(str(e['product_type']))}</b>")
            )

        variants = e.get("quality_variants") or []
        shown = 0
        for v in variants:
            w, h = v.get("width"), v.get("height")
            if w and h:
                lines.append(f"  {w}×{h}")
                shown += 1
            if shown >= 5:
                break

        dash_reps = e.get("dash_representations") or []
        for rep in dash_reps[:3]:
            w, h = rep.get("width"), rep.get("height")
            rfps = rep.get("fps")
            parts = []
            if w and h:
                parts.append(f"{w}×{h}")
            if rfps:
                parts.append(f"{rfps} fps")
            if rep.get("codec"):
                parts.append(th.esc(str(rep["codec"])[:20]))
            if parts:
                lines.append(f"  DASH · {' · '.join(parts)}")

        return lines

    def _extra_quotes(self, bundle: ArchiveBundle) -> list[str]:
        """Доп. информация — только в цитатах."""
        quotes: list[str] = []
        m = bundle.metadata
        owner_extra = m.raw_fields or {}

        if m.description:
            quotes.append(self._quote(m.description, max_len=600))

        if m.biography and not m.description:
            quotes.append(self._quote(m.biography, max_len=400))

        if owner_extra.get("owner_bio"):
            quotes.append(self._quote(owner_extra["owner_bio"], max_len=300))

        for asset in bundle.media:
            if asset.media_type != "video":
                continue
            e = asset.extra
            music = e.get("music") or {}
            if music.get("title") or music.get("artist"):
                music_text = " — ".join(
                    p for p in (music.get("artist"), music.get("title")) if p
                )
                quotes.append(self._quote(f"♫ {music_text}", max_len=200))
            if e.get("accessibility_caption"):
                quotes.append(
                    self._quote(e["accessibility_caption"], max_len=250)
                )
            break

        comments = [
            a for a in bundle.activity if a.activity_type == "comment"
        ]
        if comments:
            comment_lines: list[str] = []
            for act in comments[:8]:
                actor = self._actor_link(act.actor)
                text = (act.content or "").strip()
                if text:
                    comment_lines.append(f"{actor}: {th.esc(text[:120])}")
                else:
                    comment_lines.append(str(actor))
            if comment_lines:
                body = "\n".join(comment_lines)
                quotes.append(f"<blockquote>{body}</blockquote>\n")

        return [q for q in quotes if q]

    def _pick_preview_media(self, bundle: ArchiveBundle) -> MediaAsset | None:
        valid = [m for m in bundle.media if m.url and m.url.startswith("http")]
        if not valid:
            return None
        for asset in valid:
            if asset.media_type == "video":
                return asset
        return valid[0]

    def format_full_report(self, bundle: ArchiveBundle) -> str:
        m = bundle.metadata
        owner_extra = m.raw_fields or {}

        parts: list[str] = [self._header(bundle)]

        # ── Автор ──
        author_lines: list[str] = []
        if m.username:
            author_lines.append(
                self._kv("Ник", self._profile_link(m.username))
            )
        if m.display_name:
            name_val = (
                self._profile_link(m.username, m.display_name)
                if m.username
                else th.esc(m.display_name)
            )
            author_lines.append(self._kv("Имя", name_val))
        if m.is_verified:
            author_lines.append(self._kv("Верификация", "<b>да</b>"))
        if m.is_private:
            author_lines.append(self._kv("Приватный", "<b>да</b>"))
        if m.follower_count is not None:
            author_lines.append(
                self._kv("Подписчики", f"<b>{m.follower_count:,}</b>")
            )
        if m.following_count is not None:
            author_lines.append(
                self._kv("Подписки", f"<b>{m.following_count:,}</b>")
            )
        if owner_extra.get("owner_followers"):
            author_lines.append(
                self._kv(
                    "Подписчики автора",
                    f"<b>{owner_extra['owner_followers']:,}</b>",
                )
            )
        if author_lines:
            parts.append(self._section("Автор"))
            parts.extend(line + "\n" for line in author_lines)

        # ── Контент ──
        content_title = (
            "Публикация"
            if bundle.resolved_type == EntityType.PUBLICATION
            else "Контент"
        )
        content_lines: list[str] = []
        if m.entity_id:
            content_lines.append(
                self._kv("ID", f"<code>{th.esc(m.entity_id)}</code>")
            )
        if m.title:
            content_lines.append(
                self._kv("Shortcode", f"<code>{th.esc(m.title)}</code>")
            )
        if m.created_at:
            content_lines.append(
                self._kv(
                    "Дата",
                    f"<b>{m.created_at.strftime('%d.%m.%Y %H:%M')} UTC</b>",
                )
            )
        if m.location:
            content_lines.append(self._kv("Локация", th.esc(m.location)))
        if m.external_url:
            content_lines.append(
                self._kv(
                    "Ссылка",
                    f'<a href="{th.href(m.external_url)}">внешняя</a>',
                )
            )
        if content_lines:
            parts.append(self._section(content_title))
            parts.extend(line + "\n" for line in content_lines)

        # ── Доп. информация (цитаты) ──
        extra_quotes = self._extra_quotes(bundle)
        if extra_quotes:
            parts.append(self._section("Описание"))
            parts.extend(extra_quotes)

        # ── Статистика ──
        stat_lines: list[str] = []
        if m.view_count is not None:
            stat_lines.append(
                self._kv("Просмотры", f"<b>{m.view_count:,}</b>")
            )
        if m.like_count is not None:
            stat_lines.append(self._kv("Лайки", f"<b>{m.like_count:,}</b>"))
        if m.comment_count is not None:
            stat_lines.append(
                self._kv("Комментарии", f"<b>{m.comment_count:,}</b>")
            )
        if m.publication_count is not None:
            stat_lines.append(
                self._kv("Публикации", f"<b>{m.publication_count:,}</b>")
            )
        for k, v in (bundle.collection_stats or {}).items():
            stat_lines.append(self._kv(th.esc(k), f"<b>{v}</b>"))
        if stat_lines:
            parts.append(self._section("Статистика"))
            parts.extend(line + "\n" for line in stat_lines)

        # ── Качество видео ──
        for asset in bundle.media:
            if asset.media_type == "video":
                ql = self._quality_lines(asset)
                if ql:
                    parts.append(self._section("Видео"))
                    parts.extend(line + "\n" for line in ql)
                break

        # ── Теги и упоминания ──
        tag_lines: list[str] = []
        if m.tags:
            tag_lines.append(" ".join(f"#{th.esc(t.lstrip('#'))}" for t in m.tags[:15]))
        for u in (owner_extra.get("mentions") or [])[:8]:
            tag_lines.append(f"  {self._profile_link(u)}")
        if tag_lines:
            parts.append(self._section("Теги"))
            parts.extend(line + "\n" for line in tag_lines)

        # ── Медиа ──
        if bundle.media:
            media_lines: list[str] = [
                self._kv("Файлов", f"<b>{len(bundle.media)}</b>")
            ]
            for i, asset in enumerate(bundle.media[:8], 1):
                kind = "видео" if asset.media_type == "video" else "фото"
                media_lines.append(
                    f'  <a href="{th.href(asset.url)}">Скачать #{i}</a> · {kind}'
                )
            if len(bundle.media) > 8:
                media_lines.append(
                    f"  +{len(bundle.media) - 8} ещё в JSON"
                )
            parts.append(self._section("Медиа"))
            parts.extend(line + "\n" for line in media_lines)

        # ── Связи ──
        if bundle.relations:
            rel_lines: list[str] = []
            for rel in bundle.relations[:8]:
                label_txt = rel.target_label
                if re.fullmatch(r"[A-Za-z0-9_.]+", label_txt):
                    label_html = self._profile_link(label_txt, f"@{label_txt}")
                else:
                    label_html = th.esc(label_txt)
                rel_lines.append(
                    f"  <code>{th.esc(rel.relation_type)}</code> · {label_html}"
                )
            if len(bundle.relations) > 8:
                rel_lines.append(f"  +{len(bundle.relations) - 8}")
            parts.append(self._section("Связи"))
            parts.extend(line + "\n" for line in rel_lines)

        # ── Лайки (комментарии уже в цитатах) ──
        likes = [a for a in bundle.activity if a.activity_type == "like"]
        if likes:
            parts.append(self._section("Лайки"))
            parts.append(self._kv("Собрано", f"<b>{len(likes)}</b>") + "\n")
            likers = " · ".join(
                self._actor_link(a.actor) for a in likes[:8] if a.actor
            )
            if likers:
                parts.append(f"  {likers}\n")

        parts.append(self._footer())
        return th.truncate_html("".join(parts), TG_MAX_LENGTH)

    def build_json_dump(self, bundle: ArchiveBundle) -> bytes:
        payload = bundle.to_dict()
        payload["metadata"]["raw_fields"] = bundle.metadata.raw_fields
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return text.encode("utf-8")

    async def _send_html(
        self,
        message: Message,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> None:
        safe = th.truncate_html(text, TG_MAX_LENGTH)
        try:
            await message.answer(
                safe, parse_mode="HTML", reply_markup=keyboard
            )
        except TelegramBadRequest as exc:
            logger.warning("HTML fallback: %s", exc)
            plain = th.strip_to_plain(safe)
            await message.answer(plain[:4090], reply_markup=keyboard)

    async def send_archive(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
    ) -> None:
        report = self.format_full_report(bundle)
        preview = self._pick_preview_media(bundle)
        caption = th.truncate_html(report, TG_CAPTION_MAX)
        keyboard = self._build_keyboard(bundle)

        sent = False
        if preview:
            try:
                if preview.media_type == "video":
                    await message.answer_video(
                        video=preview.url,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                else:
                    await message.answer_photo(
                        photo=preview.url,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                sent = True
            except TelegramBadRequest as exc:
                logger.warning("Media caption error: %s", exc)
                try:
                    if preview.media_type == "video":
                        await message.answer_video(
                            preview.url, reply_markup=keyboard
                        )
                    else:
                        await message.answer_photo(
                            preview.url, reply_markup=keyboard
                        )
                    await self._send_html(message, report, keyboard)
                    sent = True
                except Exception:
                    pass

        if not sent:
            await self._send_html(message, report, keyboard)

        json_bytes = self.build_json_dump(bundle)
        filename = (
            f"archive_{bundle.resolved_type.value}_"
            f"{bundle.metadata.entity_id or 'data'}.json"
        )
        await message.answer_document(
            BufferedInputFile(json_bytes, filename=filename),
            caption=f"{self.BRAND} · полный JSON-архив",
            parse_mode="HTML",
        )

    async def send_error(self, message: Message, error: str) -> None:
        await message.answer(
            f"<b>{self.BRAND}</b>\n\n"
            f"<b>Ошибка</b>\n"
            f"<blockquote>{th.esc(error)}</blockquote>",
            parse_mode="HTML",
        )

    async def send_processing(self, message: Message, url: str) -> Message:
        return await message.answer(
            f"<b>{self.BRAND}</b>\n\n"
            f"Собираю данные…\n"
            f"<blockquote><code>{th.esc(url)}</code></blockquote>",
            parse_mode="HTML",
        )