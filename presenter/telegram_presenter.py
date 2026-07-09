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
from core.platforms import Platform
from core.tiktok.cdn_urls import sort_download_urls
from core.tiktok.hq_meta import build_hq_downloads as build_tiktok_hq_downloads
from core.profile_adapter import (
    extract_avatar_from_profile_payload,
    extract_avatar_url,
)
from utils import telegram_html as th
from utils.image_utils import to_square_jpeg

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from config import Settings
    from core.fetcher import GraphQLFetcher

logger = logging.getLogger(__name__)

TG_MAX_LENGTH = 4096
TG_CAPTION_MAX = 1024

_SEP = "───────────────"


class TelegramPresenter:
    """Минималистичное оформление отчётов для Telegram."""

    BRAND = "ContentExplorer"
    VERSION = "1.4.1"

    PUB_MODES = {
        "prof": "Профиль автора",
        "aud": "Звук",
        "vid": "Видео полностью",
        "hq": "Макс. качество",
    }

    STAT_LABELS = {
        "posts_collected": "Постов собрано",
        "reels_collected": "Reels собрано",
        "tagged_collected": "Отметок собрано",
        "stories_collected": "Сторис собрано",
        "highlights_collected": "Актуального",
        "highlight_items": "Элементов актуального",
        "related_profiles": "Похожих аккаунтов",
        "media_files": "Медиафайлов",
        "comments_sampled": "Комментариев (топ-посты)",
    }

    REL_LABELS = {
        "publication": "пост",
        "reel": "reel",
        "tagged_in": "отметка",
        "highlight": "актуальное",
        "related_profile": "похожий",
        "story": "сторис",
    }

    TYPE_LABEL = {
        EntityType.PROFILE: "Профиль",
        EntityType.PUBLICATION: "Публикация",
        EntityType.STORY: "Сторис",
        EntityType.HIGHLIGHT: "Актуальное",
        EntityType.COLLECTION: "Коллекция",
        EntityType.UNKNOWN: "Контент",
    }

    def __init__(
        self,
        settings: Settings,
        fetcher: GraphQLFetcher | None = None,
        tiktok_fetcher: object | None = None,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher
        self.tiktok_fetcher = tiktok_fetcher
        self._ig_base = settings.platform_base_url.rstrip("/")
        self._tt_base = settings.tiktok_base_url.rstrip("/")

    def _bundle_platform(self, bundle: ArchiveBundle) -> Platform:
        if bundle.metadata.raw_fields.get("platform") == "tiktok":
            return Platform.TIKTOK
        if "tiktok.com" in (bundle.source_url or "").lower():
            return Platform.TIKTOK
        return Platform.INSTAGRAM

    def _base_url(self, bundle: ArchiveBundle) -> str:
        return (
            self._tt_base
            if self._bundle_platform(bundle) == Platform.TIKTOK
            else self._ig_base
        )

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

    def _profile_url(
        self, username: str, *, platform: Platform = Platform.INSTAGRAM
    ) -> str:
        base = self._tt_base if platform == Platform.TIKTOK else self._ig_base
        user = username.strip("/").lstrip("@")
        if platform == Platform.TIKTOK:
            return f"{base}/@{user}"
        return f"{base}/{user}/"

    def _profile_link(
        self,
        username: str | None,
        label: str | None = None,
        *,
        platform: Platform = Platform.INSTAGRAM,
    ) -> str:
        if not username:
            return "—"
        text = label or f"@{username}"
        return (
            f'<a href="{th.href(self._profile_url(username, platform=platform))}">'
            f"{th.esc(text)}</a>"
        )

    def _actor_link(
        self, username: str | None, *, platform: Platform = Platform.INSTAGRAM
    ) -> str:
        if not username:
            return "—"
        return self._profile_link(username, f"@{username}", platform=platform)

    @staticmethod
    def _format_bitrate(bps: int | float | None) -> str:
        if not bps:
            return "—"
        mbps = bps / 1_000_000
        return f"{mbps:.2f} Мбит/с" if mbps >= 1 else f"{bps / 1000:.0f} Кбит/с"

    def _build_keyboard(self, bundle: ArchiveBundle) -> InlineKeyboardMarkup | None:
        buttons: list[InlineKeyboardButton] = []
        m = bundle.metadata
        platform = self._bundle_platform(bundle)

        if m.username:
            label = (
                "Профиль"
                if bundle.resolved_type == EntityType.PROFILE
                else "Автор"
            )
            buttons.append(
                InlineKeyboardButton(
                    text=label,
                    url=self._profile_url(m.username, platform=platform),
                )
            )
        if bundle.source_url and bundle.resolved_type != EntityType.PROFILE:
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

    @staticmethod
    def _normalize_media_url(url: str | None) -> str | None:
        if not url:
            return None
        u = url.strip()
        if u.startswith("//"):
            return f"https:{u}"
        if u.startswith("http"):
            return u
        return None

    def _profile_avatar_asset(self, bundle: ArchiveBundle) -> MediaAsset | None:
        m = bundle.metadata
        extra = m.raw_fields or {}
        url = self._normalize_media_url(
            extract_avatar_url({
                "profile_pic_url_hd": m.avatar_url,
                "profile_pic_url": extra.get("profile_pic_url"),
                "hd_profile_pic_url_info": extra.get(
                    "hd_profile_pic_url_info"
                ),
            })
            or m.avatar_url
        )
        if not url:
            for raw in bundle.raw_graphql or []:
                url = self._normalize_media_url(
                    extract_avatar_from_profile_payload(raw)
                )
                if url:
                    break
        if not url:
            for asset in bundle.media:
                if asset.extra.get("source") == "avatar":
                    url = self._normalize_media_url(asset.url)
                    if url:
                        break
        if not url:
            logger.warning(
                "Аватар не найден для @%s",
                bundle.metadata.username,
            )
            return None
        return MediaAsset(
            id="avatar",
            media_type="image",
            url=url,
            extra={"source": "avatar"},
        )

    def _pick_preview_media(self, bundle: ArchiveBundle) -> MediaAsset | None:
        if bundle.resolved_type == EntityType.PROFILE:
            return self._profile_avatar_asset(bundle)

        valid = [
            m for m in bundle.media
            if self._normalize_media_url(m.url)
        ]
        if not valid:
            return None

        for asset in valid:
            if asset.media_type == "video":
                asset.url = self._normalize_media_url(asset.url) or asset.url
                return asset
        first = valid[0]
        first.url = self._normalize_media_url(first.url) or first.url
        return first

    @staticmethod
    def _iter_tiktok_video_urls(
        asset: MediaAsset,
        *,
        prefer_hd: bool = False,
    ) -> list[str]:
        extra = asset.extra
        seen: set[str] = set()
        urls: list[str] = []

        def add(url: str | None) -> None:
            if url and url.startswith("http") and url not in seen:
                seen.add(url)
                urls.append(url)

        if prefer_hd:
            flat_keys = ("hdplay", "play", "wmplay", "hq_best_url", "video_url_best")
        else:
            flat_keys = ("play", "wmplay", "hdplay", "hq_best_url", "video_url_best")

        for key in flat_keys:
            val = extra.get(key)
            if isinstance(val, str):
                add(val)

        entries = list(extra.get("hq_downloads") or [])
        if not prefer_hd:
            for entry in entries:
                if entry.get("source") in ("play", "watermark") and entry.get("url"):
                    add(entry["url"])
        for entry in entries:
            add(entry.get("url"))

        add(asset.url)
        return sort_download_urls(urls)

    async def _refresh_tiktok_asset_urls(
        self,
        bundle: ArchiveBundle,
        asset: MediaAsset,
    ) -> bool:
        if not self.tiktok_fetcher:
            return False
        video_id = bundle.metadata.entity_id or asset.id
        username = bundle.metadata.username
        try:
            item = await self.tiktok_fetcher.refresh_mirror_item(
                bundle.source_url,
                video_id=video_id,
                username=username,
            )
        except Exception as exc:
            logger.warning("TT mirror refresh: %s", exc)
            return False

        hq = build_tiktok_hq_downloads(item)
        asset.extra.update(hq)
        for key in ("play", "hdplay", "wmplay", "cover"):
            val = item.get(key)
            if isinstance(val, str) and val.startswith("http"):
                asset.extra[key] = val
        best = hq.get("hq_best_url")
        if best:
            asset.url = best
        return True

    async def _download_tiktok_video_bytes(
        self,
        bundle: ArchiveBundle,
        asset: MediaAsset,
        *,
        label: str = "preview_video",
        max_bytes: int = 48 * 1024 * 1024,
        prefer_hd: bool = False,
    ) -> bytes | None:
        if not self.tiktok_fetcher:
            return None
        referer = bundle.source_url or self._base_url(bundle)

        for attempt in range(2):
            urls = self._iter_tiktok_video_urls(asset, prefer_hd=prefer_hd)
            if not urls:
                if attempt == 0 and await self._refresh_tiktok_asset_urls(
                    bundle, asset
                ):
                    continue
                return None
            try:
                data, _, _ = await self.tiktok_fetcher.download_from_urls(
                    urls,
                    referer=referer,
                    label=label,
                    max_bytes=max_bytes,
                )
                return data
            except ValueError as exc:
                logger.warning("%s: %s", label, exc)
                return None
            except Exception as exc:
                logger.warning("%s download failed: %s", label, exc)
                if attempt == 0 and await self._refresh_tiktok_asset_urls(
                    bundle, asset
                ):
                    continue
        return None

    async def _download_video_bytes(
        self,
        bundle: ArchiveBundle,
        asset: MediaAsset,
        *,
        label: str = "preview_video",
        max_bytes: int = 48 * 1024 * 1024,
        prefer_hd: bool = False,
    ) -> bytes | None:
        platform = self._bundle_platform(bundle)
        if platform == Platform.TIKTOK:
            return await self._download_tiktok_video_bytes(
                bundle,
                asset,
                label=label,
                max_bytes=max_bytes,
                prefer_hd=prefer_hd,
            )
        if not asset.url or not asset.url.startswith("http"):
            return None
        referer = bundle.source_url or self._base_url(bundle)
        try:
            if self.fetcher:
                return await self.fetcher.download_media_bytes(
                    asset.url,
                    referer=referer,
                    label=label,
                    max_bytes=max_bytes,
                )[0]
        except Exception as exc:
            logger.warning("%s download failed: %s", label, exc)
        return None

    async def _download_image_bytes(
        self,
        bundle: ArchiveBundle,
        url: str,
        *,
        label: str = "preview_image",
    ) -> bytes | None:
        if not url or not url.startswith("http"):
            return None
        referer = bundle.source_url or self._base_url(bundle)
        platform = self._bundle_platform(bundle)
        try:
            if platform == Platform.TIKTOK and self.tiktok_fetcher:
                return await self.tiktok_fetcher.download_image_bytes(
                    url, referer=referer, label=label
                )
            if self.fetcher:
                return await self.fetcher.download_image_bytes(
                    url, referer=referer, label=label
                )
        except Exception as exc:
            logger.warning("%s download failed: %s", label, exc)
        return None

    async def _send_tiktok_preview(
        self,
        message: Message,
        bundle: ArchiveBundle,
        preview: MediaAsset,
        *,
        caption: str | None,
        keyboard: InlineKeyboardMarkup | None,
        report_fallback: str | None,
    ) -> bool:
        """TikTok CDN не отдаёт Telegram — скачиваем и шлём файлом."""
        cap = th.truncate_html(caption, TG_CAPTION_MAX) if caption else None
        cover_url = preview.thumbnail_url or preview.url

        if preview.media_type == "video":
            video_bytes = await self._download_video_bytes(
                bundle, preview, label="hub_video"
            )
            if video_bytes:
                video_file = BufferedInputFile(
                    video_bytes, filename="tiktok_preview.mp4"
                )
                duration = (
                    int(preview.duration_sec)
                    if preview.duration_sec
                    else None
                )
                caps = [cap] if cap else []
                if report_fallback:
                    caps.extend(
                        th.truncate_html(report_fallback, limit)
                        for limit in (TG_CAPTION_MAX, 900, 500)
                        if limit != TG_CAPTION_MAX or not cap
                    )
                seen_caps: set[str] = set()
                for attempt_cap in caps:
                    if not attempt_cap or attempt_cap in seen_caps:
                        continue
                    seen_caps.add(attempt_cap)
                    try:
                        await message.answer_video(
                            video=video_file,
                            caption=attempt_cap,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                            duration=duration,
                            width=preview.width,
                            height=preview.height,
                        )
                        return True
                    except TelegramBadRequest as exc:
                        logger.warning("TT video send: %s", exc)
                try:
                    await message.answer_document(
                        video_file,
                        caption=cap,
                        parse_mode="HTML" if cap else None,
                        reply_markup=keyboard,
                    )
                    return True
                except TelegramBadRequest as exc:
                    logger.warning("TT video document: %s", exc)

        if cover_url and cover_url.startswith("http"):
            photo_bytes = await self._download_image_bytes(
                bundle, cover_url, label="hub_cover"
            )
            if photo_bytes:
                photo_file = BufferedInputFile(
                    photo_bytes, filename="tiktok_cover.jpg"
                )
                try:
                    await message.answer_photo(
                        photo=photo_file,
                        caption=cap,
                        parse_mode="HTML" if cap else None,
                        reply_markup=keyboard,
                    )
                    return True
                except TelegramBadRequest as exc:
                    logger.warning("TT cover send: %s", exc)

        if report_fallback:
            await self._send_html(message, report_fallback, keyboard)
        elif cap:
            await self._send_html(message, caption or "", keyboard)
        return bool(report_fallback or cap)

    async def _send_preview_message(
        self,
        message: Message,
        bundle: ArchiveBundle,
        preview: MediaAsset,
        *,
        caption: str | None = None,
        keyboard: InlineKeyboardMarkup | None = None,
        report_fallback: str | None = None,
    ) -> bool:
        platform = self._bundle_platform(bundle)
        if platform == Platform.TIKTOK:
            return await self._send_tiktok_preview(
                message,
                bundle,
                preview,
                caption=caption,
                keyboard=keyboard,
                report_fallback=report_fallback,
            )

        use_photo = preview.media_type != "video"
        cap = th.truncate_html(caption, TG_CAPTION_MAX) if caption else None
        try:
            if use_photo:
                await message.answer_photo(
                    photo=preview.url,
                    caption=cap,
                    parse_mode="HTML" if cap else None,
                    reply_markup=keyboard,
                )
            else:
                await message.answer_video(
                    video=preview.url,
                    caption=cap,
                    parse_mode="HTML" if cap else None,
                    reply_markup=keyboard,
                )
            return True
        except TelegramBadRequest as exc:
            logger.warning("Hub media error: %s", exc)
            try:
                if use_photo:
                    await message.answer_photo(
                        preview.url, reply_markup=keyboard
                    )
                else:
                    await message.answer_video(
                        preview.url, reply_markup=keyboard
                    )
                if report_fallback:
                    await self._send_html(message, report_fallback, keyboard)
                elif cap:
                    await self._send_html(message, caption or "", keyboard)
                return True
            except Exception:
                return False

    def _post_url(self, shortcode: str) -> str:
        return f"{self._ig_base}/p/{shortcode}/"

    def _reel_url(self, shortcode: str) -> str:
        return f"{self._ig_base}/reel/{shortcode}/"

    def _format_profile_report(self, bundle: ArchiveBundle) -> str:
        m = bundle.metadata
        extra = m.raw_fields or {}
        parts: list[str] = [self._header(bundle)]

        # ── Профиль ──
        profile_lines: list[str] = []
        if m.username:
            profile_lines.append(
                self._kv("Ник", self._profile_link(m.username))
            )
        if m.display_name:
            profile_lines.append(
                self._kv(
                    "Имя",
                    self._profile_link(m.username, m.display_name)
                    if m.username
                    else th.esc(m.display_name),
                )
            )
        if m.entity_id:
            profile_lines.append(
                self._kv("ID", f"<code>{th.esc(m.entity_id)}</code>")
            )
        flags: list[str] = []
        if m.is_verified:
            flags.append("верифицирован")
        if m.is_private:
            flags.append("приватный")
        if extra.get("is_business"):
            flags.append("бизнес")
        if extra.get("is_professional"):
            flags.append("профи")
        if extra.get("is_joined_recently"):
            flags.append("новый аккаунт")
        if flags:
            profile_lines.append(
                self._kv("Статус", th.esc(", ".join(flags)))
            )
        if extra.get("category"):
            profile_lines.append(
                self._kv("Категория", th.esc(str(extra["category"])))
            )
        pronouns = extra.get("pronouns")
        if pronouns:
            text = (
                ", ".join(pronouns)
                if isinstance(pronouns, list)
                else str(pronouns)
            )
            profile_lines.append(self._kv("Местоимения", th.esc(text)))
        if extra.get("business_email"):
            profile_lines.append(
                self._kv("Email", th.esc(extra["business_email"]))
            )
        if extra.get("business_phone"):
            profile_lines.append(
                self._kv("Телефон", th.esc(extra["business_phone"]))
            )
        if m.external_url:
            profile_lines.append(
                self._kv(
                    "Сайт",
                    f'<a href="{th.href(m.external_url)}">{th.esc(m.external_url[:50])}</a>',
                )
            )
        parts.append(self._section("Профиль"))
        parts.extend(line + "\n" for line in profile_lines)

        # ── О себе (цитаты) ──
        quotes: list[str] = []
        if m.biography:
            quotes.append(self._quote(m.biography, max_len=700))
        for link in (extra.get("bio_links") or [])[:5]:
            if isinstance(link, dict):
                url = link.get("url")
                title = link.get("title") or url
                if url:
                    quotes.append(
                        self._quote(
                            f"{title}: {url}" if title != url else url,
                            max_len=200,
                        )
                    )
            elif isinstance(link, str):
                quotes.append(self._quote(link, max_len=200))
        if quotes:
            parts.append(self._section("О себе"))
            parts.extend(quotes)

        # ── Статистика ──
        stat_lines: list[str] = []
        if m.follower_count is not None:
            stat_lines.append(
                self._kv("Подписчики", f"<b>{m.follower_count:,}</b>")
            )
        if m.following_count is not None:
            stat_lines.append(
                self._kv("Подписки", f"<b>{m.following_count:,}</b>")
            )
        if m.publication_count is not None:
            stat_lines.append(
                self._kv("Публикации", f"<b>{m.publication_count:,}</b>")
            )
        if extra.get("reels_total") is not None:
            stat_lines.append(
                self._kv("Reels", f"<b>{extra['reels_total']:,}</b>")
            )
        if extra.get("tagged_total") is not None:
            stat_lines.append(
                self._kv("Отметки", f"<b>{extra['tagged_total']:,}</b>")
            )
        if extra.get("highlight_reel_count") is not None:
            stat_lines.append(
                self._kv(
                    "Актуальное",
                    f"<b>{extra['highlight_reel_count']}</b>",
                )
            )
        if extra.get("aggregate_likes"):
            stat_lines.append(
                self._kv(
                    "Сумма лайков",
                    f"<b>{extra['aggregate_likes']:,}</b>",
                )
            )
        if extra.get("aggregate_views"):
            stat_lines.append(
                self._kv(
                    "Сумма просмотров",
                    f"<b>{extra['aggregate_views']:,}</b>",
                )
            )
        if stat_lines:
            parts.append(self._section("Статистика"))
            parts.extend(line + "\n" for line in stat_lines)

        # ── Сбор данных ──
        if bundle.collection_stats:
            coll_lines: list[str] = []
            for k, v in bundle.collection_stats.items():
                label = self.STAT_LABELS.get(k, k)
                coll_lines.append(self._kv(label, f"<b>{v}</b>"))
            parts.append(self._section("Сбор данных"))
            parts.extend(line + "\n" for line in coll_lines)

        # ── Сторис ──
        stories = [
            r for r in bundle.relations if r.relation_type == "story"
        ]
        if stories:
            parts.append(self._section("Сторис"))
            parts.append(
                self._kv("Активных", f"<b>{len(stories)}</b>") + "\n"
            )

        # ── Актуальное ──
        highlights = [
            r for r in bundle.relations if r.relation_type == "highlight"
        ]
        if highlights:
            hl_lines: list[str] = [
                self._kv("Собрано", f"<b>{len(highlights)}</b>")
            ]
            for hl in highlights[:6]:
                title = hl.target_label or hl.target_id
                count = (hl.metadata or {}).get("items_count")
                suffix = f" · {count} шт." if count else ""
                hl_lines.append(f"  {th.esc(title)}{suffix}")
            if len(highlights) > 6:
                hl_lines.append(f"  +{len(highlights) - 6} ещё")
            parts.append(self._section("Актуальное"))
            parts.extend(line + "\n" for line in hl_lines)

        # ── Топ посты ──
        posts = sorted(
            [r for r in bundle.relations if r.relation_type == "publication"],
            key=lambda r: (r.metadata or {}).get("likes", 0),
            reverse=True,
        )
        if posts:
            top_lines: list[str] = []
            for rel in posts[:6]:
                sc = rel.target_label
                meta = rel.metadata or {}
                likes = meta.get("likes", 0)
                comments = meta.get("comments", 0)
                cap = meta.get("caption", "")
                link = (
                    f'<a href="{th.href(self._post_url(sc))}">{th.esc(sc)}</a>'
                    if sc
                    else "—"
                )
                top_lines.append(
                    f"  {link} · ❤ {likes:,} · 💬 {comments:,}"
                )
                if cap:
                    top_lines.append(
                        f"  <i>{th.esc(cap[:80])}</i>"
                    )
            if len(posts) > 6:
                top_lines.append(f"  +{len(posts) - 6} постов в JSON")
            parts.append(self._section("Топ посты"))
            parts.extend(line + "\n" for line in top_lines)

        # ── Reels ──
        reels = sorted(
            [r for r in bundle.relations if r.relation_type == "reel"],
            key=lambda r: (r.metadata or {}).get("views", 0),
            reverse=True,
        )
        if reels:
            reel_lines: list[str] = [
                self._kv("Собрано", f"<b>{len(reels)}</b>")
            ]
            for rel in reels[:5]:
                sc = rel.target_label
                meta = rel.metadata or {}
                views = meta.get("views", 0)
                likes = meta.get("likes", 0)
                link = (
                    f'<a href="{th.href(self._reel_url(sc))}">{th.esc(sc)}</a>'
                    if sc
                    else "—"
                )
                reel_lines.append(
                    f"  {link} · 👁 {views:,} · ❤ {likes:,}"
                )
            if len(reels) > 5:
                reel_lines.append(f"  +{len(reels) - 5} reels в JSON")
            parts.append(self._section("Reels"))
            parts.extend(line + "\n" for line in reel_lines)

        # ── Отметки ──
        tagged = [
            r for r in bundle.relations if r.relation_type == "tagged_in"
        ]
        if tagged:
            tag_lines: list[str] = [
                self._kv("Собрано", f"<b>{len(tagged)}</b>")
            ]
            for rel in tagged[:5]:
                owner = (rel.metadata or {}).get("owner")
                sc = rel.target_label
                post_link = (
                    f'<a href="{th.href(self._post_url(sc))}">{th.esc(sc)}</a>'
                    if sc
                    else "—"
                )
                owner_link = self._profile_link(owner) if owner else "—"
                tag_lines.append(f"  {post_link} · {owner_link}")
            if len(tagged) > 5:
                tag_lines.append(f"  +{len(tagged) - 5} в JSON")
            parts.append(self._section("Отметки"))
            parts.extend(line + "\n" for line in tag_lines)

        # ── Похожие аккаунты ──
        related = [
            r
            for r in bundle.relations
            if r.relation_type == "related_profile"
        ]
        if related:
            rel_lines: list[str] = []
            for rel in related[:8]:
                verified = " ✓" if (rel.metadata or {}).get("is_verified") else ""
                rel_lines.append(
                    f"  {self._profile_link(rel.target_label)}{verified}"
                )
            parts.append(self._section("Похожие"))
            parts.extend(line + "\n" for line in rel_lines)

        # ── Комментарии (топ-посты) ──
        comments = [
            a for a in bundle.activity if a.activity_type == "comment"
        ]
        if comments:
            comment_lines: list[str] = []
            for act in comments[:10]:
                actor = self._actor_link(act.actor)
                post = (act.extra or {}).get("post_shortcode", "")
                text = (act.content or "").strip()
                prefix = f"[{post}] " if post else ""
                if text:
                    comment_lines.append(
                        f"{actor}: {th.esc(prefix + text[:100])}"
                    )
            if comment_lines:
                parts.append(self._section("Комментарии"))
                parts.append(
                    f"<blockquote>{chr(10).join(comment_lines)}</blockquote>\n"
                )

        # ── Медиа ──
        downloadable = [
            a for a in bundle.media
            if a.url and a.url.startswith("http")
            and a.extra.get("source") != "avatar"
        ]
        if downloadable:
            media_lines: list[str] = [
                self._kv("Файлов", f"<b>{len(downloadable)}</b>")
            ]
            for i, asset in enumerate(downloadable[:8], 1):
                src = asset.extra.get("source", asset.media_type)
                kind = "видео" if asset.media_type == "video" else "фото"
                media_lines.append(
                    f'  <a href="{th.href(asset.url)}">#{i}</a> · {kind} · {th.esc(str(src))}'
                )
            if len(downloadable) > 8:
                media_lines.append(
                    f"  +{len(downloadable) - 8} в JSON"
                )
            parts.append(self._section("Медиа"))
            parts.extend(line + "\n" for line in media_lines)

        parts.append(self._footer())
        return th.truncate_html("".join(parts), TG_MAX_LENGTH)

    def format_full_report(self, bundle: ArchiveBundle) -> str:
        if bundle.resolved_type == EntityType.PROFILE:
            return self._format_profile_report(bundle)
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

    def _publication_entity_token(
        self,
        entity_id: str,
        *,
        platform: Platform = Platform.INSTAGRAM,
        username: str | None = None,
    ) -> str:
        if platform == Platform.TIKTOK and username:
            return f"{entity_id}:{username.lstrip('@')}"[:57]
        return entity_id[:57]

    def build_publication_hub_keyboard(
        self,
        entity_id: str,
        *,
        platform: Platform = Platform.INSTAGRAM,
        username: str | None = None,
    ) -> InlineKeyboardMarkup:
        eid = self._publication_entity_token(
            entity_id, platform=platform, username=username
        )
        prefix = "t" if platform == Platform.TIKTOK else "p"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Профиль",
                        callback_data=f"{prefix}:prof:{eid}",
                    ),
                    InlineKeyboardButton(
                        text="Звук",
                        callback_data=f"{prefix}:aud:{eid}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Видео полностью",
                        callback_data=f"{prefix}:vid:{eid}",
                    ),
                    InlineKeyboardButton(
                        text="HD загрузка",
                        callback_data=f"{prefix}:hq:{eid}",
                    ),
                ],
            ]
        )

    def build_publication_actions_keyboard(
        self,
        entity_id: str,
        *,
        platform: Platform = Platform.INSTAGRAM,
        username: str | None = None,
    ) -> InlineKeyboardMarkup:
        """Кнопки после «Видео полностью»: профиль, звук, HD."""
        eid = self._publication_entity_token(
            entity_id, platform=platform, username=username
        )
        prefix = "t" if platform == Platform.TIKTOK else "p"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Профиль",
                        callback_data=f"{prefix}:prof:{eid}",
                    ),
                    InlineKeyboardButton(
                        text="Звук",
                        callback_data=f"{prefix}:aud:{eid}",
                    ),
                    InlineKeyboardButton(
                        text="HD загрузка",
                        callback_data=f"{prefix}:hq:{eid}",
                    ),
                ],
            ]
        )

    def format_publication_hub(self, bundle: ArchiveBundle) -> str:
        m = bundle.metadata
        platform = self._bundle_platform(bundle)
        parts: list[str] = [
            f"<b>{self.BRAND}</b>\n",
            f"{th.esc(self.TYPE_LABEL[EntityType.PUBLICATION])}\n",
        ]

        if m.username:
            parts.append(
                self._kv(
                    "Автор",
                    self._profile_link(m.username, platform=platform),
                )
                + "\n"
            )

        stat_bits: list[str] = []
        if m.view_count is not None:
            stat_bits.append(f"👁 {m.view_count:,}")
        if m.like_count is not None:
            stat_bits.append(f"❤ {m.like_count:,}")
        if m.comment_count is not None:
            stat_bits.append(f"💬 {m.comment_count:,}")
        if stat_bits:
            parts.append("\n" + " · ".join(stat_bits) + "\n")

        if m.description:
            parts.append(self._section("Описание"))
            parts.append(self._quote(m.description, max_len=700))

        has_video = any(a.media_type == "video" for a in bundle.media)
        hint = (
            "Выберите, что проверить подробнее:"
            if has_video
            else "Выберите раздел для детальной проверки:"
        )
        parts.append(f"\n<i>{th.esc(hint)}</i>")
        parts.append(self._footer())
        return th.truncate_html("".join(parts), TG_CAPTION_MAX)

    def format_deep_profile(self, bundle: ArchiveBundle) -> str:
        m = bundle.metadata
        extra = m.raw_fields or {}
        parts: list[str] = [
            f"<b>{self.BRAND}</b>\n",
            f"<b>{th.esc(self.PUB_MODES['prof'])}</b>\n",
        ]

        profile_lines: list[str] = []
        if m.username:
            profile_lines.append(
                self._kv("Ник", self._profile_link(m.username))
            )
        if m.display_name:
            profile_lines.append(
                self._kv(
                    "Имя",
                    self._profile_link(m.username, m.display_name)
                    if m.username
                    else th.esc(m.display_name),
                )
            )
        flags: list[str] = []
        if m.is_verified:
            flags.append("верифицирован")
        if m.is_private:
            flags.append("приватный")
        if extra.get("owner_is_business"):
            flags.append("бизнес")
        if flags:
            profile_lines.append(
                self._kv("Статус", th.esc(", ".join(flags)))
            )
        if extra.get("owner_category"):
            profile_lines.append(
                self._kv("Категория", th.esc(str(extra["owner_category"])))
            )
        if m.external_url:
            profile_lines.append(
                self._kv(
                    "Сайт",
                    f'<a href="{th.href(m.external_url)}">'
                    f"{th.esc(m.external_url[:50])}</a>",
                )
            )
        parts.append(self._section("Автор публикации"))
        parts.extend(line + "\n" for line in profile_lines)

        bio = m.biography or extra.get("owner_bio")
        if bio:
            parts.append(self._section("О себе"))
            parts.append(self._quote(bio, max_len=600))

        for link in (extra.get("owner_bio_links") or [])[:4]:
            if isinstance(link, dict):
                url = link.get("url")
                title = link.get("title") or url
                if url:
                    parts.append(self._quote(f"{title}: {url}", max_len=200))

        stat_lines: list[str] = []
        if m.follower_count is not None:
            stat_lines.append(
                self._kv("Подписчики", f"<b>{m.follower_count:,}</b>")
            )
        if m.following_count is not None:
            stat_lines.append(
                self._kv("Подписки", f"<b>{m.following_count:,}</b>")
            )
        if m.publication_count is not None:
            stat_lines.append(
                self._kv("Публикации", f"<b>{m.publication_count:,}</b>")
            )
        if stat_lines:
            parts.append(self._section("Статистика"))
            parts.extend(line + "\n" for line in stat_lines)

        parts.append(self._footer())
        return th.truncate_html("".join(parts), TG_MAX_LENGTH)

    def format_deep_audio(self, bundle: ArchiveBundle) -> str:
        parts: list[str] = [
            f"<b>{self.BRAND}</b>\n",
            f"<b>{th.esc(self.PUB_MODES['aud'])}</b>\n",
        ]

        video_asset = next(
            (a for a in bundle.media if a.media_type == "video"), None
        )
        if not video_asset:
            parts.append(
                self._section("Звук")
                + self._kv("Статус", "<b>нет видео в публикации</b>\n")
            )
            parts.append(self._footer())
            return th.truncate_html("".join(parts), TG_MAX_LENGTH)

        e = video_asset.extra
        audio_lines: list[str] = []

        if e.get("has_audio") is not None:
            audio_lines.append(
                self._kv(
                    "Звуковая дорожка",
                    f"<b>{'есть' if e['has_audio'] else 'нет'}</b>",
                )
            )
        if e.get("audio_codec"):
            audio_lines.append(
                self._kv(
                    "Аудиокодек",
                    f"<code>{th.esc(e['audio_codec'])}</code>",
                )
            )
        if video_asset.duration_sec:
            audio_lines.append(
                self._kv(
                    "Длительность",
                    f"<b>{video_asset.duration_sec:.1f} с</b>",
                )
            )

        music = e.get("music") or {}
        if music.get("title") or music.get("artist"):
            parts.append(self._section("Музыка"))
            if music.get("artist"):
                parts.append(
                    self._kv("Исполнитель", th.esc(music["artist"])) + "\n"
                )
            if music.get("title"):
                parts.append(
                    self._kv("Трек", th.esc(music["title"])) + "\n"
                )
            if music.get("duration_ms"):
                sec = music["duration_ms"] / 1000
                parts.append(
                    self._kv("Длина трека", f"<b>{sec:.1f} с</b>") + "\n"
                )
            music_text = " — ".join(
                p for p in (music.get("artist"), music.get("title")) if p
            )
            if music_text:
                parts.append(self._quote(f"♫ {music_text}", max_len=200))

        if e.get("accessibility_caption"):
            parts.append(self._section("Озвучка / субтитры"))
            parts.append(
                self._quote(e["accessibility_caption"], max_len=400)
            )

        if e.get("video_subtitles_uri"):
            parts.append(self._section("Субтитры"))
            parts.append(
                self._kv(
                    "Файл",
                    f'<a href="{th.href(e["video_subtitles_uri"])}">скачать</a>',
                )
                + "\n"
            )

        source = e.get("audio_source")
        if source:
            parts.append(
                self._kv("Источник", f"<code>{th.esc(str(source))}</code>")
                + "\n"
            )
        fmt = e.get("audio_format")
        if fmt:
            parts.append(
                self._kv("Формат", f"<b>{th.esc(str(fmt).upper())}</b>") + "\n"
            )

        if audio_lines:
            parts.append(self._section("Технические данные"))
            parts.extend(line + "\n" for line in audio_lines)

        if not music and not audio_lines:
            parts.append(
                self._section("Звук")
                + self._kv("Данные", "<b>не найдены</b>\n")
            )

        parts.append(self._footer())
        return th.truncate_html("".join(parts), TG_MAX_LENGTH)

    def _format_hq_entry_line(self, entry: dict) -> str:
        parts: list[str] = []
        w, h = entry.get("width"), entry.get("height")
        if w and h:
            parts.append(f"{w}×{h}")
        if entry.get("fps"):
            parts.append(f"{entry['fps']} fps")
        if entry.get("codec"):
            parts.append(str(entry["codec"])[:20])
        if entry.get("bandwidth_bps"):
            parts.append(self._format_bitrate(entry["bandwidth_bps"]))
        src = entry.get("source")
        if src:
            parts.append(th.esc(str(src)))
        return " · ".join(parts) if parts else "вариант"

    def format_deep_hq(
        self,
        bundle: ArchiveBundle,
        *,
        delivered: dict | None = None,
        notice: str | None = None,
    ) -> str:
        parts: list[str] = [
            f"<b>{self.BRAND}</b>\n",
            f"<b>{th.esc(self.PUB_MODES['hq'])}</b>\n",
        ]

        if notice:
            parts.append(
                f"<blockquote>{th.esc(notice)}</blockquote>\n"
            )

        media_list = [
            a
            for a in bundle.media
            if a.url and a.url.startswith("http")
        ]
        if not media_list:
            parts.append(
                self._section("Загрузка")
                + self._kv("Статус", "<b>ссылки недоступны</b>\n")
            )
            parts.append(self._footer())
            return th.truncate_html("".join(parts), TG_MAX_LENGTH)

        for idx, asset in enumerate(media_list, 1):
            kind = "видео" if asset.media_type == "video" else "фото"
            parts.append(self._section(f"Файл #{idx} · {kind}"))

            e = asset.extra
            best = e.get("hq_best") or {}
            best_url = (
                e.get("hq_best_url")
                or e.get("video_url_best")
                or asset.url
            )
            res = e.get("resolution") or (
                f"{asset.width}×{asset.height}"
                if asset.width and asset.height
                else None
            )

            if res:
                parts.append(
                    self._kv("Максимум", f"<b>{th.esc(str(res))}</b>") + "\n"
                )
            if best.get("source"):
                parts.append(
                    self._kv(
                        "Источник",
                        f"<code>{th.esc(str(best['source']))}</code>",
                    )
                    + "\n"
                )
            if delivered and idx == 1:
                size = delivered.get("size_bytes")
                if size:
                    parts.append(
                        self._kv(
                            "Размер файла",
                            f"<b>{size / (1024 * 1024):.2f} МБ</b>",
                        )
                        + "\n"
                    )
            if asset.duration_sec:
                parts.append(
                    self._kv(
                        "Длительность",
                        f"<b>{asset.duration_sec:.1f} с</b>",
                    )
                    + "\n"
                )

            parts.append(
                self._kv(
                    "Скачать",
                    f'<a href="{th.href(best_url)}">лучшее качество</a>',
                )
                + "\n"
            )

            downloads = list(e.get("hq_downloads") or [])
            if not downloads:
                downloads = list(e.get("quality_variants") or [])

            if downloads:
                parts.append(
                    self._kv("Все варианты", f"<b>{len(downloads)}</b>") + "\n"
                )
                for entry in downloads[:10]:
                    url = entry.get("url")
                    if not url:
                        continue
                    label = self._format_hq_entry_line(entry)
                    parts.append(
                        f'  <a href="{th.href(url)}">{th.esc(label)}</a>\n'
                    )
                if len(downloads) > 10:
                    parts.append(f"  +{len(downloads) - 10} ещё\n")

            dash_reps = [
                r
                for r in (e.get("dash_representations") or [])
                if r.get("url")
            ]
            if dash_reps:
                parts.append(
                    self._kv("DASH URL", f"<b>{len(dash_reps)}</b>") + "\n"
                )
                for rep in dash_reps[:4]:
                    url = rep.get("url")
                    if not url:
                        continue
                    label = self._format_hq_entry_line(rep)
                    parts.append(
                        f'  <a href="{th.href(url)}">{th.esc(label)}</a>\n'
                    )

        parts.append(self._footer())
        return th.truncate_html("".join(parts), TG_MAX_LENGTH)

    def format_deep_report(
        self, bundle: ArchiveBundle, mode: str
    ) -> str:
        if mode == "prof":
            return self.format_deep_profile(bundle)
        if mode == "aud":
            return self.format_deep_audio(bundle)
        if mode == "vid":
            return self.format_full_report(bundle)
        if mode == "hq":
            return self.format_deep_hq(bundle)
        return self.format_full_report(bundle)

    async def send_publication_hub(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
        *,
        platform: Platform = Platform.INSTAGRAM,
    ) -> None:
        entity_id = bundle.metadata.title or bundle.metadata.entity_id or ""
        caption = self.format_publication_hub(bundle)
        keyboard = self.build_publication_hub_keyboard(
            entity_id,
            platform=platform,
            username=bundle.metadata.username,
        )
        preview = self._pick_preview_media(bundle)
        if preview:
            sent = await self._send_preview_message(
                message,
                bundle,
                preview,
                caption=caption,
                keyboard=keyboard,
            )
            if sent:
                return
        await self._send_html(message, caption, keyboard)

    async def send_audio_report(
        self,
        message: Message,
        bundle: ArchiveBundle,
        audio_bytes: bytes,
        filename: str,
    ) -> None:
        """Отправляет скачанный оригинальный аудиофайл."""
        video = next(
            (a for a in bundle.media if a.media_type == "video"), None
        )
        e = video.extra if video else {}
        music = e.get("music") or {}
        caption = self.format_deep_audio(bundle)
        safe_caption = th.truncate_html(caption, TG_CAPTION_MAX)

        title = (music.get("title") or "Audio")[:64]
        performer = (music.get("artist") or bundle.metadata.username or "")[
            :64
        ]
        duration = None
        if music.get("duration_ms"):
            duration = int(music["duration_ms"] / 1000)
        elif video and video.duration_sec:
            duration = int(video.duration_sec)

        audio_file = BufferedInputFile(audio_bytes, filename=filename)
        try:
            await message.answer_audio(
                audio=audio_file,
                caption=safe_caption,
                parse_mode="HTML",
                title=title or None,
                performer=performer or None,
                duration=duration,
            )
            return
        except TelegramBadRequest as exc:
            logger.warning("answer_audio failed: %s", exc)

        await message.answer_document(
            audio_file,
            caption=safe_caption,
            parse_mode="HTML",
        )

    def _publication_keyboard_for_bundle(
        self, bundle: ArchiveBundle
    ) -> InlineKeyboardMarkup:
        entity_id = bundle.metadata.title or bundle.metadata.entity_id or ""
        return self.build_publication_actions_keyboard(
            entity_id,
            platform=self._bundle_platform(bundle),
            username=bundle.metadata.username,
        )

    async def send_publication_video_report(
        self,
        message: Message,
        bundle: ArchiveBundle,
    ) -> None:
        """Видео сверху + полный отчёт + кнопки профиль/звук/HD в одном сообщении."""
        report = self.format_full_report(bundle)
        caption = th.truncate_html(report, TG_CAPTION_MAX)
        keyboard = self._publication_keyboard_for_bundle(bundle)
        preview = self._pick_preview_media(bundle)
        if preview:
            sent = await self._send_preview_message(
                message,
                bundle,
                preview,
                caption=caption,
                keyboard=keyboard,
                report_fallback=report,
            )
            if sent:
                return
        await self._send_html(message, report, keyboard)

    async def deliver_hq_video(
        self,
        message: Message,
        bundle: ArchiveBundle,
        *,
        download_hq: object | None = None,
    ) -> bool:
        """Скачивает и отправляет видео HQ; при ошибке — текст со ссылками."""
        file_bytes: bytes | None = None
        filename = "tiktok_hq.mp4"
        meta: dict | None = None
        notice: str | None = None

        if download_hq:
            try:
                file_bytes, filename, meta = await download_hq(bundle)
            except ValueError as exc:
                notice = str(exc)

        if not file_bytes and self._bundle_platform(bundle) == Platform.TIKTOK:
            video = next(
                (a for a in bundle.media if a.media_type == "video"), None
            )
            if video:
                file_bytes = await self._download_tiktok_video_bytes(
                    bundle,
                    video,
                    label="hq_fallback",
                    prefer_hd=True,
                )
                if file_bytes:
                    meta = {"size_bytes": len(file_bytes)}

        if file_bytes:
            await self.send_hq_report(
                message,
                bundle,
                file_bytes,
                filename,
                delivered=meta,
            )
            return True

        await self.send_deep_report(
            message.bot,
            message,
            bundle,
            "hq",
            notice=notice or "Не удалось скачать файл",
        )
        return False

    async def send_hq_report(
        self,
        message: Message,
        bundle: ArchiveBundle,
        file_bytes: bytes,
        filename: str,
        *,
        delivered: dict | None = None,
    ) -> None:
        caption = self.format_deep_hq(
            bundle, delivered=delivered, notice=None
        )
        safe_caption = th.truncate_html(caption, TG_CAPTION_MAX)
        keyboard = self._publication_keyboard_for_bundle(bundle)
        doc = BufferedInputFile(file_bytes, filename=filename)
        video = next(
            (a for a in bundle.media if a.media_type == "video"), None
        )
        duration = (
            int(video.duration_sec)
            if video and video.duration_sec
            else None
        )
        width = video.width if video else None
        height = video.height if video else None
        is_video = filename.lower().endswith((".mp4", ".mov", ".webm"))

        if video and is_video:
            for attempt_cap in (
                safe_caption,
                th.truncate_html(caption, 900),
                th.truncate_html(caption, 500),
            ):
                try:
                    await message.answer_video(
                        video=doc,
                        caption=attempt_cap,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                        duration=duration,
                        width=width,
                        height=height,
                    )
                    return
                except TelegramBadRequest as exc:
                    logger.warning("HQ video send: %s", exc)

        try:
            await message.answer_document(
                doc,
                caption=safe_caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except TelegramBadRequest as exc:
            logger.warning("HQ document send: %s", exc)
            await message.answer_document(doc, caption=safe_caption)

    async def send_deep_report(
        self,
        bot: Bot,
        message: Message,
        bundle: ArchiveBundle,
        mode: str,
        *,
        notice: str | None = None,
    ) -> None:
        if mode == "vid":
            if bundle.resolved_type == EntityType.PUBLICATION:
                await self.send_publication_video_report(message, bundle)
            else:
                await self.send_archive(bot, message, bundle)
            return
        if mode == "prof":
            await self.send_archive(bot, message, bundle)
            return

        if mode == "hq" and notice:
            report = self.format_deep_hq(bundle, notice=notice)
        else:
            report = self.format_deep_report(bundle, mode)
        keyboard = self._build_keyboard(bundle)
        await self._send_html(message, report, keyboard)

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

    async def _send_profile_archive(
        self,
        message: Message,
        bundle: ArchiveBundle,
        report: str,
        keyboard: InlineKeyboardMarkup | None,
    ) -> bool:
        """Профиль: аватар сверху (файлом) + отчёт."""
        preview = self._profile_avatar_asset(bundle)
        if not preview:
            return False

        platform = self._bundle_platform(bundle)
        referer = self._profile_url(
            bundle.metadata.username or "", platform=platform
        )
        photo_bytes: bytes | None = None
        square_side: int | None = None
        downloader = (
            getattr(self, "tiktok_fetcher", None)
            if platform == Platform.TIKTOK
            else self.fetcher
        )
        if downloader:
            try:
                raw = await downloader.download_image_bytes(
                    preview.url,
                    referer=referer,
                    label="avatar_download",
                )
                photo_bytes, square_side = to_square_jpeg(raw)
                preview.width = square_side
                preview.height = square_side
            except Exception as exc:
                logger.warning("Avatar download failed: %s", exc)

        caption = th.truncate_html(report, TG_CAPTION_MAX)

        for cap in (caption, ""):
            if photo_bytes:
                photo: BufferedInputFile | str = BufferedInputFile(
                    photo_bytes, filename="avatar_1x1.jpg"
                )
            else:
                photo = preview.url
            try:
                await message.answer_photo(
                    photo=photo,
                    caption=cap or None,
                    parse_mode="HTML" if cap else None,
                    reply_markup=keyboard if cap else None,
                )
                if not cap:
                    await self._send_html(message, report, keyboard)
                return True
            except TelegramBadRequest as exc:
                logger.warning("Profile photo send error: %s", exc)
                photo_bytes = None
                continue
            except Exception as exc:
                logger.warning("Profile photo send failed: %s", exc)
                return False
        return False

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
        if bundle.resolved_type == EntityType.PROFILE:
            sent = await self._send_profile_archive(
                message, bundle, report, keyboard
            )
        elif preview:
            sent = await self._send_preview_message(
                message,
                bundle,
                preview,
                caption=caption,
                keyboard=keyboard,
                report_fallback=report,
            )

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

    async def send_processing(
        self,
        message: Message,
        url: str,
        *,
        platform: str = "Instagram",
    ) -> Message:
        return await message.answer(
            f"<b>{self.BRAND}</b>\n\n"
            f"Собираю данные ({platform})…\n"
            f"<blockquote><code>{th.esc(url)}</code></blockquote>",
            parse_mode="HTML",
        )