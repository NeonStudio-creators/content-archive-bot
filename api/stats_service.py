"""
Сбор подписчиков и просмотров для Instagram, TikTok, YouTube.
Использует те же fetcher/parser, что и Telegram-бот.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from api.models import StatsPayload, StatsResponse
from core.fetcher import ResolvedLink
from core.link_resolver import LinkResolver
from core.models import ArchiveBundle, EntityType
from core.parser import _edge_count
from core.platforms import Platform
from core.tiktok.resolver import TikTokLinkResolver
from core.youtube.parser import _parse_count
from core.youtube.resolver import YouTubeLinkResolver
from utils.dict_utils import dig, safe_dict

if TYPE_CHECKING:
    from core.orchestrator import ArchiveOrchestrator

logger = logging.getLogger(__name__)


class StatsService:
    def __init__(self, orchestrator: ArchiveOrchestrator) -> None:
        self._orch = orchestrator

    async def fetch(self, url: str, *, platform: Platform | None = None) -> StatsResponse:
        clean = LinkResolver.clean_url(url.strip())
        resolved = LinkResolver.resolve(clean)
        if resolved is None:
            return StatsResponse(
                ok=False,
                platform=platform.value if platform else "unknown",
                entity_type="unknown",
                url=clean,
                error="Не удалось распознать ссылку",
            )
        if platform and resolved.platform != platform:
            return StatsResponse(
                ok=False,
                platform=platform.value,
                entity_type=resolved.entity_type.value,
                url=clean,
                error=f"Ссылка относится к {resolved.platform.value}, не к {platform.value}",
            )
        try:
            if resolved.platform == Platform.INSTAGRAM:
                return await self._instagram(resolved)
            if resolved.platform == Platform.TIKTOK:
                return await self._tiktok(resolved)
            return await self._youtube(resolved)
        except Exception as exc:
            logger.warning("stats fetch failed %s: %s", clean, exc)
            return StatsResponse(
                ok=False,
                platform=resolved.platform.value,
                entity_type=resolved.entity_type.value,
                url=clean,
                error=str(exc),
            )

    async def fetch_instagram(self, url: str) -> StatsResponse:
        return await self.fetch(url, platform=Platform.INSTAGRAM)

    async def fetch_tiktok(self, url: str) -> StatsResponse:
        return await self.fetch(url, platform=Platform.TIKTOK)

    async def fetch_youtube(self, url: str) -> StatsResponse:
        return await self.fetch(url, platform=Platform.YOUTUBE)

    async def _instagram(self, resolved: ResolvedLink) -> StatsResponse:
        if not self._orch.auth.is_configured():
            raise RuntimeError("SESSION_TOKEN не настроен")
        await self._orch.fetcher.ensure_session()
        if resolved.entity_type == EntityType.PROFILE:
            username = resolved.identifiers.get("username", "")
            data = await self._orch.fetcher.fetch_web_profile(username)
            user = safe_dict(dig(data, "data", "user"))
            return StatsResponse(
                ok=True,
                platform="instagram",
                entity_type="profile",
                url=resolved.original_url,
                username=user.get("username") or username,
                display_name=user.get("full_name"),
                stats=StatsPayload(
                    followers=_edge_count(user, "edge_followed_by", "count"),
                    following=_edge_count(user, "edge_follow", "count"),
                    publications=_edge_count(
                        user, "edge_owner_to_timeline_media", "count"
                    ),
                ),
            )
        if resolved.entity_type == EntityType.PUBLICATION:
            bundle = await self._orch.process_publication_quick(resolved.original_url)
            author_followers = await self._instagram_author_followers(
                bundle.metadata.username
            )
            return self._from_bundle(bundle, author_followers=author_followers)
        raise ValueError(f"Instagram: тип {resolved.entity_type.value} не поддерживается")

    async def _tiktok(self, resolved: ResolvedLink) -> StatsResponse:
        await self._orch.tiktok_fetcher.ensure_session()
        if resolved.entity_type == EntityType.PROFILE:
            username = resolved.identifiers.get("username", "")
            payload = await self._orch.tiktok_fetcher.fetch_profile_html(username)
            bundle = self._orch.tiktok_parser.parse_profile(resolved, payload)
            extra = bundle.metadata.raw_fields or {}
            return StatsResponse(
                ok=True,
                platform="tiktok",
                entity_type="profile",
                url=resolved.original_url,
                username=bundle.metadata.username,
                display_name=bundle.metadata.display_name,
                stats=StatsPayload(
                    followers=bundle.metadata.follower_count,
                    following=bundle.metadata.following_count,
                    likes=bundle.metadata.like_count,
                    publications=bundle.metadata.publication_count,
                    aggregate_views=extra.get("aggregate_views"),
                ),
            )
        if resolved.entity_type == EntityType.PUBLICATION:
            bundle = await self._orch.process_publication_quick(resolved.original_url)
            return self._from_bundle(bundle, author_followers=False)
        raise ValueError(f"TikTok: тип {resolved.entity_type.value} не поддерживается")

    async def _youtube(self, resolved: ResolvedLink) -> StatsResponse:
        await self._orch.youtube_fetcher.ensure_session()
        if resolved.entity_type == EntityType.PROFILE:
            ids = resolved.identifiers
            payload = await self._orch.youtube_fetcher.fetch_channel(
                handle=ids.get("handle"),
                channel_id=ids.get("channel_id"),
            )
            bundle = self._orch.youtube_parser.parse_profile(resolved, payload)
            return StatsResponse(
                ok=True,
                platform="youtube",
                entity_type="profile",
                url=resolved.original_url,
                username=bundle.metadata.username,
                display_name=bundle.metadata.display_name,
                stats=StatsPayload(
                    followers=bundle.metadata.follower_count,
                ),
            )
        if resolved.entity_type == EntityType.PUBLICATION:
            bundle = await self._orch.process_publication_quick(resolved.original_url)
            author_followers = await self._youtube_channel_followers(bundle)
            return self._from_bundle(
                bundle,
                author_followers=author_followers,
            )
        raise ValueError(f"YouTube: тип {resolved.entity_type.value} не поддерживается")

    async def _instagram_author_followers(self, username: str | None) -> int | None:
        if not username:
            return None
        try:
            data = await self._orch.fetcher.fetch_web_profile(username)
            user = safe_dict(dig(data, "data", "user"))
            return _edge_count(user, "edge_followed_by", "count")
        except Exception as exc:
            logger.warning("instagram author followers @%s: %s", username, exc)
            return None

    async def _youtube_channel_followers(
        self,
        bundle: ArchiveBundle,
    ) -> int | None:
        channel_id = (bundle.metadata.raw_fields or {}).get("channel_id")
        if not channel_id:
            return None
        try:
            payload = await self._orch.youtube_fetcher.fetch_channel(
                channel_id=str(channel_id),
            )
            return _parse_count(payload.get("subscriber_text"))
        except Exception as exc:
            logger.warning("youtube channel subs for %s: %s", channel_id, exc)
            return None

    def _from_bundle(
        self,
        bundle: ArchiveBundle,
        *,
        author_followers: bool | int | None,
    ) -> StatsResponse:
        m = bundle.metadata
        extra = m.raw_fields or {}
        platform = extra.get("platform") or "unknown"
        followers: int | None = None
        if isinstance(author_followers, int):
            followers = author_followers
        elif author_followers and m.follower_count is not None:
            followers = m.follower_count

        return StatsResponse(
            ok=True,
            platform=str(platform),
            entity_type=m.entity_type.value,
            url=bundle.source_url,
            username=m.username,
            display_name=m.display_name,
            stats=StatsPayload(
                followers=followers,
                following=m.following_count,
                views=m.view_count,
                likes=m.like_count,
                comments=m.comment_count,
                publications=m.publication_count,
                aggregate_views=extra.get("aggregate_views"),
            ),
        )