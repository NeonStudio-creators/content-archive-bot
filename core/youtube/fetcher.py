"""
Сетевой слой YouTube: cookies + InnerTube API.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
import aiohttp

from config import Settings
from core.session_bootstrap import merge_cookies, parse_set_cookies
from core.youtube.auth import YouTubeSessionAuthManager
from core.youtube.hq_meta import _format_url
from core.youtube.resolver import YouTubeLinkResolver
from utils.rate_limit import QuietRateLimiter
from utils.retry import with_retry

logger = logging.getLogger(__name__)

INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLvilw_F_7xPOSmNg"


@dataclass
class YouTubeFetcher:
    settings: Settings
    auth: YouTubeSessionAuthManager
    rate_limiter: QuietRateLimiter
    _session: aiohttp.ClientSession | None = field(default=None, init=False)
    _bootstrapped: bool = field(default=False, init=False)
    _visitor_id: str = field(default="", init=False)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=90, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _client_context(self, client_name: str, client_version: str) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "clientName": client_name,
            "clientVersion": client_version,
            "hl": "en",
            "gl": "US",
            "userAgent": self.settings.user_agent,
        }
        if self._visitor_id:
            ctx["visitorData"] = self._visitor_id
        return {"client": ctx}

    async def ensure_session(self) -> None:
        if self._bootstrapped:
            return
        session = await self._get_session()
        try:
            await self.rate_limiter.wait()
            headers = self.auth.build_headers(referer=f"{self.settings.youtube_base_url}/")
            async with session.get(
                f"{self.settings.youtube_base_url}/",
                headers=headers,
                cookies=self.auth.build_cookies(),
                allow_redirects=True,
            ) as resp:
                html = await resp.text()
                self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
                match = re.search(r'"VISITOR_DATA":"([^"]+)"', html)
                if match:
                    self._visitor_id = match.group(1)
        except Exception as exc:
            logger.warning("youtube bootstrap failed: %s", exc)
        finally:
            self._bootstrapped = True

    async def _innertube_post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        referer: str,
        client_name: str = "WEB",
        client_version: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_session()
        version = client_version or self.settings.youtube_client_version
        payload = {
            "context": self._client_context(client_name, version),
            **body,
        }
        url = (
            f"{self.settings.youtube_base_url}/youtubei/v1/{endpoint}"
            f"?key={INNERTUBE_API_KEY}&prettyPrint=false"
        )
        session = await self._get_session()
        await self.rate_limiter.wait()
        headers = self.auth.build_headers(referer=referer, for_api=True)
        async with session.post(
            url,
            headers=headers,
            cookies=self.auth.build_cookies(),
            json=payload,
        ) as resp:
            text = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
            if resp.status >= 400:
                raise ValueError(f"YouTube API HTTP {resp.status}")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"YouTube API JSON: {exc}") from exc

    async def fetch_player_via_html(self, video_id: str) -> dict[str, Any] | None:
        """ytInitialPlayerResponse со страницы watch — основной источник streamingData."""
        url = YouTubeLinkResolver.watch_url(video_id)
        await self.ensure_session()
        session = await self._get_session()
        await self.rate_limiter.wait()
        headers = self.auth.build_headers(referer=url)
        async with session.get(
            url,
            headers=headers,
            cookies=self.auth.build_cookies(),
            allow_redirects=True,
        ) as resp:
            html = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))

        patterns = (
            r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;",
            r"var ytInitialPlayerResponse\s*=\s*(\{.+?\});",
        )
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if data.get("videoDetails") or data.get("streamingData"):
                return data
        return None

    async def fetch_player(
        self,
        video_id: str,
        *,
        referer: str | None = None,
    ) -> dict[str, Any]:
        ref = referer or YouTubeLinkResolver.watch_url(video_id)
        clients = [
            ("WEB", self.settings.youtube_client_version),
            ("MWEB", "2.20240701.00.00"),
            ("TVHTML5_SIMPLY_EMBEDDED_PLAYER", "2.0"),
        ]
        errors: list[str] = []
        for client_name, client_version in clients:
            try:
                data = await self._innertube_post(
                    "player",
                    {"videoId": video_id},
                    referer=ref,
                    client_name=client_name,
                    client_version=client_version,
                )
                status = (data.get("playabilityStatus") or {}).get("status")
                if status == "OK" and data.get("streamingData"):
                    data["_client"] = client_name
                    return data
                reason = (data.get("playabilityStatus") or {}).get("reason", status)
                errors.append(f"{client_name}: {reason}")
            except Exception as exc:
                errors.append(f"{client_name}: {exc}")

        hint = (
            " Добавьте YOUTUBE_SESSION_TOKEN (cookies с youtube.com)."
            if not self.auth.is_configured()
            else ""
        )
        raise ValueError(
            "Не удалось получить видео YouTube. "
            + ("; ".join(errors[:3]) if errors else "")
            + hint
        )

    async def fetch_channel(
        self,
        *,
        handle: str | None = None,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_session()
        if handle:
            page_url = YouTubeLinkResolver.channel_url(handle=handle)
            referer = page_url
            browse_id = f"@{handle.lstrip('@')}"
        elif channel_id:
            page_url = YouTubeLinkResolver.channel_url(channel_id=channel_id)
            referer = page_url
            browse_id = channel_id
        else:
            raise ValueError("Не указан канал YouTube")

        session = await self._get_session()
        await self.rate_limiter.wait()
        headers = self.auth.build_headers(referer=referer)
        async with session.get(
            page_url,
            headers=headers,
            cookies=self.auth.build_cookies(),
            allow_redirects=True,
        ) as resp:
            html = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))

        meta_match = re.search(
            r'<meta itemprop="channelId" content="([^"]+)"',
            html,
        )
        resolved_channel_id = meta_match.group(1) if meta_match else channel_id

        title_match = re.search(
            r'<meta property="og:title" content="([^"]+)"',
            html,
        )
        avatar_match = re.search(
            r'"avatar":\{"thumbnails":\[\{"url":"([^"]+)"',
            html,
        )
        sub_match = re.search(
            r'"subscriberCountText":\{"simpleText":"([^"]+)"',
            html,
        )

        return {
            "handle": handle,
            "channel_id": resolved_channel_id,
            "title": title_match.group(1) if title_match else handle,
            "avatar": avatar_match.group(1) if avatar_match else None,
            "subscriber_text": sub_match.group(1) if sub_match else None,
            "html": html,
            "browse_id": browse_id if handle else resolved_channel_id,
        }

    @staticmethod
    def _stream_url_count(streaming: dict[str, Any] | None) -> int:
        if not streaming:
            return 0
        total = 0
        for fmt in (streaming.get("formats") or []) + (
            streaming.get("adaptiveFormats") or []
        ):
            if _format_url(fmt):
                total += 1
        return total

    @staticmethod
    def _merge_player_data(
        primary: dict[str, Any],
        secondary: dict[str, Any],
    ) -> dict[str, Any]:
        """Объединяет streamingData — берём потоки с большим числом прямых URL."""
        merged = dict(primary)
        p_stream = primary.get("streamingData") or {}
        s_stream = secondary.get("streamingData") or {}
        if YouTubeFetcher._stream_url_count(s_stream) > YouTubeFetcher._stream_url_count(
            p_stream
        ):
            merged["streamingData"] = s_stream
        for key in ("videoDetails", "playabilityStatus", "microformat"):
            if not merged.get(key) and secondary.get(key):
                merged[key] = secondary[key]
        return merged

    async def fetch_source_player(self, video_id: str) -> dict[str, Any]:
        """Исходные потоки (adaptive/hd) — InnerTube с cookies + HTML."""
        canonical = YouTubeLinkResolver.watch_url(video_id)
        html_player = await self.fetch_player_via_html(video_id)
        player: dict[str, Any] | None = html_player

        if self.auth.is_configured():
            try:
                api_player = await self.fetch_player(video_id, referer=canonical)
                if html_player:
                    player = self._merge_player_data(api_player, html_player)
                else:
                    player = api_player
                player["_source"] = "innertube+html"
            except Exception as exc:
                logger.warning("youtube source innertube: %s", exc)

        if not player:
            player = await self.fetch_player(video_id, referer=canonical)
            player["_source"] = player.get("_client", "innertube")
        elif not player.get("_source"):
            player["_source"] = "html"

        player["_video_id"] = video_id
        player["_canonical_url"] = canonical
        return player

    async def fetch_video(self, url: str, *, video_id: str | None = None) -> dict[str, Any]:
        vid = video_id or YouTubeLinkResolver.extract_video_id(url)
        if not vid:
            raise ValueError("Не удалось извлечь ID видео YouTube")
        return await self.fetch_source_player(vid)

    def _cdn_download_attempts(
        self,
        referer: str | None = None,
    ) -> list[tuple[dict[str, str], dict[str, str] | None]]:
        ref = referer or f"{self.settings.youtube_base_url}/"
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
                self.auth.build_headers(referer=ref, accept="*/*"),
                self.auth.build_cookies(),
            ),
        ]

    @staticmethod
    def _looks_like_media(data: bytes) -> bool:
        if len(data) < 256 or data[:1] == b"<":
            return False
        if data[:4] == b"ftyp" or data[4:8] == b"ftyp":
            return True
        if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
            return True
        return len(data) >= 4096

    async def download_media_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "youtube_media",
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
                        logger.warning("%s: HTTP %s", label, resp.status)
                        continue
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(262_144):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("Файл слишком большой для Telegram")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if not self._looks_like_media(data):
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
        label: str = "youtube_media",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> tuple[bytes, int, str]:
        errors: list[str] = []
        for url in urls:
            if not url or not url.startswith("http"):
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
                errors.append(str(exc))
        raise ValueError(
            "Не удалось скачать файл. "
            + ("; ".join(errors[:3]) if errors else "нет URL")
        )

    async def download_bytes(
        self,
        url: str,
        *,
        referer: str | None = None,
        label: str = "youtube_download",
        max_bytes: int = 48 * 1024 * 1024,
    ) -> bytes:
        data, _ = await self.download_media_bytes(
            url,
            referer=referer,
            label=label,
            max_bytes=max_bytes,
        )
        return data