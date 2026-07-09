"""
Сетевой слой TikTok: sessionid + HTML/API, mirror как fallback.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import aiohttp

from config import Settings
from core.session_bootstrap import merge_cookies, parse_set_cookies
from core.tiktok.auth import TikTokSessionAuthManager
from core.tiktok.cdn_urls import is_restricted_download_url, sort_download_urls
from core.tiktok.resolver import TikTokLinkResolver
from utils.rate_limit import QuietRateLimiter
from utils.retry import with_retry

logger = logging.getLogger(__name__)

TIKWM_API = "https://www.tikwm.com/api/"


@dataclass
class TikTokFetcher:
    settings: Settings
    auth: TikTokSessionAuthManager
    rate_limiter: QuietRateLimiter
    _session: aiohttp.ClientSession | None = field(default=None, init=False)
    _bootstrapped: bool = field(default=False, init=False)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _request_kwargs(
        self,
        *,
        referer: str | None = None,
        accept: str | None = None,
        for_api: bool = False,
    ) -> dict[str, Any]:
        return {
            "headers": self.auth.build_headers(
                referer=referer,
                accept=accept,
                for_api=for_api,
            ),
            "cookies": self.auth.build_cookies(),
        }

    async def ensure_session(self) -> None:
        if self._bootstrapped:
            return
        session = await self._get_session()
        try:
            await self.rate_limiter.wait()
            kw = self._request_kwargs()
            async with session.get(
                f"{self.settings.tiktok_base_url}/",
                allow_redirects=True,
                **kw,
            ) as resp:
                await resp.text()
                merged = merge_cookies(
                    parse_set_cookies(resp.headers),
                    self.auth.build_cookies(),
                )
                self.auth.update_runtime_cookies(merged)
            logger.info(
                "tiktok bootstrap: sessionid=%s, cookies=%s",
                "OK" if self.auth.is_configured() else "MISSING",
                list(self.auth.build_cookies().keys()),
            )
        except Exception as exc:
            logger.warning("tiktok bootstrap failed: %s", exc)
        finally:
            self._bootstrapped = True

    def normalize_video_url(
        self,
        url: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> str:
        """Приводит ссылку к формату, который принимают TikTok API и mirror."""
        clean = TikTokLinkResolver.clean_url(url)
        vid = video_id or TikTokLinkResolver.extract_video_id(clean)
        if vid:
            path_user_match = re.search(r"/@([^/]+)/video/", clean)
            user = username or (path_user_match.group(1) if path_user_match else None)
            if user:
                return TikTokLinkResolver.video_page_url(vid, user)
            if "/video/" not in clean and "@" not in clean:
                return TikTokLinkResolver.video_page_url(vid, prefer_mobile=True)
        return clean

    async def resolve_short_url(
        self,
        url: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> str:
        """Разворачивает vm/vt/t ссылки в канонический URL."""
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        short_hosts = {"vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com"}
        tiktok_hosts = short_hosts | {"tiktok.com", "www.tiktok.com"}

        if host not in tiktok_hosts and "tiktok.com" not in host:
            return self.normalize_video_url(
                url, video_id=video_id, username=username
            )

        vid = video_id or TikTokLinkResolver.extract_video_id(url)
        if "/@" in parsed.path and vid:
            return self.normalize_video_url(url, video_id=vid, username=username)

        if host in short_hosts or parsed.path.startswith("/t/"):
            await self.ensure_session()
            session = await self._get_session()
            await self.rate_limiter.wait()
            kw = self._request_kwargs(referer=f"{self.settings.tiktok_base_url}/")
            async with session.get(url, allow_redirects=True, **kw) as resp:
                final = TikTokLinkResolver.clean_url(str(resp.url))
                self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
                final_vid = TikTokLinkResolver.extract_video_id(final) or vid
                if final_vid:
                    path_user = re.search(r"/@([^/]+)/video/", final)
                    user = username or (path_user.group(1) if path_user else None)
                    return self.normalize_video_url(
                        final,
                        video_id=final_vid,
                        username=user,
                    )
                if vid:
                    return TikTokLinkResolver.video_page_url(
                        vid, username, prefer_mobile=True
                    )
                return final

        return self.normalize_video_url(url, video_id=vid, username=username)

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
            kw = self._request_kwargs(referer=f"{self.settings.tiktok_base_url}/")
            async with session.get(referer, allow_redirects=True, **kw) as resp:
                html = await resp.text()
                self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
                if resp.status >= 400:
                    raise ValueError(f"TikTok HTTP {resp.status}")
                if len(html) < 5000 and "__UNIVERSAL_DATA_FOR_REHYDRATION__" not in html:
                    raise ValueError(
                        f"Профиль @{username} недоступен (WAF). "
                        "Добавьте TIKTOK_SESSION_TOKEN (cookie sessionid с tiktok.com)."
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
        kw = self._request_kwargs(referer=f"{self.settings.tiktok_base_url}/")
        async with session.get(url, allow_redirects=True, **kw) as resp:
            html = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
        if "__UNIVERSAL_DATA_FOR_REHYDRATION__" not in html:
            return None
        scope = self._parse_universal(html)
        detail = scope.get("webapp.video-detail") or {}
        item = (detail.get("itemInfo") or {}).get("itemStruct")
        if isinstance(item, dict) and item.get("id"):
            return item
        return None

    def _api_base_params(self) -> dict[str, str]:
        cookies = self.auth.build_cookies()
        ms = cookies.get("msToken", "")
        return {
            "aid": "1988",
            "app_language": "en",
            "app_name": "tiktok_web",
            "browser_language": "en-US",
            "browser_name": "Mozilla",
            "browser_online": "true",
            "browser_platform": "Win32",
            "browser_version": self.settings.user_agent,
            "channel": "tiktok_web",
            "cookie_enabled": "true",
            "device_platform": "web_pc",
            "focus_state": "true",
            "from_page": "video",
            "is_fullscreen": "false",
            "is_page_visible": "true",
            "language": "en",
            "os": "windows",
            "priority_region": "",
            "referer": "",
            "region": "US",
            "screen_height": "1080",
            "screen_width": "1920",
            "tz_name": "UTC",
            "webcast_language": "en",
            "msToken": ms,
        }

    async def fetch_video_via_api(self, item_id: str, referer: str) -> dict[str, Any] | None:
        if not self.auth.is_configured():
            return None
        await self.ensure_session()
        params = {**self._api_base_params(), "itemId": str(item_id)}
        api_url = (
            f"{self.settings.tiktok_base_url}/api/item/detail/?"
            f"{urlencode(params, quote_via=quote)}"
        )
        session = await self._get_session()
        await self.rate_limiter.wait()
        kw = self._request_kwargs(referer=referer, for_api=True)
        async with session.get(api_url, **kw) as resp:
            body = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
        if not body or not body.strip():
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        item = (payload.get("itemInfo") or {}).get("itemStruct")
        if isinstance(item, dict) and item.get("id"):
            return item
        return None

    def _mirror_url_candidates(
        self,
        url: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> list[str]:
        vid = video_id or TikTokLinkResolver.extract_video_id(url)
        candidates: list[str] = []
        seen: set[str] = set()

        def add(u: str | None) -> None:
            if not u or u in seen:
                return
            seen.add(u)
            candidates.append(u)

        add(url)
        if vid:
            if username:
                add(TikTokLinkResolver.video_page_url(vid, username))
            add(TikTokLinkResolver.video_page_url(vid, prefer_mobile=True))
        return candidates

    async def fetch_video_mirror(
        self,
        url: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        """Fallback через mirror API (несколько форматов URL)."""
        await self.ensure_session()
        session = await self._get_session()
        candidates = self._mirror_url_candidates(
            url, video_id=video_id, username=username
        )
        errors: list[str] = []

        for candidate in candidates:
            api_url = f"{TIKWM_API}?url={quote(candidate, safe='')}&hd=1"
            try:
                await self.rate_limiter.wait()
                async with session.get(
                    api_url,
                    headers=self.auth.build_headers(accept="application/json"),
                ) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        errors.append(f"{candidate}: HTTP {resp.status}")
                        continue
                    payload = json.loads(body)
                    if payload.get("code") != 0:
                        msg = payload.get("msg") or "mirror error"
                        if "limit" in str(msg).lower():
                            await self.rate_limiter.wait()
                        errors.append(f"{candidate}: {msg}")
                        continue
                    data = payload.get("data")
                    if isinstance(data, dict) and data.get("id"):
                        return data
                    errors.append(f"{candidate}: пустой ответ")
            except json.JSONDecodeError as exc:
                errors.append(f"{candidate}: JSON {exc}")
            except aiohttp.ClientError as exc:
                errors.append(f"{candidate}: {exc}")

        hint = (
            " Проверьте TIKTOK_SESSION_TOKEN (sessionid с tiktok.com)."
            if not self.auth.is_configured()
            else ""
        )
        raise ValueError(
            "Не удалось получить видео TikTok. "
            + ("; ".join(errors[:2]) if errors else "mirror недоступен")
            + hint
        )

    async def _enrich_mirror_downloads(
        self,
        item: dict[str, Any],
        canonical: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> None:
        """
        HTML/API отдают playAddr на webapp-prime CDN (403 с бэкенда).
        Дополняем рабочими play/hdplay с tikwm.
        """
        try:
            mirror = await self.fetch_video_mirror(
                canonical,
                video_id=video_id,
                username=username,
            )
        except Exception as exc:
            logger.warning("mirror enrich failed: %s", exc)
            return

        for key in (
            "play",
            "hdplay",
            "wmplay",
            "cover",
            "size",
            "hd_size",
            "wm_size",
        ):
            if mirror.get(key):
                item[key] = mirror[key]
        item["_mirror_enriched"] = True

    async def fetch_video(
        self,
        url: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        canonical = await self.resolve_short_url(
            url, video_id=video_id, username=username
        )
        item_id = video_id or TikTokLinkResolver.extract_video_id(canonical)

        item = await self.fetch_video_via_html(canonical)
        if item:
            item["_source"] = "html"
            item["_canonical_url"] = canonical
            await self._enrich_mirror_downloads(
                item,
                canonical,
                video_id=item_id,
                username=username,
            )
            return item

        if item_id:
            api_item = await self.fetch_video_via_api(item_id, canonical)
            if api_item:
                api_item["_source"] = "api"
                api_item["_canonical_url"] = canonical
                await self._enrich_mirror_downloads(
                    api_item,
                    canonical,
                    video_id=item_id,
                    username=username,
                )
                return api_item

        if not self.auth.is_configured():
            logger.warning("TIKTOK_SESSION_TOKEN не задан — mirror fallback")
        data = await self.fetch_video_mirror(
            canonical,
            video_id=item_id,
            username=username,
        )
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

    def _cdn_download_attempts(
        self,
        referer: str | None = None,
    ) -> list[tuple[dict[str, str], dict[str, str] | None]]:
        """CDN TikTok часто отклоняет запросы с sessionid — пробуем без cookies."""
        ref = referer or f"{self.settings.tiktok_base_url}/"
        return [
            (
                {
                    "User-Agent": self.settings.user_agent,
                    "Accept": "*/*",
                    "Referer": ref,
                },
                None,
            ),
            (
                {
                    "User-Agent": self.settings.user_agent,
                    "Accept": "*/*",
                },
                None,
            ),
            (
                self.auth.build_headers(referer=ref, accept="*/*"),
                None,
            ),
            (
                self.auth.build_headers(referer=ref, accept="*/*"),
                self.auth.build_cookies(),
            ),
        ]

    @staticmethod
    def _looks_like_media(data: bytes) -> bool:
        if len(data) < 256:
            return False
        if data[:1] == b"<":
            return False
        if data[:4] == b"ftyp" or data[4:8] == b"ftyp":
            return True
        if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
            return True
        if data[:2] == b"\xff\xd8":
            return True
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return True
        return len(data) >= 4096

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
        last_error: Exception | None = None

        for headers, cookies in self._cdn_download_attempts(referer):
            try:
                async with session.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                    allow_redirects=True,
                ) as resp:
                    if resp.status >= 400:
                        hint = (
                            " (restricted CDN)"
                            if resp.status == 403
                            and is_restricted_download_url(url)
                            else ""
                        )
                        logger.warning(
                            "%s: HTTP %s for %s%s",
                            label,
                            resp.status,
                            url[:80],
                            hint,
                        )
                        continue

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
                    if not self._looks_like_media(data):
                        logger.warning(
                            "%s: ответ не похож на медиа (%s байт)",
                            label,
                            len(data),
                        )
                        continue
                    return data, total
            except ValueError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("%s attempt failed: %s", label, exc)

        if last_error:
            raise last_error
        raise ValueError(f"Не удалось скачать: {url[:80]}")

    async def download_from_urls(
        self,
        urls: list[str],
        *,
        referer: str | None = None,
        label: str = "tiktok_media",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> tuple[bytes, int, str]:
        """Пробует список CDN-URL по очереди (mirror CDN раньше webapp-prime)."""
        errors: list[str] = []
        for url in sort_download_urls(urls):
            if not url or not str(url).startswith("http"):
                continue
            try:
                data, size = await self.download_media_bytes(
                    url,
                    referer=referer,
                    label=label,
                    max_bytes=max_bytes,
                )
                return data, size, url
            except ValueError:
                raise
            except Exception as exc:
                errors.append(f"{url[:60]}: {exc}")
                logger.warning("%s candidate failed: %s", label, exc)

        raise ValueError(
            "Не удалось скачать файл. "
            + ("; ".join(errors[:3]) if errors else "нет URL")
        )

    async def refresh_mirror_item(
        self,
        url: str,
        *,
        video_id: str | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        """Свежие play/hdplay URL через mirror (старые CDN-ссылки быстро протухают)."""
        return await self.fetch_video_mirror(
            url,
            video_id=video_id,
            username=username,
        )

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