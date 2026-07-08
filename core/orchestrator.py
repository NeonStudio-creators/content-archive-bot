"""
ArchiveOrchestrator — координирует полный цикл сбора архива.
"""

from __future__ import annotations

import asyncio
import logging

from config import Settings
from core.auth import SessionAuthManager
from core.fetcher import GraphQLFetcher, LinkResolver, ResolvedLink
from core.models import ActivityRecord, ArchiveBundle, EntityType, MediaAsset
from core.parser import EntityDeepCollector, _parse_story_item
from utils.dict_utils import dig, safe_dict
from utils.rate_limit import QuietRateLimiter

logger = logging.getLogger(__name__)


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
        self.parser = EntityDeepCollector()

    async def close(self) -> None:
        await self.fetcher.close()

    async def process_url(self, url: str) -> ArchiveBundle:
        """Полный пайплайн обработки одной ссылки."""
        resolved = LinkResolver.resolve(url)
        if resolved is None:
            raise ValueError(f"Не удалось распознать ссылку: {url}")

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
            gathered = await asyncio.gather(
                self.fetcher.fetch_user_posts(user_id),
                self.fetcher.fetch_user_reels(user_id),
                self.fetcher.fetch_user_tagged(user_id),
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

            enrich_task = self._enrich_top_posts_comments(post_edges)
            highlights_task = self._fetch_highlights_media(highlight_edges)
            extra_activity, highlight_media = await asyncio.gather(
                enrich_task,
                highlights_task,
            )

        return self.parser.parse_profile(
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