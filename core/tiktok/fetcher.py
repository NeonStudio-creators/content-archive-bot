"""
Сетевой слой TikTok: HTML-профили и mirror API для видео.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from config import Settings
from core.tiktok.resolver import TikTokLinkResolver
from utils.rate_limit import QuietRateLimiter
from utils.retry import with_retry

logger = logging.getLogger(__name__)

TIKWM_API = "https://www.tikwm.com/api/"


@dataclass
class TikTokFetcher:
    settings: Settings
    rate_limiter: QuietRateLimiter
    _session: aiohttp.ClientSession | None = field(default=None, init=False)
    _bootstrapped: bool = field(default=False, init=False)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar()
            timeout = aiohttp.ClientTimeout(total=60, connect=15)
            self._session = aiohttp.ClientSession(
                cookie_jar=jar,
                timeout=timeout,
            )
        return self._session

    def _headers(self, *, referer: str | None = None, accept: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": accept or "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        cookie = self.settings.tiktok_cookie.strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    async def ensure_session(self) -> None:
        if self._bootstrapped:
            return
        session = await self._get_session()
        try:
            await self.rate_limiter.wait()
            async with session.get(
                f"{self.settings.tiktok_base_url}/",
                headers=self._headers(),
                allow_redirects=True,
            ) as resp:
                await resp.text()
            logger.info("tiktok bootstrap: status=%s", resp.status)
        except Exception as exc:
            logger.warning("tiktok bootstrap failed: %s", exc)
        finally:
            self._bootstrapped = True

    async def resolve_short_url(self, url: str) -> str:
        """Разворачивает vm/vt/t ссылки в канонический URL."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host not in {"vm.tiktok.com", "vt.tiktok.com", "www.tiktok.com", "tiktok.com"}:
            return TikTokLinkResolver.clean_url(url)
        if "/@" in parsed.path and "/video/" in parsed.path:
            return TikTokLinkResolver.clean_url(url)

        session = await self._get_session()
        await self.rate_limiter.wait()
        async with session.get(
            url,
            headers=self._headers(referer=f"{self.settings.tiktok_base_url}/"),
            allow_redirects=True,
        ) as resp:
            final = str(resp.url)
            return TikTokLinkResolver.clean_url(final)

    @staticmethod
    def _parse_universal(html: str) -> dict[str, Any]:
        match = re.search(
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(\{.*?\})</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return {}
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
        return payload.get("__DEFAULT_SCOPE__", {})

    async def fetch_profile_html(self, username: str) -> dict[str, Any]:
        await self.ensure_session()
        referer = f"{self.settings.tiktok_base_url}/@{username}"
        session = await self._get_session()

        async def _load() -> dict[str, Any]:
            await self.rate_limiter.wait()
            async with session.get(
                referer,
                headers=self._headers(referer=f"{self.settings.tiktok_base_url}/"),
                allow_redirects=True,
            ) as resp:
                html = await resp.text()
                if resp.status >= 400:
                    raise ValueError(f"TikTok HTTP {resp.status}")
                if len(html) < 5000 and "__UNIVERSAL_DATA_FOR_REHYDRATION__" not in html:
                    raise ValueError(
                        f"Профиль @{username} недоступен (WAF). "
                        "Добавьте TIKTOK_COOKIE из браузера."
                    )
                scope = self._parse_universal(html)
                if not scope.get("webapp.user-detail"):
                    raise ValueError(f"Профиль @{username} не найден")
                return {"scope": scope, "html": html}

        return await with_retry(
            _load,
            max_retries=self.settings.max_retries,
            backoff_sec=self.settings.retry_backoff_sec,
            label=f"tiktok_profile_{username}",
        )

    async def fetch_video_via_html(self, url: str) -> dict[str, Any] | None:
        await self.ensure_session()
        session = await self._get_session()
        await self.rate_limiter.wait()
        async with session.get(
            url,
            headers=self._headers(referer=f"{self.settings.tiktok_base_url}/"),
            allow_redirects=True,
        ) as resp:
            html = await resp.text()
        if "__UNIVERSAL_DATA_FOR_REHYDRATION__" not in html:
            return None
        scope = self._parse_universal(html)
        detail = scope.get("webapp.video-detail") or {}
        item = (detail.get("itemInfo") or {}).get("itemStruct")
        if isinstance(item, dict) and item.get("id"):
            return item
        return None

    async def fetch_video_mirror(self, url: str) -> dict[str, Any]:
        """Основной источник метаданных видео (tikwm)."""
        await self.ensure_session()
        session = await self._get_session()
        api_url = f"{TIKWM_API}?url={quote(url, safe='')}&hd=1"

        async def _call() -> dict[str, Any]:
            await self.rate_limiter.wait()
            async with session.get(
                api_url,
                headers=self._headers(accept="application/json"),
            ) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise ValueError(f"Mirror API HTTP {resp.status}")
                payload = json.loads(body)
                if payload.get("code") != 0:
                    msg = payload.get("msg") or "mirror error"
                    if "limit" in str(msg).lower():
                        await self.rate_limiter.wait()
                        raise aiohttp.ClientError(msg)
                    raise ValueError(f"TikTok mirror: {msg}")
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError("TikTok mirror: пустой ответ")
                return data

        return await with_retry(
            _call,
            max_retries=self.settings.max_retries,
            backoff_sec=self.settings.retry_backoff_sec,
            label="tiktok_mirror",
        )

    async def fetch_video(self, url: str) -> dict[str, Any]:
        canonical = await self.resolve_short_url(url)
        item = await self.fetch_video_via_html(canonical)
        if item:
            item["_source"] = "html"
            return item
        data = await self.fetch_video_mirror(canonical)
        data["_source"] = "mirror"
        data["_canonical_url"] = canonical
        return data

    async def download_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "tiktok_download",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> bytes:
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
        label: str = "tiktok_media",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> tuple[bytes, int]:
        await self.rate_limiter.wait()
        session = await self._get_session()
        ref = referer or f"{self.settings.tiktok_base_url}/"

        async with session.get(
            url,
            headers=self._headers(referer=ref, accept="*/*"),
            allow_redirects=True,
        ) as resp:
            if resp.status >= 400:
                raise ValueError(f"Скачивание HTTP {resp.status}")

            cl_header = resp.headers.get("Content-Length")
            if cl_header and int(cl_header) > max_bytes:
                mb = int(cl_header) / (1024 * 1024)
                raise ValueError(
                    f"Файл слишком большой ({mb:.1f} МБ). "
                    f"Лимит Telegram — {max_bytes // (1024 * 1024)} МБ."
                )

            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(262_144):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Файл слишком большой для Telegram")
                chunks.append(chunk)

            data = b"".join(chunks)
            if len(data) < 256:
                raise ValueError("Пустой файл при скачивании")
            return data, total

    async def download_image_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "tiktok_avatar",
    ) -> bytes:
        return await self.download_bytes(
            url,
            referer=referer,
            label=label,
            max_bytes=8 * 1024 * 1024,
        )