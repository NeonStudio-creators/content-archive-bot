"""
ArchiveOrchestrator — координирует полный цикл сбора архива.
"""

from __future__ import annotations

import logging

from config import Settings
from core.auth import SessionAuthManager
from core.fetcher import GraphQLFetcher, LinkResolver, ResolvedLink
from core.models import ArchiveBundle, EntityType
from core.parser import EntityDeepCollector
from utils.rate_limit import QuietRateLimiter

logger = logging.getLogger(__name__)


class ArchiveOrchestrator:
    """
    Главный оркестратор: resolve → fetch → parse.
    Делегирует работу специализированным коллекторам по типу сущности.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.auth = SessionAuthManager(settings)
        self.rate_limiter = QuietRateLimiter(settings.request_delay_sec)
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

    async def _collect_profile(self, resolved: ResolvedLink) -> ArchiveBundle:
        username = resolved.identifiers["username"]
        profile_data = await self.fetcher.fetch_web_profile(username)

        user = profile_data.get("data", {}).get("user", {})
        if not user:
            raise ValueError(f"Профиль @{username} не найден или недоступен")

        if user.get("is_private"):
            logger.warning("Профиль @%s приватный — ограниченный сбор", username)

        user_id = str(user.get("id", ""))
        post_edges: list = []
        if user_id and not user.get("is_private"):
            post_edges = await self.fetcher.fetch_user_posts(user_id)

        return self.parser.parse_profile(resolved, profile_data, post_edges)

    async def _collect_publication(self, resolved: ResolvedLink) -> ArchiveBundle:
        shortcode = resolved.identifiers["shortcode"]
        media_data = await self.fetcher.fetch_media_info(
            shortcode,
            original_url=resolved.original_url,
        )

        media_node = media_data.get("data", {}).get("shortcode_media")
        if not media_node:
            raise ValueError(f"Публикация {shortcode} не найдена")

        media_id = str(media_node.get("id", ""))
        comment_edges: list = []
        if media_id:
            try:
                comment_edges = await self.fetcher.fetch_media_comments(
                    media_id, shortcode
                )
            except Exception as exc:
                logger.warning("Комментарии недоступны для %s: %s", shortcode, exc)

        return self.parser.parse_publication(resolved, media_data, comment_edges)

    async def _collect_story(self, resolved: ResolvedLink) -> ArchiveBundle:
        # Stories через web_profile с include_reel
        username = resolved.identifiers["username"]
        profile_data = await self.fetcher.fetch_web_profile(username)
        return self.parser.parse_story(resolved, profile_data)

    async def _collect_highlight(self, resolved: ResolvedLink) -> ArchiveBundle:
        highlight_id = resolved.identifiers["highlight_id"]
        data = await self.fetcher.fetch_highlight(highlight_id)
        return self.parser.parse_highlight(resolved, data)

    async def _collect_collection(self, resolved: ResolvedLink) -> ArchiveBundle:
        # Коллекции saved — через посты пользователя (упрощённый путь)
        username = resolved.identifiers["username"]
        profile_data = await self.fetcher.fetch_web_profile(username)
        user = profile_data.get("data", {}).get("user", {})
        user_id = str(user.get("id", ""))

        edges: list = []
        if user_id:
            edges = await self.fetcher.fetch_user_posts(user_id)

        return self.parser.parse_collection(resolved, edges)