"""
GraphQLFetcher и LinkResolver — сетевой слой для внутренних API-запросов.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp

from config import Settings
from core.auth import SessionAuthManager
from core.models import EntityType
from utils.rate_limit import QuietRateLimiter
from utils.retry import with_retry

logger = logging.getLogger(__name__)

# ── GraphQL doc_id (внутренние идентификаторы запросов платформы) ──────────
DOC_IDS = {
    "user_profile": "25025320fc2a3a4c0da3e2ee7b81bce8",
    "user_posts": "0033d8c4fa3a17f23b88bd3ac1c55e5b",
    "media_info": "17880173348408341",
    "media_comments": "97b41c299c4654e3ad9531e2d966a90a",
    "story_viewer": "ad99dd9d3646cc3c0dda65deb29b92a0",
    "highlight": "45246d3fe16ccc6577e0eb1a2397fb74",
}

# ── Паттерны URL для LinkResolver ──────────────────────────────────────────
URL_PATTERNS: list[tuple[re.Pattern[str], EntityType, str]] = [
    (
        re.compile(
            r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
            re.I,
        ),
        EntityType.PUBLICATION,
        "shortcode",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.)?instagram\.com/stories/highlights/(\d+)",
            re.I,
        ),
        EntityType.HIGHLIGHT,
        "highlight_id",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.)?instagram\.com/stories/([^/]+)/(\d+)",
            re.I,
        ),
        EntityType.STORY,
        "story",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.)?instagram\.com/([^/]+)/saved/([^/?#]+)",
            re.I,
        ),
        EntityType.COLLECTION,
        "collection",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?$",
            re.I,
        ),
        EntityType.PROFILE,
        "username",
    ),
]

RESERVED_USERNAMES = {
    "p", "reel", "tv", "stories", "explore", "accounts", "direct",
    "about", "legal", "developer", "api",
}


@dataclass
class ResolvedLink:
    """Результат разбора URL пользователем."""

    original_url: str
    entity_type: EntityType
    identifiers: dict[str, str]


class LinkResolver:
    """Определяет тип сущности и извлекает идентификаторы из URL."""

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        pattern = re.compile(
            r"https?://(?:www\.)?instagram\.com/[^\s<>\"']+",
            re.I,
        )
        return pattern.findall(text)

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        url = url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"

        for regex, entity_type, key in URL_PATTERNS:
            match = regex.search(url)
            if not match:
                continue

            if key == "username":
                username = match.group(1).lower()
                if username in RESERVED_USERNAMES:
                    continue
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={"username": username},
                )

            if key == "shortcode":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={"shortcode": match.group(1)},
                )

            if key == "story":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={
                        "username": match.group(1),
                        "story_id": match.group(2),
                    },
                )

            if key == "highlight_id":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={"highlight_id": match.group(1)},
                )

            if key == "collection":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={
                        "username": match.group(1),
                        "collection_id": match.group(2),
                    },
                )

        return None


@dataclass
class GraphQLFetcher:
    """
    Выполняет GraphQL-запросы к внутреннему API платформы.
    Поддерживает пагинацию через page_info / end_cursor.
    """

    settings: Settings
    auth: SessionAuthManager
    rate_limiter: QuietRateLimiter

    def __post_init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def graphql(
        self,
        doc_id: str,
        variables: dict[str, Any],
        *,
        referer: str | None = None,
        label: str = "graphql",
    ) -> dict[str, Any]:
        """Одиночный GraphQL-запрос с ретраями и rate-limit."""

        async def _do_request() -> dict[str, Any]:
            await self.rate_limiter.wait()
            session = await self._get_session()

            params = {
                "doc_id": doc_id,
                "variables": json.dumps(variables, separators=(",", ":")),
            }
            headers = self.auth.build_headers(referer=referer)
            cookies = self.auth.build_cookies()

            async with session.get(
                self.settings.graphql_endpoint,
                params=params,
                headers=headers,
                cookies=cookies,
            ) as resp:
                if resp.status == 429:
                    resp.raise_for_status()
                body = await resp.text()
                if resp.status >= 400:
                    logger.error("GraphQL %s: HTTP %s — %s", label, resp.status, body[:300])
                    resp.raise_for_status()
                return json.loads(body)

        return await with_retry(
            _do_request,
            max_retries=self.settings.max_retries,
            backoff_sec=self.settings.retry_backoff_sec,
            label=label,
        )

    async def fetch_paginated(
        self,
        doc_id: str,
        variables: dict[str, Any],
        *,
        edges_path: list[str],
        referer: str | None = None,
        label: str = "paginated",
    ) -> list[dict[str, Any]]:
        """
        Универсальная пагинация: обходит все страницы до has_next_page=False
        или достижения max_pagination_pages.
        """
        all_edges: list[dict[str, Any]] = []
        cursor: str | None = None
        base_vars = dict(variables)

        for page in range(self.settings.max_pagination_pages):
            vars_page = {**base_vars}
            if cursor:
                vars_page["after"] = cursor

            data = await self.graphql(
                doc_id,
                vars_page,
                referer=referer,
                label=f"{label}_page_{page}",
            )

            # Навигация по вложенному пути edges
            node: Any = data.get("data", data)
            for key in edges_path:
                node = node.get(key, {}) if isinstance(node, dict) else {}

            edges = node.get("edges", []) if isinstance(node, dict) else []
            all_edges.extend(edges)

            page_info = node.get("page_info", {}) if isinstance(node, dict) else {}
            if not page_info.get("has_next_page"):
                break
            cursor = page_info.get("end_cursor")
            if not cursor:
                break

        return all_edges

    async def fetch_web_profile(self, username: str) -> dict[str, Any]:
        """Профиль через GraphQL doc_id."""
        return await self.graphql(
            DOC_IDS["user_profile"],
            {"username": username, "include_reel": True},
            referer=f"{self.settings.platform_base_url}/{username}/",
            label="user_profile",
        )

    async def fetch_user_posts(self, user_id: str) -> list[dict[str, Any]]:
        """Публикации профиля с пагинацией."""
        return await self.fetch_paginated(
            DOC_IDS["user_posts"],
            {
                "id": user_id,
                "first": self.settings.pagination_page_size,
            },
            edges_path=["user", "edge_owner_to_timeline_media"],
            label="user_posts",
        )

    async def fetch_media_info(self, shortcode: str) -> dict[str, Any]:
        """Метаданные одной публикации."""
        return await self.graphql(
            DOC_IDS["media_info"],
            {"shortcode": shortcode},
            referer=f"{self.settings.platform_base_url}/p/{shortcode}/",
            label="media_info",
        )

    async def fetch_media_comments(
        self, media_id: str, shortcode: str
    ) -> list[dict[str, Any]]:
        """Комментарии к публикации."""
        return await self.fetch_paginated(
            DOC_IDS["media_comments"],
            {
                "shortcode": shortcode,
                "first": 20,
            },
            edges_path=["shortcode_media", "edge_media_to_parent_comment"],
            referer=f"{self.settings.platform_base_url}/p/{shortcode}/",
            label="media_comments",
        )

    async def fetch_highlight(self, highlight_id: str) -> dict[str, Any]:
        return await self.graphql(
            DOC_IDS["highlight"],
            {"highlight_reel_ids": [highlight_id], "precomposed_overlay": False},
            label="highlight",
        )

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")