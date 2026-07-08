"""
TelegramPresenter — стиль оформления как @reTikTok_bot.
Видео сверху + checker-отчёт + JSON.
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

# Стиль re:TikTok — иерархия маркеров
_BULLET = "🧲"
_NEST1 = "👻🧲"
_NEST2 = "👻👻🧲"


class TelegramPresenter:
    """Оформление в стиле re:TikTok Checker."""

    BRAND = "re:Instagram"
    VERSION = "1.0.0"

    TYPE_LABEL = {
        EntityType.PROFILE: "Profile",
        EntityType.PUBLICATION: "Publication",
        EntityType.STORY: "Story",
        EntityType.HIGHLIGHT: "Highlight",
        EntityType.COLLECTION: "Collection",
        EntityType.UNKNOWN: "Content",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ig_base = settings.platform_base_url.rstrip("/")

    @staticmethod
    def _item(text: str, level: int = 0) -> str:
        prefix = _BULLET if level == 0 else (_NEST1 if level == 1 else _NEST2)
        return f"{prefix} {text}"

    @staticmethod
    def _section(title: str, lines: list[str]) -> str:
        if not lines:
            return ""
        body = "\n".join(lines)
        return f"<blockquote><b>❤️ {th.esc(title)}</b>\n{body}</blockquote>\n"

    def _header(self, mode: str = "Checker") -> str:
        return f"⚡ <b>{self.BRAND}</b> {mode}\n"

    def _footer(self) -> str:
        return f"\n⚡ {_BULLET} <b>{self.BRAND}</b>"

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
        return f"{mbps:.2f} Mbps" if mbps >= 1 else f"{bps / 1000:.0f} Kbps"

    def _build_keyboard(self, bundle: ArchiveBundle) -> InlineKeyboardMarkup | None:
        buttons: list[InlineKeyboardButton] = []
        m = bundle.metadata

        if m.username:
            buttons.append(
                InlineKeyboardButton(
                    text="👤 Author",
                    url=self._profile_url(m.username),
                )
            )
        if bundle.source_url:
            buttons.append(
                InlineKeyboardButton(text="🎬 Video", url=bundle.source_url)
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
            lines.append(self._item(f"Resolution: <b>{th.esc(str(res))}</b>"))

        fps = e.get("fps")
        if fps:
            lines.append(self._item(f"FPS: <b>{fps}</b>"))

        if asset.duration_sec:
            lines.append(self._item(f"Duration: <b>{asset.duration_sec:.2f}s</b>"))

        codec = e.get("video_codec") or (
            ", ".join(e["codecs"]) if e.get("codecs") else None
        )
        if codec:
            lines.append(self._item(f"Codec: <code>{th.esc(str(codec))}</code>"))

        if e.get("audio_codec"):
            lines.append(
                self._item(f"Audio codec: <code>{th.esc(e['audio_codec'])}</code>")
            )
        elif e.get("has_audio") is not None:
            lines.append(
                self._item(f"Audio: <b>{'yes' if e['has_audio'] else 'no'}</b>")
            )

        if e.get("bandwidth_bps"):
            lines.append(
                self._item(f"Bitrate: <b>{self._format_bitrate(e['bandwidth_bps'])}</b>")
            )

        if e.get("number_of_qualities"):
            lines.append(
                self._item(f"Quality variants: <b>{e['number_of_qualities']}</b>")
            )

        if e.get("aspect_ratio"):
            lines.append(self._item(f"Aspect ratio: <b>{e['aspect_ratio']}</b>"))

        if e.get("product_type"):
            lines.append(
                self._item(f"Format: <b>{th.esc(str(e['product_type']))}</b>")
            )

        variants = e.get("quality_variants") or []
        shown = 0
        for v in variants:
            w, h = v.get("width"), v.get("height")
            if w and h:
                lines.append(self._item(f"{w}×{h}", level=1))
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
                parts.append(f"{rfps}fps")
            if rep.get("codec"):
                parts.append(th.esc(str(rep["codec"])[:20]))
            if parts:
                lines.append(self._item("DASH: " + " · ".join(parts), level=1))

        music = e.get("music") or {}
        if music.get("title") or music.get("artist"):
            lines.append(
                self._item(
                    f"Music: {th.esc(music.get('artist', ''))} — "
                    f"{th.esc(music.get('title', ''))}"
                )
            )

        if e.get("accessibility_caption"):
            lines.append(
                self._item(f"<i>{th.esc(str(e['accessibility_caption'])[:100])}</i>")
            )

        return lines

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
        label = self.TYPE_LABEL.get(bundle.resolved_type, "Content")
        owner_extra = m.raw_fields or {}

        parts: list[str] = [self._header("Checker")]

        # ── Author ──
        author_lines: list[str] = []
        if m.username:
            author_lines.append(self._item(f"Username: {self._profile_link(m.username)}"))
        if m.display_name:
            author_lines.append(
                self._item(
                    f"Name: {self._profile_link(m.username, m.display_name) if m.username else th.esc(m.display_name)}"
                )
            )
        if m.is_verified:
            author_lines.append(self._item("Verified: <b>yes</b>"))
        if m.is_private:
            author_lines.append(self._item("Private: <b>yes</b>"))
        if m.follower_count is not None:
            author_lines.append(
                self._item(f"Followers: <b>{m.follower_count:,}</b>")
            )
        if m.following_count is not None:
            author_lines.append(
                self._item(f"Following: <b>{m.following_count:,}</b>")
            )
        if owner_extra.get("owner_followers"):
            author_lines.append(
                self._item(f"Author followers: <b>{owner_extra['owner_followers']:,}</b>", level=1)
            )
        if owner_extra.get("owner_bio"):
            author_lines.append(
                self._item(f"<i>{th.esc(owner_extra['owner_bio'][:150])}</i>", level=1)
            )
        if author_lines:
            parts.append(self._section("Author", author_lines))

        # ── Video / Content ──
        content_lines: list[str] = [
            self._item(f"Type: <b>{th.esc(label)}</b>"),
        ]
        if m.entity_id:
            content_lines.append(self._item(f"ID: <code>{th.esc(m.entity_id)}</code>"))
        if m.title:
            content_lines.append(self._item(f"Shortcode: <code>{th.esc(m.title)}</code>"))
        if m.created_at:
            content_lines.append(
                self._item(f"Created: <b>{m.created_at.strftime('%d.%m.%Y %H:%M')} UTC</b>")
            )
        if m.description:
            content_lines.append(self._item(f"Description: <i>{th.esc(m.description[:400])}</i>"))
        elif m.biography:
            content_lines.append(self._item(f"Bio: <i>{th.esc(m.biography[:400])}</i>"))
        if m.location:
            content_lines.append(self._item(f"Location: {th.esc(m.location)}"))
        if m.external_url:
            content_lines.append(
                self._item(f'<a href="{th.href(m.external_url)}">External link</a>')
            )
        parts.append(self._section("Video" if bundle.resolved_type == EntityType.PUBLICATION else "Content", content_lines))

        # ── Statistics (точные числа как re:TikTok) ──
        stat_lines: list[str] = []
        if m.view_count is not None:
            stat_lines.append(self._item(f"👁 <b>{m.view_count:,}</b> views"))
        if m.like_count is not None:
            stat_lines.append(self._item(f"❤️ <b>{m.like_count:,}</b> likes"))
        if m.comment_count is not None:
            stat_lines.append(self._item(f"💬 <b>{m.comment_count:,}</b> comments"))
        if m.publication_count is not None:
            stat_lines.append(self._item(f"📷 <b>{m.publication_count:,}</b> posts"))
        for k, v in (bundle.collection_stats or {}).items():
            stat_lines.append(self._item(f"{th.esc(k)}: <b>{v}</b>", level=1))
        if stat_lines:
            parts.append(self._section("Statistics", stat_lines))

        # ── Quality ──
        for asset in bundle.media:
            if asset.media_type == "video":
                ql = self._quality_lines(asset)
                if ql:
                    parts.append(self._section("Quality", ql))
                break

        # ── Tags & mentions ──
        tag_lines: list[str] = []
        if m.tags:
            tag_lines.append(self._item(" ".join(th.esc(t) for t in m.tags[:15])))
        for u in (owner_extra.get("mentions") or [])[:8]:
            tag_lines.append(self._item(self._profile_link(u), level=1))
        if tag_lines:
            parts.append(self._section("Tags", tag_lines))

        # ── Media links ──
        if bundle.media:
            media_lines: list[str] = []
            for i, asset in enumerate(bundle.media[:8], 1):
                icon = "🎬" if asset.media_type == "video" else "🖼"
                media_lines.append(
                    self._item(
                        f'{icon} <a href="{th.href(asset.url)}">Download #{i}</a>',
                        level=1,
                    )
                )
            if len(bundle.media) > 8:
                media_lines.append(
                    self._item(f"+{len(bundle.media) - 8} more in JSON", level=1)
                )
            parts.append(self._section("Media", [self._item(f"Files: <b>{len(bundle.media)}</b>")] + media_lines))

        # ── Relations ──
        if bundle.relations:
            rel_lines: list[str] = []
            for rel in bundle.relations[:8]:
                label_txt = rel.target_label
                if re.fullmatch(r"[A-Za-z0-9_.]+", label_txt):
                    label_html = self._profile_link(label_txt, f"@{label_txt}")
                else:
                    label_html = th.esc(label_txt)
                rel_lines.append(
                    self._item(
                        f"<code>{th.esc(rel.relation_type)}</code> {label_html}",
                        level=1,
                    )
                )
            if len(bundle.relations) > 8:
                rel_lines.append(self._item(f"+{len(bundle.relations) - 8}", level=1))
            parts.append(self._section("Relations", rel_lines))

        # ── Activity ──
        comments = [a for a in bundle.activity if a.activity_type == "comment"]
        likes = [a for a in bundle.activity if a.activity_type == "like"]
        if comments or likes:
            act_lines: list[str] = []
            if comments:
                act_lines.append(self._item(f"Comments: <b>{len(comments)}</b>"))
                for act in comments[:5]:
                    act_lines.append(
                        self._item(
                            f"{self._actor_link(act.actor)}: "
                            f"<i>{th.esc((act.content or '')[:80])}</i>",
                            level=1,
                        )
                    )
            if likes:
                act_lines.append(self._item(f"Likes: <b>{len(likes)}</b>"))
                likers = " · ".join(
                    self._actor_link(a.actor) for a in likes[:6] if a.actor
                )
                if likers:
                    act_lines.append(self._item(likers, level=1))
            parts.append(self._section("Activity", act_lines))

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
            caption=f"⚡ {self.BRAND} · JSON dump",
            parse_mode="HTML",
        )

    async def send_error(self, message: Message, error: str) -> None:
        await message.answer(
            f"⚡ <b>{self.BRAND}</b>\n\n"
            f"{_BULLET} <b>Error</b>\n"
            f"{_NEST1} {th.esc(error)}",
            parse_mode="HTML",
        )

    async def send_processing(self, message: Message, url: str) -> Message:
        return await message.answer(
            f"⚡ <b>{self.BRAND}</b> Checker\n\n"
            f"{_BULLET} <b>Analyzing…</b>\n"
            f"{_NEST1} <code>{th.esc(url)}</code>",
            parse_mode="HTML",
        )