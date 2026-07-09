"""
ArchiveOrchestrator — координирует полный цикл сбора архива.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from config import Settings
from core.audio_meta import extract_audio_sources, url_from_audio_block
from core.hq_meta import build_hq_downloads, hq_filename, iter_video_nodes
from core.profile_adapter import extract_avatar_from_profile_payload
from core.auth import SessionAuthManager
from core.fetcher import GraphQLFetcher, ResolvedLink
from core.link_resolver import LinkResolver
from core.models import ActivityRecord, ArchiveBundle, EntityType, MediaAsset
from core.parser import EntityDeepCollector, _parse_story_item
from core.platforms import Platform
from core.tiktok.audio_meta import extract_audio_sources
from core.tiktok.auth import TikTokSessionAuthManager
from core.tiktok.fetcher import TikTokFetcher
from core.tiktok.resolver import TikTokLinkResolver
from core.source_quality import filter_download_candidates, is_compressed_source
from core.tiktok.cdn_urls import sort_download_urls
from core.tiktok.hq_meta import build_hq_downloads as build_tiktok_hq, hq_filename as tiktok_hq_filename
from core.tiktok.parser import TikTokParser
from core.tiktok.profile_adapter import extract_avatar_from_scope
from core.youtube.auth import YouTubeSessionAuthManager
from core.youtube.fetcher import YouTubeFetcher
from core.youtube.hq_meta import build_hq_downloads as build_youtube_hq
from core.youtube.hq_meta import hq_filename as youtube_hq_filename
from core.youtube.audio_meta import extract_audio_sources as extract_youtube_audio
from core.youtube.parser import YouTubeParser
from core.youtube.resolver import YouTubeLinkResolver
from utils.dict_utils import dig, safe_dict
from utils.rate_limit import QuietRateLimiter

logger = logging.getLogger(__name__)


def _parse_tiktok_entity_token(token: str) -> tuple[str, str | None]:
    """video_id или video_id:username из callback-кнопки."""
    raw = token.strip()
    if ":" in raw:
        video_id, username = raw.split(":", 1)
        return video_id.strip(), username.strip() or None
    return raw, None


def _media_node_from_response(media_data: dict) -> dict:
    data_block = safe_dict(media_data.get("data"))
    return (
        safe_dict(data_block.get("shortcode_media"))
        or safe_dict(data_block.get("xdt_shortcode_media"))
        or safe_dict(media_data.get("shortcode_media"))
        or safe_dict(media_data.get("xdt_shortcode_media"))
        or {}
    )


def _post_engagement(edge: dict) -> int:
    node = safe_dict(edge.get("node"))
    likes = safe_dict(node.get("edge_liked_by")).get("count") or 0
    return int(likes) if likes else 0


class ArchiveOrchestrator:
    """
    Главный оркестратор: resolve → fetch → parse.
    Делегирует работу специализированным коллекторам по типу сущности.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.auth = SessionAuthManager(settings)
        self.rate_limiter = QuietRateLimiter(
            settings.request_delay_sec,
            settings.max_concurrent_requests,
        )
        self.fetcher = GraphQLFetcher(settings, self.auth, self.rate_limiter)
        self.tiktok_auth = TikTokSessionAuthManager(settings)
        self.tiktok_fetcher = TikTokFetcher(
            settings, self.tiktok_auth, self.rate_limiter
        )
        self.parser = EntityDeepCollector()
        self.tiktok_parser = TikTokParser()
        self.youtube_auth = YouTubeSessionAuthManager(settings)
        self.youtube_fetcher = YouTubeFetcher(
            settings, self.youtube_auth, self.rate_limiter
        )
        self.youtube_parser = YouTubeParser()

    async def close(self) -> None:
        await self.fetcher.close()
        await self.tiktok_fetcher.close()
        await self.youtube_fetcher.close()

    async def process_publication_quick(self, url: str) -> ArchiveBundle:
        """Быстрый сбор публикации — только медиа и описание, без комментариев."""
        resolved = LinkResolver.resolve(url)
        if resolved is None or resolved.entity_type != EntityType.PUBLICATION:
            raise ValueError(f"Не удалось распознать публикацию: {url}")

        if resolved.platform == Platform.TIKTOK:
            return await self._tiktok_publication_quick(resolved)
        if resolved.platform == Platform.YOUTUBE:
            return await self._youtube_publication_quick(resolved)

        if not self.auth.is_configured():
            raise RuntimeError("SESSION_TOKEN не настроен")

        await self.fetcher.ensure_session()

        shortcode = resolved.identifiers["shortcode"]
        media_data = await self.fetcher.fetch_media_info(
            shortcode,
            original_url=resolved.original_url,
        )
        return self.parser.parse_publication(resolved, media_data)

    async def _tiktok_publication_quick(self, resolved: ResolvedLink) -> ArchiveBundle:
        await self.tiktok_fetcher.ensure_session()
        ids = resolved.identifiers
        video_id = ids.get("video_id") or TikTokLinkResolver.extract_video_id(
            resolved.original_url
        )
        username = ids.get("username")
        canonical = await self.tiktok_fetcher.resolve_short_url(
            resolved.original_url,
            video_id=video_id,
            username=username,
        )
        resolved = ResolvedLink(
            original_url=canonical,
            entity_type=resolved.entity_type,
            identifiers=ids,
            platform=Platform.TIKTOK,
        )
        item = await self.tiktok_fetcher.fetch_video(
            canonical,
            video_id=video_id,
            username=username,
        )
        return self.tiktok_parser.parse_video_quick(resolved, item)

    async def _collect_author_profile(
        self,
        shortcode: str,
        *,
        original_url: str | None = None,
    ) -> ArchiveBundle:
        """Полный сбор профиля автора публикации."""
        url = original_url or f"https://www.instagram.com/p/{shortcode}/"
        media_data = await self.fetcher.fetch_media_info(
            shortcode,
            original_url=url,
        )
        pub_resolved = ResolvedLink(
            original_url=url,
            entity_type=EntityType.PUBLICATION,
            identifiers={"shortcode": shortcode},
        )
        pub_bundle = self.parser.parse_publication(pub_resolved, media_data)
        username = pub_bundle.metadata.username
        if not username:
            raise ValueError("Не удалось определить автора публикации")

        profile_resolved = ResolvedLink(
            original_url=f"https://www.instagram.com/{username}/",
            entity_type=EntityType.PROFILE,
            identifiers={"username": username},
        )
        return await self._collect_profile(profile_resolved)

    async def process_publication_deep(
        self,
        shortcode: str,
        mode: str,
        *,
        original_url: str | None = None,
        platform: Platform = Platform.INSTAGRAM,
    ) -> ArchiveBundle:
        """Глубокий сбор по кнопке: prof | aud | vid | hq."""
        if platform == Platform.TIKTOK:
            return await self._tiktok_publication_deep(
                shortcode,
                mode,
                original_url=original_url,
            )
        if platform == Platform.YOUTUBE:
            return await self._youtube_publication_deep(
                shortcode,
                mode,
                original_url=original_url,
            )

        if not self.auth.is_configured():
            raise RuntimeError("SESSION_TOKEN не настроен")

        await self.fetcher.ensure_session()

        if mode == "prof":
            return await self._collect_author_profile(
                shortcode, original_url=original_url
            )

        url = original_url or f"https://www.instagram.com/p/{shortcode}/"
        resolved = ResolvedLink(
            original_url=url,
            entity_type=EntityType.PUBLICATION,
            identifiers={"shortcode": shortcode},
        )

        if mode == "vid":
            return await self._collect_publication(resolved)

        media_data = await self.fetcher.fetch_media_info(
            shortcode,
            original_url=resolved.original_url,
        )
        bundle = self.parser.parse_publication(resolved, media_data)
        if mode == "aud":
            await self._resolve_publication_audio(
                bundle, _media_node_from_response(media_data)
            )
        elif mode == "hq":
            await self._resolve_publication_hq(
                bundle, _media_node_from_response(media_data)
            )
        return bundle

    async def _resolve_publication_hq(
        self,
        bundle: ArchiveBundle,
        media_node: dict,
    ) -> None:
        """Обогащает медиа ссылками максимального качества."""
        nodes = iter_video_nodes(media_node)
        targets = [
            a
            for a in bundle.media
            if a.media_type in ("video", "image") and a.url
        ]

        if not nodes:
            nodes = [media_node]

        for asset, node in zip(targets, nodes):
            hq = build_hq_downloads(node)
            asset.extra.update(hq)
            best_url = hq.get("hq_best_url")
            if best_url:
                asset.url = best_url
            best = hq.get("hq_best") or {}
            if best.get("width"):
                asset.width = best["width"]
            if best.get("height"):
                asset.height = best["height"]

        if len(targets) > len(nodes):
            for asset in targets[len(nodes):]:
                hq = build_hq_downloads(media_node)
                asset.extra.update(hq)

    async def download_publication_hq(
        self,
        bundle: ArchiveBundle,
        *,
        platform: Platform = Platform.INSTAGRAM,
    ) -> tuple[bytes, str, dict[str, Any]]:
        """Скачивает файл максимального качества для отправки в Telegram."""
        if platform == Platform.TIKTOK:
            return await self._download_tiktok_hq(bundle)
        if platform == Platform.YOUTUBE:
            return await self._download_youtube_hq(bundle)

        candidates_assets = [
            a
            for a in bundle.media
            if a.media_type == "video"
            or (a.media_type == "image" and a.url)
        ]
        if not candidates_assets:
            raise ValueError("Нет медиа для загрузки в максимальном качестве")

        asset = candidates_assets[0]
        extra = asset.extra
        entries = filter_download_candidates(
            list(extra.get("hq_downloads") or []),
            source_only=True,
        )
        if not entries and extra.get("hq_best"):
            entries = [extra["hq_best"]]
        if not entries:
            fallback = extra.get("video_url_best") or asset.url
            if fallback:
                entries = [{"url": fallback, "source": "fallback"}]

        if not entries:
            raise ValueError("Исходное видео недоступно")

        base = (
            bundle.metadata.title
            or bundle.metadata.username
            or "media"
        )
        errors: list[str] = []

        for idx, entry in enumerate(entries[:6]):
            url = entry.get("url")
            if not url:
                continue
            try:
                data, size = await self.fetcher.download_media_bytes(
                    url,
                    referer=bundle.source_url,
                    label="hq_download",
                )
                filename = hq_filename(base, entry, index=idx + 1)
                return data, filename, {**entry, "size_bytes": size}
            except ValueError:
                raise
            except Exception as exc:
                errors.append(f"{entry.get('source')}: {exc}")
                logger.warning("HQ download %s: %s", entry.get("source"), exc)

        raise ValueError(
            "Не удалось скачать файл. "
            + ("; ".join(errors[:3]) if errors else "")
        )

    async def _resolve_publication_audio(
        self,
        bundle: ArchiveBundle,
        media_node: dict,
    ) -> None:
        """Находит прямую ссылку на оригинальный аудиофайл."""
        video = next(
            (a for a in bundle.media if a.media_type == "video"), None
        )
        if not video:
            return

        audio_info = extract_audio_sources(media_node)
        if not audio_info.get("audio_url"):
            audio_info.update({
                k: v
                for k, v in video.extra.items()
                if k.startswith("audio_") or k == "music_canonical_id"
            })

        if not audio_info.get("audio_url"):
            api_block = await self.fetcher.fetch_track_audio_asset(
                music_canonical_id=audio_info.get("music_canonical_id"),
                audio_asset_id=audio_info.get("audio_asset_id"),
                audio_cluster_id=audio_info.get("audio_cluster_id"),
                referer=bundle.source_url,
            )
            if api_block:
                url = url_from_audio_block(api_block)
                if url:
                    audio_info["audio_url"] = url
                    audio_info["audio_source"] = "api"
                    if not audio_info.get("music"):
                        music = {}
                        if api_block.get("title") or api_block.get("song_name"):
                            music["title"] = (
                                api_block.get("title")
                                or api_block.get("song_name")
                            )
                        if api_block.get("display_artist") or api_block.get(
                            "artist_name"
                        ):
                            music["artist"] = (
                                api_block.get("display_artist")
                                or api_block.get("artist_name")
                            )
                        if api_block.get("duration_in_ms"):
                            music["duration_ms"] = api_block["duration_in_ms"]
                        if music:
                            audio_info["music"] = music

        video.extra.update(audio_info)

    @staticmethod
    def _video_url_candidates_from_extra(
        video: MediaAsset,
        *,
        source_only: bool = True,
    ) -> list[str]:
        extra = video.extra
        seen: set[str] = set()
        urls: list[str] = []

        def add(url: str | None) -> None:
            if url and url.startswith("http") and url not in seen:
                seen.add(url)
                urls.append(url)

        entries = filter_download_candidates(
            list(extra.get("hq_downloads") or []),
            source_only=source_only,
        )
        if not entries and extra.get("hq_best"):
            entries = [extra["hq_best"]]

        for entry in entries:
            add(entry.get("url"))

        if source_only:
            for key in ("hdplay", "hq_best_url", "video_url_best"):
                add(extra.get(key))
        else:
            for key in ("hdplay", "play", "wmplay", "hq_best_url", "video_url_best"):
                add(extra.get(key))

        if not source_only or not is_compressed_source(
            (entries[0] if entries else {}).get("source")
        ):
            add(video.url)

        return sort_download_urls(urls)

    @staticmethod
    def _tiktok_video_url_candidates(
        video: MediaAsset,
        *,
        prefer_hd: bool = True,
    ) -> list[str]:
        return ArchiveOrchestrator._video_url_candidates_from_extra(
            video,
            source_only=prefer_hd,
        )

    async def _refresh_tiktok_video_media(self, bundle: ArchiveBundle) -> bool:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            return False
        video_id = bundle.metadata.entity_id or video.id
        username = bundle.metadata.username
        try:
            item = await self.tiktok_fetcher.refresh_mirror_item(
                bundle.source_url,
                video_id=video_id,
                username=username,
            )
        except Exception as exc:
            logger.warning("TikTok mirror refresh: %s", exc)
            return False

        hq = build_tiktok_hq(item)
        video.extra.update(hq)
        for key in ("play", "hdplay", "wmplay", "cover"):
            val = item.get(key)
            if isinstance(val, str) and val.startswith("http"):
                video.extra[key] = val
        best_url = hq.get("hq_best_url")
        if best_url:
            video.url = best_url
        return True

    async def _download_tiktok_hq(
        self, bundle: ArchiveBundle
    ) -> tuple[bytes, str, dict[str, Any]]:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            raise ValueError("Нет видео для загрузки")

        base = (
            bundle.metadata.description
            or bundle.metadata.username
            or "tiktok"
        )
        errors: list[str] = []

        for attempt in range(2):
            entries = filter_download_candidates(
                list(video.extra.get("hq_downloads") or []),
                source_only=True,
            )
            if not entries and video.extra.get("hq_best"):
                entries = [video.extra["hq_best"]]
            if not entries and video.url:
                entries = [{"url": video.url, "source": "fallback"}]
            if not entries:
                if attempt == 0 and await self._refresh_tiktok_video_media(bundle):
                    continue
                raise ValueError("Исходное видео недоступно")

            urls = self._tiktok_video_url_candidates(video, prefer_hd=True)
            try:
                data, size, used_url = await self.tiktok_fetcher.download_from_urls(
                    urls,
                    referer=bundle.source_url,
                    label="tiktok_hq",
                )
                entry = next(
                    (e for e in entries if e.get("url") == used_url),
                    entries[0],
                )
                filename = tiktok_hq_filename(base, entry, index=1)
                return data, filename, {**entry, "size_bytes": size, "url": used_url}
            except ValueError:
                raise
            except Exception as exc:
                errors.append(str(exc))
                logger.warning("TikTok HQ batch: %s", exc)

            if attempt == 0 and await self._refresh_tiktok_video_media(bundle):
                continue
            break

        raise ValueError(
            "Не удалось скачать файл. "
            + ("; ".join(errors[:3]) if errors else "")
        )

    async def download_publication_audio(
        self,
        bundle: ArchiveBundle,
        *,
        platform: Platform = Platform.INSTAGRAM,
    ) -> tuple[bytes, str]:
        """Скачивает оригинальный аудиофайл публикации."""
        if platform == Platform.TIKTOK:
            return await self._download_tiktok_audio(bundle)
        if platform == Platform.YOUTUBE:
            return await self._download_youtube_audio(bundle)

        video = next(
            (a for a in bundle.media if a.media_type == "video"), None
        )
        if not video:
            raise ValueError("В публикации нет видео — аудиофайл недоступен")

        url = video.extra.get("audio_url")
        if not url:
            raise ValueError(
                "Оригинальный аудиофайл недоступен для этой публикации"
            )

        data = await self.fetcher.download_bytes(
            url,
            referer=bundle.source_url,
            label="audio_download",
        )
        if not data:
            raise ValueError("Не удалось скачать аудиофайл")

        fmt = video.extra.get("audio_format") or "m4a"
        music = video.extra.get("music") or {}
        base = (
            music.get("title")
            or bundle.metadata.title
            or bundle.metadata.username
            or "audio"
        )
        safe_base = re.sub(r"[^\w\-.]+", "_", str(base)).strip("_")[:40]
        filename = f"{safe_base or 'audio'}.{fmt}"
        return data, filename

    async def _download_tiktok_audio(
        self, bundle: ArchiveBundle
    ) -> tuple[bytes, str]:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            raise ValueError("В видео нет аудиодорожки")

        url = video.extra.get("audio_url")
        if not url:
            raise ValueError("Оригинальный аудиофайл недоступен")

        data = await self.tiktok_fetcher.download_bytes(
            url,
            referer=bundle.source_url,
            label="tiktok_audio",
        )
        music = video.extra.get("music") or {}
        base = music.get("title") or bundle.metadata.username or "audio"
        safe_base = re.sub(r"[^\w\-.]+", "_", str(base)).strip("_")[:40]
        fmt = video.extra.get("audio_format") or "mp3"
        return data, f"{safe_base or 'audio'}.{fmt}"

    async def _youtube_publication_quick(self, resolved: ResolvedLink) -> ArchiveBundle:
        await self.youtube_fetcher.ensure_session()
        video_id = resolved.identifiers.get("video_id") or YouTubeLinkResolver.extract_video_id(
            resolved.original_url
        )
        if not video_id:
            raise ValueError("Не удалось извлечь ID видео YouTube")
        player = await self.youtube_fetcher.fetch_video(
            resolved.original_url,
            video_id=video_id,
        )
        return self.youtube_parser.parse_video_quick(resolved, player)

    async def _youtube_publication_deep(
        self,
        entity_token: str,
        mode: str,
        *,
        original_url: str | None = None,
    ) -> ArchiveBundle:
        await self.youtube_fetcher.ensure_session()
        video_id = entity_token.strip()
        if not video_id:
            raise ValueError("Не указан ID видео YouTube")

        url = original_url or YouTubeLinkResolver.watch_url(video_id)
        resolved = ResolvedLink(
            original_url=url,
            entity_type=EntityType.PUBLICATION,
            identifiers={"video_id": video_id},
            platform=Platform.YOUTUBE,
        )

        if mode == "prof":
            player = await self.youtube_fetcher.fetch_video(url, video_id=video_id)
            details = player.get("videoDetails") or {}
            channel_id = details.get("channelId")
            if not channel_id:
                raise ValueError("Не удалось определить канал автора")
            profile_resolved = ResolvedLink(
                original_url=YouTubeLinkResolver.channel_url(channel_id=channel_id),
                entity_type=EntityType.PROFILE,
                identifiers={"channel_id": channel_id},
                platform=Platform.YOUTUBE,
            )
            return await self._collect_youtube_profile(profile_resolved)

        if mode in ("vid", "hq"):
            player = await self.youtube_fetcher.fetch_source_player(video_id)
        else:
            player = await self.youtube_fetcher.fetch_video(url, video_id=video_id)

        if mode == "vid":
            bundle = self.youtube_parser.parse_video_deep(resolved, player)
            await self._resolve_youtube_hq(bundle, player)
            return bundle

        bundle = self.youtube_parser.parse_video_quick(resolved, player)
        if mode == "aud":
            await self._resolve_youtube_audio(bundle, player)
        elif mode == "hq":
            await self._resolve_youtube_hq(bundle, player)
        return bundle

    async def _resolve_youtube_hq(
        self,
        bundle: ArchiveBundle,
        player: dict[str, Any],
    ) -> None:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            return
        hq = build_youtube_hq(player)
        video.extra.update(hq)
        best_url = hq.get("hq_best_url")
        if best_url:
            video.url = best_url
        best = hq.get("hq_best") or {}
        if best.get("width"):
            video.width = best["width"]
        if best.get("height"):
            video.height = best["height"]

    async def _resolve_youtube_audio(
        self,
        bundle: ArchiveBundle,
        player: dict[str, Any],
    ) -> None:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            return
        video.extra.update(extract_youtube_audio(player))

    @staticmethod
    def _youtube_video_url_candidates(
        video: MediaAsset,
        *,
        prefer_hd: bool = True,
    ) -> list[str]:
        return ArchiveOrchestrator._video_url_candidates_from_extra(
            video,
            source_only=prefer_hd,
        )

    async def _download_youtube_hq(
        self, bundle: ArchiveBundle
    ) -> tuple[bytes, str, dict[str, Any]]:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            raise ValueError("Нет видео для загрузки")

        video_id = bundle.metadata.entity_id or video.id
        try:
            player = await self.youtube_fetcher.fetch_source_player(video_id)
            await self._resolve_youtube_hq(bundle, player)
            video = next((a for a in bundle.media if a.media_type == "video"), None)
        except Exception as exc:
            logger.warning("youtube source refresh: %s", exc)

        base = (
            bundle.metadata.description
            or bundle.metadata.display_name
            or "youtube"
        )
        entries = filter_download_candidates(
            list(video.extra.get("hq_downloads") or []),
            source_only=True,
        )
        if not entries and video.extra.get("hq_best"):
            entries = [video.extra["hq_best"]]
        if not entries and video.url:
            entries = [{"url": video.url, "source": "fallback"}]
        if not entries:
            raise ValueError(
                "Исходное видео недоступно. Проверьте YOUTUBE_SESSION_TOKEN."
            )

        urls = self._youtube_video_url_candidates(video, prefer_hd=True)
        data, size, used_url = await self.youtube_fetcher.download_from_urls(
            urls,
            referer=bundle.source_url,
            label="youtube_hq",
        )
        entry = next(
            (e for e in entries if e.get("url") == used_url),
            entries[0],
        )
        filename = youtube_hq_filename(base, entry, index=1)
        return data, filename, {**entry, "size_bytes": size, "url": used_url}

    async def _download_youtube_audio(
        self, bundle: ArchiveBundle
    ) -> tuple[bytes, str]:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            raise ValueError("В видео нет аудиодорожки")

        url = video.extra.get("audio_url")
        if not url:
            raise ValueError("Оригинальный аудиофайл недоступен")

        data = await self.youtube_fetcher.download_bytes(
            url,
            referer=bundle.source_url,
            label="youtube_audio",
        )
        music = video.extra.get("music") or {}
        base = music.get("title") or bundle.metadata.display_name or "audio"
        safe_base = re.sub(r"[^\w\-.]+", "_", str(base)).strip("_")[:40]
        fmt = video.extra.get("audio_format") or "m4a"
        return data, f"{safe_base or 'audio'}.{fmt}"

    async def _collect_youtube_profile(self, resolved: ResolvedLink) -> ArchiveBundle:
        ids = resolved.identifiers
        payload = await self.youtube_fetcher.fetch_channel(
            handle=ids.get("handle") or ids.get("username"),
            channel_id=ids.get("channel_id"),
        )
        return self.youtube_parser.parse_profile(resolved, payload)

    async def _tiktok_publication_deep(
        self,
        entity_token: str,
        mode: str,
        *,
        original_url: str | None = None,
    ) -> ArchiveBundle:
        await self.tiktok_fetcher.ensure_session()
        video_id, username = _parse_tiktok_entity_token(entity_token)
        if not video_id:
            raise ValueError("Не указан ID видео TikTok")

        url = original_url or TikTokLinkResolver.video_page_url(
            video_id,
            username,
            prefer_mobile=not username,
        )
        canonical = await self.tiktok_fetcher.resolve_short_url(
            url,
            video_id=video_id,
            username=username,
        )
        resolved = ResolvedLink(
            original_url=canonical,
            entity_type=EntityType.PUBLICATION,
            identifiers={
                "video_id": video_id,
                **({"username": username} if username else {}),
            },
            platform=Platform.TIKTOK,
        )

        if mode == "prof":
            item = await self.tiktok_fetcher.fetch_video(
                canonical,
                video_id=video_id,
                username=username,
            )
            author = safe_dict(item.get("author"))
            resolved_username = (
                author.get("unique_id")
                or username
                or resolved.identifiers.get("username")
            )
            if not resolved_username:
                raise ValueError("Не удалось определить автора видео")
            profile_resolved = ResolvedLink(
                original_url=f"{self.settings.tiktok_base_url}/@{resolved_username}",
                entity_type=EntityType.PROFILE,
                identifiers={"username": resolved_username},
                platform=Platform.TIKTOK,
            )
            return await self._collect_tiktok_profile(profile_resolved)

        item = await self.tiktok_fetcher.fetch_video(
            canonical,
            video_id=video_id,
            username=username,
        )
        if mode == "vid":
            bundle = self.tiktok_parser.parse_video_deep(resolved, item)
            await self._resolve_tiktok_hq(bundle, item)
            return bundle

        bundle = self.tiktok_parser.parse_video_quick(resolved, item)
        if mode == "aud":
            await self._resolve_tiktok_audio(bundle, item)
        elif mode == "hq":
            await self._resolve_tiktok_hq(bundle, item)
        return bundle

    async def _resolve_tiktok_hq(
        self,
        bundle: ArchiveBundle,
        item: dict[str, Any],
    ) -> None:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            return
        hq = build_tiktok_hq(item)
        video.extra.update(hq)
        best_url = hq.get("hq_best_url")
        if best_url:
            video.url = best_url
        best = hq.get("hq_best") or {}
        if best.get("width"):
            video.width = best["width"]
        if best.get("height"):
            video.height = best["height"]

    async def _resolve_tiktok_audio(
        self,
        bundle: ArchiveBundle,
        item: dict[str, Any],
    ) -> None:
        video = next((a for a in bundle.media if a.media_type == "video"), None)
        if not video:
            return
        video.extra.update(extract_audio_sources(item))

    async def download_tiktok_hq(
        self, bundle: ArchiveBundle
    ) -> tuple[bytes, str, dict[str, Any]]:
        return await self.download_publication_hq(bundle, platform=Platform.TIKTOK)

    async def download_tiktok_audio(
        self, bundle: ArchiveBundle
    ) -> tuple[bytes, str]:
        return await self.download_publication_audio(bundle, platform=Platform.TIKTOK)

    async def _collect_tiktok_profile(self, resolved: ResolvedLink) -> ArchiveBundle:
        username = resolved.identifiers["username"]
        payload = await self.tiktok_fetcher.fetch_profile_html(username)
        bundle = self.tiktok_parser.parse_profile(resolved, payload)
        await self._ensure_tiktok_profile_avatar(bundle, payload)
        return bundle

    async def _ensure_tiktok_profile_avatar(
        self,
        bundle: ArchiveBundle,
        profile_data: dict[str, Any] | None = None,
    ) -> None:
        if bundle.resolved_type != EntityType.PROFILE:
            return

        url = bundle.metadata.avatar_url
        if not url and profile_data:
            scope = profile_data.get("scope") or {}
            url = extract_avatar_from_scope(scope)
        if not url:
            for raw in bundle.raw_graphql or []:
                scope = raw.get("scope") or {}
                url = extract_avatar_from_scope(scope)
                if url:
                    break
        if url:
            self._upsert_avatar_media(bundle, url)

    async def process_url(self, url: str) -> ArchiveBundle:
        """Полный пайплайн обработки одной ссылки."""
        resolved = LinkResolver.resolve(url)
        if resolved is None:
            raise ValueError(f"Не удалось распознать ссылку: {url}")

        if resolved.platform == Platform.TIKTOK:
            if resolved.entity_type == EntityType.PROFILE:
                await self.tiktok_fetcher.ensure_session()
                return await self._collect_tiktok_profile(resolved)
            if resolved.entity_type == EntityType.PUBLICATION:
                return await self._tiktok_publication_quick(resolved)
            raise ValueError(f"TikTok: тип {resolved.entity_type} не поддерживается")

        if resolved.platform == Platform.YOUTUBE:
            await self.youtube_fetcher.ensure_session()
            if resolved.entity_type == EntityType.PROFILE:
                return await self._collect_youtube_profile(resolved)
            if resolved.entity_type == EntityType.PUBLICATION:
                return await self._youtube_publication_quick(resolved)
            raise ValueError(f"YouTube: тип {resolved.entity_type} не поддерживается")

        if not self.auth.is_configured():
            raise RuntimeError("SESSION_TOKEN не настроен")

        await self.fetcher.ensure_session()

        logger.info(
            "Обработка %s → тип %s, id=%s",
            url,
            resolved.entity_type.value,
            resolved.identifiers,
        )

        handlers = {
            EntityType.PROFILE: self._collect_profile,
            EntityType.PUBLICATION: self._collect_publication,
            EntityType.STORY: self._collect_story,
            EntityType.HIGHLIGHT: self._collect_highlight,
            EntityType.COLLECTION: self._collect_collection,
        }

        handler = handlers.get(resolved.entity_type)
        if handler is None:
            raise ValueError(f"Тип {resolved.entity_type} не поддерживается")

        return await handler(resolved)

    async def _enrich_top_posts_comments(
        self, post_edges: list
    ) -> list[ActivityRecord]:
        """Комментарии к топ-N постам по лайкам."""
        limit = self.settings.profile_enrich_top_posts
        if limit <= 0 or not post_edges:
            return []

        top = sorted(post_edges, key=_post_engagement, reverse=True)[:limit]
        tasks: list = []
        meta: list[tuple[str, str]] = []

        for edge in top:
            node = safe_dict(edge.get("node"))
            media_id = str(node.get("id", ""))
            shortcode = node.get("shortcode", "")
            if media_id and shortcode:
                tasks.append(
                    self.fetcher.fetch_media_comments(media_id, shortcode)
                )
                meta.append((shortcode, media_id))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        activity: list[ActivityRecord] = []

        for (shortcode, _), result in zip(meta, results):
            if isinstance(result, BaseException):
                logger.warning("Комментарии %s: %s", shortcode, result)
                continue
            for edge in result[:15]:
                comment = safe_dict(edge.get("node"))
                activity.append(
                    ActivityRecord(
                        activity_type="comment",
                        actor=safe_dict(comment.get("owner")).get("username"),
                        content=comment.get("text"),
                        extra={
                            "post_shortcode": shortcode,
                            "likes": safe_dict(
                                comment.get("edge_liked_by")
                            ).get("count", 0),
                        },
                    )
                )

        return activity

    async def _fetch_highlights_media(
        self, highlight_edges: list
    ) -> list[MediaAsset]:
        """Скачивает элементы первых N highlights."""
        limit = self.settings.profile_max_highlights_fetch
        if limit <= 0 or not highlight_edges:
            return []

        tasks = []
        highlight_ids: list[str] = []
        for edge in highlight_edges[:limit]:
            hid = str(safe_dict(edge.get("node")).get("id", ""))
            if hid:
                tasks.append(self.fetcher.fetch_highlight_items(hid))
                highlight_ids.append(hid)

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        media: list[MediaAsset] = []

        for hid, result in zip(highlight_ids, results):
            if isinstance(result, BaseException):
                logger.warning("Highlight %s: %s", hid, result)
                continue
            title, items = result
            for item in items:
                asset = _parse_story_item(item)
                if asset:
                    asset.extra["highlight_id"] = hid
                    asset.extra["highlight_title"] = title
                    asset.extra["source"] = "highlight"
                    media.append(asset)

        return media

    @staticmethod
    def _upsert_avatar_media(bundle: ArchiveBundle, url: str) -> None:
        bundle.metadata.avatar_url = url
        for idx, asset in enumerate(bundle.media):
            if asset.extra.get("source") == "avatar":
                bundle.media[idx] = MediaAsset(
                    id=asset.id,
                    media_type="image",
                    url=url,
                    extra={"source": "avatar"},
                )
                return
        bundle.media.insert(
            0,
            MediaAsset(
                id=f"avatar_{bundle.metadata.entity_id}",
                media_type="image",
                url=url,
                extra={"source": "avatar"},
            ),
        )

    async def _ensure_profile_avatar(
        self,
        bundle: ArchiveBundle,
        profile_data: dict | None = None,
    ) -> None:
        """Гарантирует URL аватарки в bundle перед отправкой в Telegram."""
        if bundle.resolved_type != EntityType.PROFILE:
            return

        sources: list[dict] = []
        if profile_data:
            sources.append(profile_data)
        sources.extend(bundle.raw_graphql or [])

        for payload in sources:
            url = extract_avatar_from_profile_payload(payload)
            if url:
                self._upsert_avatar_media(bundle, url)
                return

        if bundle.metadata.avatar_url:
            self._upsert_avatar_media(bundle, bundle.metadata.avatar_url)
            return

        username = bundle.metadata.username
        if not username:
            return

        try:
            fresh = await self.fetcher.fetch_web_profile(username)
            url = extract_avatar_from_profile_payload(fresh)
            if url:
                self._upsert_avatar_media(bundle, url)
                if bundle.raw_graphql:
                    bundle.raw_graphql[0] = fresh
                else:
                    bundle.raw_graphql = [fresh]
        except Exception as exc:
            logger.warning(
                "ensure_profile_avatar @%s: %s", username, exc
            )

    async def _collect_profile(self, resolved: ResolvedLink) -> ArchiveBundle:
        username = resolved.identifiers["username"]
        profile_data = await self.fetcher.fetch_web_profile(username)

        user = safe_dict(dig(profile_data, "data", "user"))
        if not user:
            raise ValueError(f"Профиль @{username} не найден или недоступен")

        if user.get("is_private"):
            logger.warning("Профиль @%s приватный — ограниченный сбор", username)

        user_id = str(user.get("id", ""))
        post_edges: list = []
        reel_edges: list = []
        tagged_edges: list = []
        highlight_edges: list = []
        highlight_media: list[MediaAsset] = []
        extra_activity: list[ActivityRecord] = []

        if user_id and not user.get("is_private"):
            pages = self.settings.profile_max_pages
            tagged_pages = self.settings.profile_max_tagged_pages

            gathered = await asyncio.gather(
                self.fetcher.fetch_user_posts(user_id, max_pages=pages),
                self.fetcher.fetch_user_reels(user_id, max_pages=pages),
                self.fetcher.fetch_user_tagged(
                    user_id, max_pages=tagged_pages
                ),
                self.fetcher.fetch_user_highlights(user_id),
                return_exceptions=True,
            )
            names = ("posts", "reels", "tagged", "highlights")
            results: list = []
            for name, item in zip(names, gathered):
                if isinstance(item, BaseException):
                    logger.warning("Сбор %s: %s", name, item)
                    results.append([])
                else:
                    results.append(item)
            post_edges, reel_edges, tagged_edges, highlight_edges = results

            optional_tasks: list[tuple[str, object]] = []
            if self.settings.profile_enrich_top_posts > 0:
                optional_tasks.append(
                    ("comments", self._enrich_top_posts_comments(post_edges))
                )
            if self.settings.profile_max_highlights_fetch > 0:
                optional_tasks.append(
                    (
                        "highlights",
                        self._fetch_highlights_media(highlight_edges),
                    )
                )

            if optional_tasks:
                done = await asyncio.gather(
                    *(coro for _, coro in optional_tasks),
                    return_exceptions=True,
                )
                for (name, _), result in zip(optional_tasks, done):
                    if isinstance(result, BaseException):
                        logger.warning("Доп. сбор %s: %s", name, result)
                    elif name == "comments":
                        extra_activity = result
                    elif name == "highlights":
                        highlight_media = result

        bundle = self.parser.parse_profile(
            resolved,
            profile_data,
            post_edges,
            reel_edges,
            tagged_edges,
            highlight_edges=highlight_edges,
            highlight_media=highlight_media,
            extra_activity=extra_activity,
            raw_responses=[],
        )
        await self._ensure_profile_avatar(bundle, profile_data)
        return bundle

    async def _collect_publication(self, resolved: ResolvedLink) -> ArchiveBundle:
        shortcode = resolved.identifiers["shortcode"]
        media_data = await self.fetcher.fetch_media_info(
            shortcode,
            original_url=resolved.original_url,
        )

        data_block = safe_dict(media_data.get("data"))
        media_node = (
            data_block.get("shortcode_media")
            or data_block.get("xdt_shortcode_media")
            or media_data.get("shortcode_media")
            or media_data.get("xdt_shortcode_media")
        )
        if not media_node or not isinstance(media_node, dict):
            raise ValueError(f"Публикация {shortcode} не найдена")

        media_id = str(media_node.get("id", ""))
        owner_username = safe_dict(media_node.get("owner")).get("username", "")

        comment_edges: list = []
        likers: list = []
        owner_profile: dict | None = None

        if media_id:
            tasks = [
                self.fetcher.fetch_media_comments(media_id, shortcode),
                self.fetcher.fetch_media_likers(media_id, shortcode),
            ]
            if owner_username:
                tasks.append(self.fetcher.fetch_web_profile(owner_username))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            if not isinstance(results[0], BaseException):
                comment_edges = results[0]
            else:
                logger.warning("Комментарии: %s", results[0])

            if len(results) > 1 and not isinstance(results[1], BaseException):
                likers = results[1]
            elif len(results) > 1:
                logger.warning("Лайки: %s", results[1])

            if len(results) > 2 and not isinstance(results[2], BaseException):
                owner_profile = results[2]

        return self.parser.parse_publication(
            resolved,
            media_data,
            comment_edges,
            likers=likers,
            owner_profile=owner_profile,
        )

    async def _collect_story(self, resolved: ResolvedLink) -> ArchiveBundle:
        username = resolved.identifiers["username"]
        profile_data = await self.fetcher.fetch_web_profile(username)
        return self.parser.parse_story(resolved, profile_data)

    async def _collect_highlight(self, resolved: ResolvedLink) -> ArchiveBundle:
        highlight_id = resolved.identifiers["highlight_id"]
        data = await self.fetcher.fetch_highlight(highlight_id)
        return self.parser.parse_highlight(resolved, data)

    async def _collect_collection(self, resolved: ResolvedLink) -> ArchiveBundle:
        username = resolved.identifiers["username"]
        profile_data = await self.fetcher.fetch_web_profile(username)
        user = safe_dict(dig(profile_data, "data", "user"))
        user_id = str(user.get("id", ""))

        edges: list = []
        if user_id:
            edges = await self.fetcher.fetch_user_posts(user_id)

        return self.parser.parse_collection(resolved, edges)