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
from core.media_adapter import (
    from_embedded_json,
    from_graphql_polaris,
    from_rest_media_info,
)
from core.models import EntityType
from utils.instagram_id import shortcode_to_media_id
from utils.rate_limit import QuietRateLimiter
from utils.retry import with_retry

logger = logging.getLogger(__name__)

# ── GraphQL doc_id (внутренние идентификаторы запросов платформы) ──────────
DOC_IDS = {
    "user_profile": "25025320fc2a3a4c0da3e2ee7b81bce8",
    "user_posts": "0033d8c4fa3a17f23b88bd3ac1c55e5b",
    # Актуальный doc_id (media_id, не shortcode) — Polaris 2025+
    "media_info": "27130156389949648",
    "media_comments": "97b41c299c4654e3ad9531e2d966a90a",
    "story_viewer": "ad99dd9d3646cc3c0dda65deb29b92a0",
    "highlight": "45246d3fe16ccc6577e0eb1a2397fb74",
}

MOBILE_API_BASE = "https://i.instagram.com/api/v1"

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
        self._lsd_token: str | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        referer: str | None = None,
        label: str = "request",
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        api_type: str = "web",
    ) -> dict[str, Any] | str:
        async def _do_request() -> dict[str, Any] | str:
            await self.rate_limiter.wait()
            session = await self._get_session()
            headers = self.auth.build_headers(referer=referer, api_type=api_type)
            cookies = self.auth.build_cookies()

            async with session.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                cookies=cookies,
            ) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    logger.warning(
                        "%s: HTTP %s — %s", label, resp.status, body[:300]
                    )
                    resp.raise_for_status()
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return body

        return await with_retry(
            _do_request,
            max_retries=self.settings.max_retries,
            backoff_sec=self.settings.retry_backoff_sec,
            label=label,
        )

    async def graphql(
        self,
        doc_id: str,
        variables: dict[str, Any],
        *,
        referer: str | None = None,
        label: str = "graphql",
        method: str = "GET",
    ) -> dict[str, Any]:
        """GraphQL-запрос (GET или POST) с ретраями."""

        variables_json = json.dumps(variables, separators=(",", ":"))

        if method.upper() == "POST":
            form: dict[str, str] = {
                "doc_id": doc_id,
                "variables": variables_json,
                "server_timestamps": "true",
            }
            if self._lsd_token:
                form["lsd"] = self._lsd_token
            result = await self._request(
                "POST",
                self.settings.graphql_endpoint,
                referer=referer,
                label=label,
                data=form,
            )
        else:
            result = await self._request(
                "GET",
                self.settings.graphql_endpoint,
                referer=referer,
                label=label,
                params={"doc_id": doc_id, "variables": variables_json},
            )

        if isinstance(result, dict):
            return result
        raise ValueError(f"GraphQL {label}: невалидный JSON-ответ")

    async def mobile_api_get(
        self,
        path: str,
        *,
        referer: str | None = None,
        label: str = "mobile_api",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Запрос к i.instagram.com/api/v1 (работает с sessionid)."""
        url = f"{MOBILE_API_BASE}{path}"
        result = await self._request(
            "GET",
            url,
            referer=referer,
            label=label,
            params=params,
            api_type="mobile",
        )
        if isinstance(result, dict):
            return result
        raise ValueError(f"Mobile API {label}: невалидный JSON")

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

    def _publication_referer(self, shortcode: str, original_url: str | None = None) -> str:
        if original_url and "instagram.com" in original_url:
            return original_url.split("?")[0]
        return f"{self.settings.platform_base_url}/reel/{shortcode}/"

    async def _fetch_media_via_rest(
        self, shortcode: str, media_id: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            payload = await self.mobile_api_get(
                f"/media/{media_id}/info/",
                referer=referer,
                label="media_info_rest",
            )
            result = from_rest_media_info(payload, shortcode)
            if result:
                logger.info("media_info: REST API OK для %s", shortcode)
                return result
        except Exception as exc:
            logger.warning("media_info REST failed для %s: %s", shortcode, exc)
        return None

    async def _fetch_media_via_graphql(
        self, shortcode: str, media_id: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            payload = await self.graphql(
                DOC_IDS["media_info"],
                {"media_id": media_id},
                referer=referer,
                label="media_info_graphql",
                method="POST",
            )
            result = from_graphql_polaris(payload, shortcode)
            if result:
                logger.info("media_info: GraphQL OK для %s", shortcode)
                return result
            # Старый формат shortcode_media
            node = payload.get("data", {}).get("shortcode_media")
            if node:
                return {"data": {"shortcode_media": node}}
        except Exception as exc:
            logger.warning("media_info GraphQL failed для %s: %s", shortcode, exc)
        return None

    async def _fetch_media_via_html(
        self, shortcode: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            html = await self._request(
                "GET",
                referer,
                referer=referer,
                label="media_info_html",
            )
            if not isinstance(html, str):
                return None

            # application/json в <script>
            for match in re.finditer(
                r'<script[^>]*type="application/json"[^>]*>(\{.+?\})</script>',
                html,
                re.DOTALL,
            ):
                try:
                    blob = json.loads(match.group(1))
                    result = from_embedded_json(blob, shortcode)
                    if result:
                        logger.info("media_info: HTML JSON OK для %s", shortcode)
                        return result
                except json.JSONDecodeError:
                    continue

            # xdt_api / shortcode_media в тексте страницы
            for pattern in (
                r'"shortcode_media"\s*:\s*(\{.+?\})\s*,\s*"',
                r'"xdt_shortcode_media"\s*:\s*(\{.+?\})\s*,\s*"',
            ):
                match = re.search(pattern, html)
                if match:
                    try:
                        node = json.loads(match.group(1))
                        return {"data": {"shortcode_media": node}}
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("media_info HTML failed для %s: %s", shortcode, exc)
        return None

    async def fetch_media_info(
        self,
        shortcode: str,
        *,
        original_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Метаданные публикации — каскад стратегий:
        1. REST i.instagram.com/api/v1/media/{id}/info/
        2. GraphQL POST с media_id (новый doc_id)
        3. Парсинг HTML страницы
        """
        media_id = shortcode_to_media_id(shortcode)
        referer = self._publication_referer(shortcode, original_url)
        logger.info(
            "media_info %s → media_id=%s, referer=%s",
            shortcode,
            media_id,
            referer,
        )

        for fetcher in (
            self._fetch_media_via_rest(shortcode, media_id, referer),
            self._fetch_media_via_graphql(shortcode, media_id, referer),
            self._fetch_media_via_html(shortcode, referer),
        ):
            result = await fetcher
            if result:
                return result

        raise ValueError(
            f"Публикация {shortcode} недоступна. "
            "Проверьте SESSION_TOKEN и CSRF_TOKEN — возможно, сессия истекла."
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