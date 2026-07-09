"""
GraphQLFetcher и LinkResolver — сетевой слой для внутренних API-запросов.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import aiohttp

from config import Settings
from core.auth import SessionAuthManager
from core.auth_errors import is_instagram_auth_error
from core.media_adapter import (
    from_embedded_json,
    from_graphql_polaris,
    from_rest_media_info,
)
from core.profile_adapter import (
    find_user_id_in_search,
    from_embedded_profile_json,
    from_gql_profile,
    from_html_meta,
    from_usernameinfo,
    from_web_profile_info,
)
from core.session_bootstrap import (
    is_profile_not_found_html,
    merge_cookies,
    parse_set_cookies,
    parse_tokens_from_html,
)
from core.models import EntityType
from core.platforms import Platform
from utils.concurrency import first_success
from utils.dict_utils import dig, safe_dict
from utils.instagram_id import shortcode_to_media_id
from utils.rate_limit import QuietRateLimiter
from utils.retry import with_retry

logger = logging.getLogger(__name__)

# ── GraphQL doc_id (внутренние идентификаторы запросов платформы) ──────────
DOC_IDS = {
    "user_profile": "26762473490008061",
    "user_profile_by_username": "26347858941511777",
    "user_profile_legacy": "25025320fc2a3a4c0da3e2ee7b81bce8",
    "user_posts": "7898261790222653",
    # Актуальный doc_id (media_id, не shortcode) — Polaris 2025+
    "media_info": "27130156389949648",
    "media_comments": "97b41c299c4654e3ad9531e2d966a90a",
    "story_viewer": "ad99dd9d3646cc3c0dda65deb29b92a0",
    "highlight": "45246d3fe16ccc6577e0eb1a2397fb74",
    "user_reels": "7845543455542541",
    "user_tagged": "e31a871f7301132ceaab56507a66bbb7",
    "user_highlights": "7c16654f22c819fb63d1183034a5162f",
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
            r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/(?:reels|tagged)/?$",
            re.I,
        ),
        EntityType.PROFILE,
        "username",
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
    platform: Platform = Platform.INSTAGRAM


class LinkResolver:
    """Определяет тип сущности и извлекает идентификаторы из URL."""

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        pattern = re.compile(
            r"https?://(?:www\.)?instagram\.com/[^\s<>\"']+",
            re.I,
        )
        return [LinkResolver.clean_url(u) for u in pattern.findall(text)]

    @staticmethod
    def clean_url(url: str) -> str:
        """Убирает ?igsh=, #fragment и лишние слэши — для распознавания ссылок."""
        url = url.strip()
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        url = cls.clean_url(url)

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
@dataclass
class InstagramSessionVerify:
    """Результат проверки Instagram-сессии (/session)."""

    session_id_ok: bool
    csrf_ok: bool
    csrf_source: str
    strategy: str | None = None
    profile_username: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.strategy is not None


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
        self._session_ready = False
        self._auth_refresh_callback: Callable[[], Awaitable[None]] | None = None

    def set_auth_refresh_callback(
        self, callback: Callable[[], Awaitable[None]] | None
    ) -> None:
        self._auth_refresh_callback = callback

    def _absorb_response_cookies(self, headers: Any) -> None:
        cookies = parse_set_cookies(headers)
        if not cookies:
            return
        self.auth.update_runtime_cookies(cookies)
        if cookies.get("lsd"):
            self._lsd_token = cookies["lsd"]

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def ensure_session(self, *, force: bool = False) -> None:
        """Прогрев: получает csrftoken / mid / ig_did с главной страницы."""
        if self._session_ready and not force:
            return
        if force:
            self._session_ready = False

        referer = f"{self.settings.platform_base_url}/"
        try:
            await self.rate_limiter.wait()
            session = await self._get_session()
            headers = self.auth.build_headers(referer=referer, api_type="web")
            cookies = self.auth.build_cookies()

            async with session.get(
                referer,
                headers=headers,
                cookies=cookies,
                allow_redirects=True,
            ) as resp:
                body = await resp.text()
                self._absorb_response_cookies(resp.headers)
                html_tokens = parse_tokens_from_html(body)
                merged = merge_cookies(
                    parse_set_cookies(resp.headers),
                    html_tokens,
                )
                self.auth.update_runtime_cookies(merged)
                if merged.get("lsd"):
                    self._lsd_token = merged["lsd"]
                logger.info(
                    "session bootstrap: csrftoken=%s, cookies=%s",
                    "OK" if self.auth.get_csrf_token() else "MISSING",
                    list(merged.keys()),
                )
        except Exception as exc:
            logger.warning("session bootstrap failed: %s", exc)
        finally:
            self._session_ready = True

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
        extra_headers: dict[str, str] | None = None,
        for_graphql: bool = False,
    ) -> dict[str, Any] | str:
        async def _do_request() -> dict[str, Any] | str:
            await self.rate_limiter.wait()
            session = await self._get_session()
            headers = self.auth.build_headers(
                referer=referer,
                api_type=api_type,
                for_graphql=for_graphql,
            )
            if extra_headers:
                headers.update(extra_headers)
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
                self._absorb_response_cookies(resp.headers)
                if resp.status >= 400:
                    logger.warning(
                        "%s: HTTP %s — %s", label, resp.status, body[:300]
                    )
                    resp.raise_for_status()
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return body

        for auth_pass in range(2):
            try:
                return await with_retry(
                    _do_request,
                    max_retries=self.settings.max_retries,
                    backoff_sec=self.settings.retry_backoff_sec,
                    label=label,
                )
            except aiohttp.ClientResponseError as exc:
                if auth_pass == 0 and exc.status in (401, 403):
                    logger.info("%s: auth error, refreshing tokens", label)
                    if self._auth_refresh_callback:
                        await self._auth_refresh_callback()
                    else:
                        await self.ensure_session(force=True)
                    continue
                raise

        raise RuntimeError(f"{label}: auth refresh exhausted")

    async def graphql(
        self,
        doc_id: str,
        variables: dict[str, Any],
        *,
        referer: str | None = None,
        label: str = "graphql",
        method: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """GraphQL-запрос (POST по умолчанию)."""

        variables_json = json.dumps(variables, separators=(",", ":"))

        gql_headers = dict(extra_headers or {})
        if self._lsd_token:
            gql_headers["X-FB-LSD"] = self._lsd_token

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
                extra_headers=gql_headers,
                for_graphql=True,
            )
        else:
            result = await self._request(
                "GET",
                self.settings.graphql_endpoint,
                referer=referer,
                label=label,
                params={"doc_id": doc_id, "variables": variables_json},
                extra_headers=gql_headers,
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

    async def mobile_api_post(
        self,
        path: str,
        *,
        data: dict[str, Any],
        referer: str | None = None,
        label: str = "mobile_api",
    ) -> dict[str, Any]:
        """POST к i.instagram.com/api/v1."""
        url = f"{MOBILE_API_BASE}/{path.lstrip('/')}"
        result = await self._request(
            "POST",
            url,
            referer=referer,
            label=label,
            data={k: str(v) for k, v in data.items()},
            api_type="mobile",
        )
        if isinstance(result, dict):
            return result
        raise ValueError(f"Mobile API {label}: невалидный JSON")

    async def download_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "download",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> bytes:
        """Скачивает медиафайл (видео/аудио) по прямой ссылке."""
        data, _ = await self.download_media_bytes(
            url,
            referer=referer,
            label=label,
            max_bytes=max_bytes,
        )
        return data

    async def download_media_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "media_download",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> tuple[bytes, int]:
        """Скачивает бинарный файл с лимитом размера (для Telegram)."""
        await self.rate_limiter.wait()
        session = await self._get_session()
        ref = referer or f"{self.settings.platform_base_url}/"

        attempts: list[tuple[dict[str, str], dict[str, str] | None]] = [
            (
                {
                    "User-Agent": self.settings.user_agent,
                    "Accept": "*/*",
                    "Referer": ref,
                },
                None,
            ),
            (
                self.auth.build_headers(referer=ref, api_type="web"),
                self.auth.build_cookies(),
            ),
            (
                self.auth.build_headers(referer=ref, api_type="mobile"),
                self.auth.build_cookies(),
            ),
        ]

        last_error: Exception | None = None
        for headers, cookies in attempts:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    allow_redirects=True,
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning(
                            "%s: HTTP %s — %s",
                            label,
                            resp.status,
                            body[:120],
                        )
                        continue

                    cl_header = resp.headers.get("Content-Length")
                    if cl_header:
                        cl = int(cl_header)
                        if cl > max_bytes:
                            mb = cl / (1024 * 1024)
                            raise ValueError(
                                f"Файл слишком большой ({mb:.1f} МБ). "
                                f"Лимит Telegram — {max_bytes // (1024 * 1024)} МБ. "
                                "Скачайте по ссылке из отчёта."
                            )

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(262_144):
                        total += len(chunk)
                        if total > max_bytes:
                            mb = total / (1024 * 1024)
                            raise ValueError(
                                f"Файл слишком большой (>{mb:.1f} МБ). "
                                "Скачайте по ссылке из отчёта."
                            )
                        chunks.append(chunk)

                    data = b"".join(chunks)
                    if len(data) < 256:
                        logger.warning("%s: слишком маленький ответ", label)
                        continue
                    if data[:1] == b"<":
                        logger.warning("%s: ответ похож на HTML", label)
                        continue
                    return data, total
            except ValueError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("%s attempt failed: %s", label, exc)

        if last_error:
            raise last_error
        raise ValueError(f"Не удалось скачать файл: {url[:80]}")

    @staticmethod
    def _looks_like_image(data: bytes) -> bool:
        if len(data) < 128:
            return False
        if data[:2] == b"\xff\xd8":
            return True
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return True
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return True
        if data[:3] == b"GIF":
            return True
        return data[:1] != b"<"

    async def download_image_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "image_download",
    ) -> bytes:
        """Скачивает изображение (аватар) — несколько стратегий заголовков."""
        await self.rate_limiter.wait()
        session = await self._get_session()
        ref = referer or f"{self.settings.platform_base_url}/"

        attempts: list[tuple[dict[str, str], dict[str, str] | None]] = [
            (
                {
                    "User-Agent": self.settings.user_agent,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": ref,
                    "Sec-Fetch-Dest": "image",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "cross-site",
                },
                None,
            ),
            (
                self.auth.build_headers(referer=ref, api_type="web"),
                self.auth.build_cookies(),
            ),
            (
                self.auth.build_headers(referer=ref, api_type="mobile"),
                self.auth.build_cookies(),
            ),
        ]

        last_error: Exception | None = None
        for headers, cookies in attempts:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    allow_redirects=True,
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning(
                            "%s: HTTP %s — %s",
                            label,
                            resp.status,
                            body[:120],
                        )
                        continue
                    data = await resp.read()
                    if self._looks_like_image(data):
                        return data
                    logger.warning(
                        "%s: ответ не похож на изображение (%s байт)",
                        label,
                        len(data),
                    )
            except Exception as exc:
                last_error = exc
                logger.warning("%s attempt failed: %s", label, exc)

        if last_error:
            raise last_error
        raise ValueError(f"Не удалось скачать изображение: {url[:80]}")

    async def fetch_track_audio_asset(
        self,
        *,
        music_canonical_id: str | None = None,
        audio_asset_id: str | None = None,
        audio_cluster_id: str | None = None,
        referer: str | None = None,
    ) -> dict[str, Any] | None:
        """Запрашивает метаданные трека через clips/music/ (оригинальный m4a)."""
        data: dict[str, Any] = {
            "tab_type": "clips",
            "referrer_media_id": "",
        }
        if music_canonical_id:
            data["music_canonical_id"] = str(music_canonical_id)
        elif audio_asset_id or audio_cluster_id:
            aid = str(audio_asset_id or audio_cluster_id)
            data["audio_cluster_id"] = aid
            data["original_sound_audio_asset_id"] = aid
        else:
            return None

        try:
            result = await self.mobile_api_post(
                "clips/music/",
                data=data,
                referer=referer,
                label="track_audio",
            )
        except Exception as exc:
            logger.warning("track_audio API: %s", exc)
            return None

        metadata = safe_dict(result.get("metadata"))
        for key in ("music_info", "original_sound_info"):
            block = safe_dict(metadata.get(key))
            if key == "music_info":
                block = safe_dict(block.get("music_asset_info")) or block
            if block:
                return block

        for path in (
            ("metadata", "music_info", "music_asset_info"),
            ("metadata", "original_sound_info"),
        ):
            block = dig(result, *path)
            if isinstance(block, dict) and block:
                return block

        return None

    async def fetch_paginated(
        self,
        doc_id: str,
        variables: dict[str, Any],
        *,
        edges_path: list[str],
        referer: str | None = None,
        label: str = "paginated",
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Универсальная пагинация: обходит все страницы до has_next_page=False
        или достижения max_pagination_pages.
        """
        all_edges: list[dict[str, Any]] = []
        cursor: str | None = None
        base_vars = dict(variables)

        page_limit = max_pages or self.settings.max_pagination_pages
        for page in range(page_limit):
            vars_page = {**base_vars}
            if cursor:
                vars_page["after"] = cursor

            data = await self.graphql(
                doc_id,
                vars_page,
                referer=referer,
                label=f"{label}_page_{page}",
                method="POST",
            )

            node: Any = data.get("data", data)
            for key in edges_path:
                node = safe_dict(node).get(key)

            node = safe_dict(node)
            edges = node.get("edges", []) or []
            all_edges.extend(edges)

            page_info = safe_dict(node.get("page_info"))
            if not page_info.get("has_next_page"):
                break
            cursor = page_info.get("end_cursor")
            if not cursor:
                break

        return all_edges

    def _profile_referer(self, username: str) -> str:
        return f"{self.settings.platform_base_url}/{username}/"

    @staticmethod
    def _polaris_profile_variables(user_id: str) -> dict[str, Any]:
        return {
            "enable_integrity_filters": True,
            "id": str(user_id),
            "render_surface": "PROFILE",
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": False,
        }

    def _csrf_source_label(self) -> str:
        return self.auth.csrf_source_label()

    async def _probe_web_profile_info(
        self, username: str, referer: str
    ) -> tuple[dict[str, Any] | None, str]:
        """web_profile_info с текстом ошибки для диагностики."""
        if not self.auth.session_id:
            return None, "sessionid не задан (SESSION_TOKEN)"
        if not self.auth.get_csrf_token():
            return None, "csrftoken отсутствует — дождитесь bootstrap или добавьте CSRF_TOKEN"
        last_err = ""
        for auth_pass in range(2):
            try:
                await self.rate_limiter.wait()
                session = await self._get_session()
                headers = self.auth.build_web_api_headers(referer)
                cookies = self.auth.build_cookies()
                async with session.get(
                    f"{self.settings.platform_base_url}/api/v1/users/web_profile_info/",
                    params={"username": username},
                    headers=headers,
                    cookies=cookies,
                ) as resp:
                    body = await resp.text()
                    self._absorb_response_cookies(resp.headers)
                    if resp.status >= 400:
                        last_err = f"HTTP {resp.status}: {body[:200]}"
                        if (
                            auth_pass == 0
                            and is_instagram_auth_error(resp.status, body)
                        ):
                            if self._auth_refresh_callback:
                                await self._auth_refresh_callback()
                            else:
                                await self.ensure_session(force=True)
                            continue
                        return None, last_err
                    payload = json.loads(body)
                if not isinstance(payload, dict):
                    return None, "невалидный JSON"
                if payload.get("status") == "fail":
                    msg = str(payload.get("message") or "fail")
                    last_err = f"API: {msg}"
                    if auth_pass == 0 and is_instagram_auth_error(200, body, payload):
                        if self._auth_refresh_callback:
                            await self._auth_refresh_callback()
                        else:
                            await self.ensure_session(force=True)
                        continue
                    return None, last_err
                result = from_web_profile_info(payload)
                if result:
                    return result, ""
                return None, "пустой user в ответе"
            except Exception as exc:
                last_err = str(exc)
                if auth_pass == 0:
                    if self._auth_refresh_callback:
                        await self._auth_refresh_callback()
                    else:
                        await self.ensure_session(force=True)
                    continue
                return None, last_err
        return None, last_err or "auth refresh не помог"

    async def _fetch_profile_via_web_api(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        result, err = await self._probe_web_profile_info(username, referer)
        if result:
            logger.info("profile: web API OK для %s", username)
            return result
        if err:
            logger.warning("profile web API failed для %s: %s", username, err)
        return None

    async def _try_profile_strategy(
        self,
        label: str,
        strategy: Callable[[str, str], Awaitable[dict[str, Any] | None]],
        username: str,
        referer: str,
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            result = await strategy(username, referer)
            if result:
                return result, ""
            return None, "пустой ответ"
        except Exception as exc:
            return None, str(exc)

    @staticmethod
    def _profile_username_from_data(data: dict[str, Any]) -> str | None:
        user = dig(data, "data", "user")
        if isinstance(user, dict):
            return user.get("username")
        return None

    async def verify_instagram_session(
        self, username: str = "instagram"
    ) -> InstagramSessionVerify:
        """
        Проверка сессии — тот же каскад, что fetch_web_profile, с деталями ошибок.
        """
        self._session_ready = False
        await self.ensure_session()
        referer = self._profile_referer(username)
        csrf = self.auth.get_csrf_token()
        verify = InstagramSessionVerify(
            session_id_ok=bool(self.auth.session_id),
            csrf_ok=bool(csrf),
            csrf_source=self._csrf_source_label(),
        )

        strategies: list[tuple[str, Callable[[str, str], Awaitable[dict[str, Any] | None]]]] = [
            ("web_profile_info", self._fetch_profile_via_web_api),
            ("mobile web_profile_info", self._fetch_profile_via_mobile),
            ("usernameinfo", self._fetch_profile_via_usernameinfo),
            ("Polaris GraphQL", self._fetch_profile_via_gql_polaris),
            ("legacy GraphQL", self._fetch_profile_via_gql_legacy),
            ("HTML", self._fetch_profile_via_html),
        ]

        probe, web_err = await self._probe_web_profile_info(username, referer)
        if probe:
            verify.strategy = "web_profile_info"
            verify.profile_username = self._profile_username_from_data(probe) or username
            return verify
        if web_err:
            verify.errors.append(f"web_profile_info: {web_err}")

        for label, strategy in strategies[1:]:
            result, err = await self._try_profile_strategy(
                label, strategy, username, referer
            )
            if result:
                verify.strategy = label
                verify.profile_username = (
                    self._profile_username_from_data(result) or username
                )
                return verify
            if err:
                verify.errors.append(f"{label}: {err}")

        return verify

    async def _fetch_profile_via_mobile(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            payload = await self.mobile_api_get(
                "/users/web_profile_info/",
                referer=referer,
                label="profile_mobile_web",
                params={"username": username},
            )
            result = from_web_profile_info(payload)
            if result:
                logger.info("profile: mobile web_profile_info OK для %s", username)
                return result
        except Exception as exc:
            logger.warning("profile mobile web_profile_info failed для %s: %s", username, exc)
        return None

    async def _fetch_profile_via_usernameinfo(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            payload = await self.mobile_api_get(
                f"/users/{username}/usernameinfo/",
                referer=referer,
                label="profile_usernameinfo",
            )
            result = from_usernameinfo(payload)
            if result:
                logger.info("profile: usernameinfo OK для %s", username)
                return result
        except Exception as exc:
            logger.warning("profile usernameinfo failed для %s: %s", username, exc)
        return None

    async def _fetch_profile_via_gql_polaris(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            search = await self.graphql(
                DOC_IDS["user_profile_by_username"],
                {"hasQuery": True, "query": username},
                referer=referer,
                label="profile_search_gql",
                method="POST",
            )
            user_id = find_user_id_in_search(search, username)
            if not user_id:
                return None

            payload = await self.graphql(
                DOC_IDS["user_profile"],
                self._polaris_profile_variables(user_id),
                referer=referer,
                label="profile_polaris_gql",
                method="POST",
                extra_headers={
                    "X-FB-Friendly-Name": "PolarisProfilePageContentQuery",
                },
            )
            result = from_gql_profile(payload)
            if result:
                logger.info("profile: Polaris GraphQL OK для %s", username)
                return result
        except Exception as exc:
            logger.warning("profile Polaris GraphQL failed для %s: %s", username, exc)
        return None

    async def _fetch_profile_via_gql_legacy(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        for method in ("POST", "GET"):
            try:
                payload = await self.graphql(
                    DOC_IDS["user_profile_legacy"],
                    {"username": username, "include_reel": True},
                    referer=referer,
                    label=f"profile_legacy_{method.lower()}",
                    method=method,
                )
                result = from_gql_profile(payload)
                if result:
                    logger.info(
                        "profile: legacy GraphQL %s OK для %s",
                        method,
                        username,
                    )
                    return result
            except Exception as exc:
                logger.warning(
                    "profile legacy %s failed для %s: %s",
                    method,
                    username,
                    exc,
                )
        return None

    async def _fetch_profile_via_html(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        try:
            html = await self._request(
                "GET",
                referer,
                referer=referer,
                label="profile_html",
            )
            if not isinstance(html, str):
                return None

            if is_profile_not_found_html(html, username):
                logger.warning("profile HTML: маркеры 404 для %s", username)
                return None

            for match in re.finditer(
                r'<script[^>]*type="application/json"[^>]*>(\{.+?\})</script>',
                html,
                re.DOTALL,
            ):
                try:
                    blob = json.loads(match.group(1))
                    result = from_embedded_profile_json(blob, username)
                    if result:
                        logger.info("profile: HTML JSON OK для %s", username)
                        return result
                except json.JSONDecodeError:
                    continue

            result = from_html_meta(html, username)
            if result:
                logger.info("profile: HTML meta OK для %s", username)
                return result
        except Exception as exc:
            logger.warning("profile HTML failed для %s: %s", username, exc)
        return None

    async def _profile_cascade(
        self, username: str, referer: str
    ) -> dict[str, Any] | None:
        """Последовательный каскад — меньше 429 и понятнее логи."""
        strategies = (
            self._fetch_profile_via_gql_polaris,
            self._fetch_profile_via_gql_legacy,
            self._fetch_profile_via_html,
        )
        for strategy in strategies:
            try:
                result = await strategy(username, referer)
                if result:
                    return result
            except Exception as exc:
                logger.warning(
                    "profile %s: %s → %s",
                    username,
                    strategy.__name__,
                    exc,
                )
        return None

    async def fetch_web_profile(self, username: str) -> dict[str, Any]:
        """
        Профиль — каскад стратегий с прогревом сессии.
        """
        await self.ensure_session()
        referer = self._profile_referer(username)
        logger.info(
            "profile %s → csrf=%s",
            username,
            "OK" if self.auth.get_csrf_token() else "MISSING",
        )

        fast = await first_success([
            lambda: self._fetch_profile_via_web_api(username, referer),
            lambda: self._fetch_profile_via_mobile(username, referer),
            lambda: self._fetch_profile_via_usernameinfo(username, referer),
        ])
        if fast:
            return fast

        result = await self._profile_cascade(username, referer)
        if result:
            return result

        if not self.auth.get_csrf_token():
            raise ValueError(
                f"Профиль @{username} недоступен: не удалось получить csrftoken. "
                "Добавьте CSRF_TOKEN в Railway (cookie csrftoken из браузера) "
                "и обновите SESSION_TOKEN."
            )

        raise ValueError(
            f"Профиль @{username} не найден или недоступен. "
            "Проверьте ник, обновите SESSION_TOKEN и CSRF_TOKEN (/session), "
            "или аккаунт может быть приватным."
        )

    async def _fetch_user_posts_mobile(
        self, user_id: str, *, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Посты через mobile feed/user (без GraphQL)."""
        edges: list[dict[str, Any]] = []
        max_id: str | None = None
        page_limit = max_pages or self.settings.max_pagination_pages

        for page in range(page_limit):
            params: dict[str, str] = {
                "count": str(self.settings.pagination_page_size),
            }
            if max_id:
                params["max_id"] = max_id

            data = await self.mobile_api_get(
                f"/feed/user/{user_id}/",
                label=f"user_posts_mobile_p{page}",
                params=params,
            )
            items = data.get("items") or []
            for item in items:
                edges.append({"node": item})

            max_id = data.get("next_max_id")
            if not max_id or not items:
                break

        return edges

    async def fetch_user_posts(
        self, user_id: str, *, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Публикации профиля — GraphQL POST, fallback mobile feed."""
        try:
            edges = await self.fetch_paginated(
                DOC_IDS["user_posts"],
                {
                    "id": user_id,
                    "first": self.settings.pagination_page_size,
                },
                edges_path=["user", "edge_owner_to_timeline_media"],
                label="user_posts",
                max_pages=max_pages,
            )
            if edges:
                return edges
        except Exception as exc:
            logger.warning("user_posts GraphQL: %s", exc)

        try:
            edges = await self._fetch_user_posts_mobile(
                user_id, max_pages=max_pages
            )
            if edges:
                logger.info("user_posts: mobile feed OK, %d items", len(edges))
            return edges
        except Exception as exc:
            logger.warning("user_posts mobile: %s", exc)
            return []

    async def fetch_user_reels(
        self, user_id: str, *, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Reels профиля."""
        try:
            return await self.fetch_paginated(
                DOC_IDS["user_reels"],
                {"id": user_id, "first": self.settings.pagination_page_size},
                edges_path=["user", "edge_felix_video_timeline"],
                label="user_reels",
                max_pages=max_pages,
            )
        except Exception as exc:
            logger.warning("user_reels недоступны: %s", exc)
            return []

    async def fetch_user_tagged(
        self, user_id: str, *, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Публикации, где отмечен пользователь."""
        if max_pages == 0:
            return []
        try:
            return await self.fetch_paginated(
                DOC_IDS["user_tagged"],
                {"id": user_id, "first": self.settings.pagination_page_size},
                edges_path=["user", "edge_user_to_photos_of_you"],
                label="user_tagged",
                max_pages=max_pages,
            )
        except Exception as exc:
            logger.warning("user_tagged GraphQL: %s", exc)

        try:
            return await self._fetch_tagged_mobile(user_id)
        except Exception as exc:
            logger.warning("user_tagged mobile: %s", exc)
            return []

    async def _fetch_tagged_mobile(self, user_id: str) -> list[dict[str, Any]]:
        """Fallback: отметки через mobile API."""
        edges: list[dict[str, Any]] = []
        max_id: str | None = None

        for page in range(self.settings.max_pagination_pages):
            params: dict[str, str] = {}
            if max_id:
                params["max_id"] = max_id

            data = await self.mobile_api_get(
                f"/usertags/{user_id}/feed/",
                label=f"tagged_mobile_p{page}",
                params=params or None,
            )
            items = data.get("items") or []
            for item in items:
                edges.append({"node": item})

            max_id = data.get("next_max_id")
            if not max_id or not items:
                break

        return edges

    async def fetch_user_highlights(self, user_id: str) -> list[dict[str, Any]]:
        """Список актуального (highlights) профиля."""
        try:
            data = await self.graphql(
                DOC_IDS["user_highlights"],
                {
                    "user_id": user_id,
                    "include_chaining": False,
                    "include_reel": False,
                    "include_highlight_reels": True,
                },
                label="user_highlights",
            )
            user = safe_dict(data.get("data", {})).get("user")
            return safe_dict(safe_dict(user).get("edge_highlight_reels")).get(
                "edges", []
            ) or []
        except Exception as exc:
            logger.warning("user_highlights недоступны: %s", exc)
            return []

    async def fetch_highlight_items(
        self, highlight_id: str
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Элементы одного highlight. Возвращает (title, items)."""
        try:
            data = await self.fetch_highlight(highlight_id)
            connection = safe_dict(
                data.get("data", {})
            ).get("xdt_api__v1__feed__reels_media__connection", {})
            for edge in connection.get("edges", []) or []:
                node = safe_dict(edge.get("node"))
                if str(node.get("id", "")) == highlight_id:
                    return node.get("title"), node.get("items", []) or []
        except Exception as exc:
            logger.warning("highlight %s: %s", highlight_id, exc)
        return None, []

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

        result = await first_success([
            lambda: self._fetch_media_via_rest(shortcode, media_id, referer),
            lambda: self._fetch_media_via_graphql(shortcode, media_id, referer),
            lambda: self._fetch_media_via_html(shortcode, referer),
        ])
        if result:
            return result

        raise ValueError(
            f"Публикация {shortcode} недоступна. "
            "Проверьте SESSION_TOKEN и CSRF_TOKEN — возможно, сессия истекла."
        )

    @staticmethod
    def _rest_comment_to_edge(comment: dict[str, Any]) -> dict[str, Any]:
        user = comment.get("user") or {}
        return {
            "node": {
                "id": str(comment.get("pk", "")),
                "text": comment.get("text", ""),
                "created_at": comment.get("created_at"),
                "edge_liked_by": {"count": comment.get("comment_like_count", 0)},
                "owner": {
                    "id": str(user.get("pk", "")),
                    "username": user.get("username"),
                    "profile_pic_url": user.get("profile_pic_url"),
                },
            }
        }

    async def _fetch_comments_rest(
        self, media_id: str, shortcode: str
    ) -> list[dict[str, Any]]:
        referer = f"{self.settings.platform_base_url}/p/{shortcode}/"
        edges: list[dict[str, Any]] = []
        max_id: str | None = None

        for page in range(self.settings.max_comment_pages):
            params: dict[str, str] = {
                "can_support_threading": "true",
                "permalink_enabled": "false",
            }
            if max_id:
                params["max_id"] = max_id

            data = await self.mobile_api_get(
                f"/media/{media_id}/comments/",
                referer=referer,
                label=f"comments_rest_p{page}",
                params=params,
            )
            comments = data.get("comments") or []
            for comment in comments:
                edges.append(self._rest_comment_to_edge(comment))
                # Ответы на комментарий
                for child in comment.get("preview_child_comments") or []:
                    edges.append(self._rest_comment_to_edge(child))

            max_id = data.get("next_max_id")
            if not max_id or not comments:
                break

        return edges

    async def _fetch_comments_graphql(
        self, media_id: str, shortcode: str
    ) -> list[dict[str, Any]]:
        return await self.fetch_paginated(
            DOC_IDS["media_comments"],
            {
                "shortcode": shortcode,
                "first": self.settings.comments_page_size,
            },
            edges_path=["shortcode_media", "edge_media_to_parent_comment"],
            referer=f"{self.settings.platform_base_url}/p/{shortcode}/",
            label="media_comments_gql",
        )

    async def fetch_media_comments(
        self, media_id: str, shortcode: str
    ) -> list[dict[str, Any]]:
        """Комментарии — REST + GraphQL параллельно, берём более полный набор."""
        rest_task = asyncio.create_task(
            self._fetch_comments_rest(media_id, shortcode)
        )
        gql_task = asyncio.create_task(
            self._fetch_comments_graphql(media_id, shortcode)
        )
        rest, gql = await asyncio.gather(rest_task, gql_task, return_exceptions=True)

        rest_edges = rest if isinstance(rest, list) else []
        gql_edges = gql if isinstance(gql, list) else []

        if len(rest_edges) >= len(gql_edges):
            logger.info("comments: REST %d", len(rest_edges))
            return rest_edges
        logger.info("comments: GraphQL %d", len(gql_edges))
        return gql_edges

    async def fetch_media_likers(
        self, media_id: str, shortcode: str
    ) -> list[dict[str, Any]]:
        """Список лайкнувших (первые страницы)."""
        referer = f"{self.settings.platform_base_url}/p/{shortcode}/"
        likers: list[dict[str, Any]] = []
        max_id: str | None = None

        for page in range(min(5, self.settings.max_comment_pages)):
            params: dict[str, str] = {}
            if max_id:
                params["max_id"] = max_id
            try:
                data = await self.mobile_api_get(
                    f"/media/{media_id}/likers/",
                    referer=referer,
                    label=f"likers_p{page}",
                    params=params or None,
                )
            except Exception as exc:
                logger.warning("likers недоступны: %s", exc)
                break

            users = data.get("users") or []
            likers.extend(users)
            max_id = data.get("next_max_id")
            if not max_id or not users:
                break

        return likers

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