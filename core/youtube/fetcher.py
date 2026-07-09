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
from core.youtube.innertube_clients import (
    INNERTUBE_CLIENTS,
    InnertubeClient,
    build_client_context,
)
from core.youtube.resolver import YouTubeLinkResolver
from utils.rate_limit import QuietRateLimiter

logger = logging.getLogger(__name__)

INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLvilw_F_7xPOSmNg"

_BOT_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "unusual traffic",
    "not a bot",
)

_PLAYER_RESPONSE_PATTERNS = (
    r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;",
    r"var ytInitialPlayerResponse\s*=\s*(\{.+?\});",
    r'"playerResponse":(\{.+?\})\s*,\s*"',
)


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

    @staticmethod
    def _is_bot_wall(html: str) -> bool:
        low = html[:8000].lower()
        return any(marker in low for marker in _BOT_MARKERS)

    @staticmethod
    def _player_ok(data: dict[str, Any]) -> bool:
        status = (data.get("playabilityStatus") or {}).get("status")
        streaming = data.get("streamingData") or {}
        has_stream = bool(
            streaming.get("formats")
            or streaming.get("adaptiveFormats")
        )
        if status == "OK" and has_stream:
            return True
        if has_stream and status in (None, "OK", "UNPLAYABLE"):
            for fmt in (streaming.get("formats") or []) + (
                streaming.get("adaptiveFormats") or []
            ):
                if _format_url(fmt):
                    return True
        return False

    @staticmethod
    def _playability_reason(data: dict[str, Any]) -> str:
        block = data.get("playabilityStatus") or {}
        return str(block.get("reason") or block.get("status") or "no streams")

    async def ensure_session(self, *, force: bool = False) -> None:
        if self._bootstrapped and not force:
            return
        if force:
            self._bootstrapped = False
        session = await self._get_session()
        try:
            await self.rate_limiter.wait()
            cookies = self.auth.build_cookies()
            cookies.setdefault("SOCS", "CAI")
            cookies.setdefault("CONSENT", "YES+1")
            headers = self.auth.build_headers(
                referer=f"{self.settings.youtube_base_url}/"
            )
            async with session.get(
                f"{self.settings.youtube_base_url}/",
                headers=headers,
                cookies=cookies,
                allow_redirects=True,
            ) as resp:
                html = await resp.text()
                self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
                for pattern in (
                    r'"VISITOR_DATA":"([^"]+)"',
                    r'"visitorData":"([^"]+)"',
                ):
                    match = re.search(pattern, html)
                    if match:
                        self._visitor_id = match.group(1)
                        break
        except Exception as exc:
            logger.warning("youtube bootstrap failed: %s", exc)
        finally:
            self._bootstrapped = True

    def _request_cookies(self, *, use_auth: bool) -> dict[str, str]:
        cookies = self.auth.build_cookies() if use_auth else {}
        cookies.setdefault("SOCS", "CAI")
        cookies.setdefault("CONSENT", "YES+1")
        return cookies

    def _api_headers(
        self,
        *,
        referer: str,
        user_agent: str,
        client: InnertubeClient,
    ) -> dict[str, str]:
        headers = self.auth.build_headers(referer=referer, for_api=True)
        headers["User-Agent"] = user_agent
        if self._visitor_id:
            headers["X-Goog-Visitor-Id"] = self._visitor_id
        if client.name == "WEB":
            headers["X-Youtube-Client-Version"] = (
                self.settings.youtube_client_version
            )
        return headers

    async def _innertube_post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        referer: str,
        client: InnertubeClient,
    ) -> dict[str, Any]:
        await self.ensure_session()
        use_cookies = client.needs_cookies and self.auth.is_configured()
        video_id = str(body.get("videoId") or "")
        payload = {
            "context": build_client_context(
                client,
                visitor_id=self._visitor_id,
                web_client_version=self.settings.youtube_client_version,
                video_id=video_id,
            ),
            **body,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }

        url = (
            f"{self.settings.youtube_base_url}/youtubei/v1/{endpoint}"
            f"?key={INNERTUBE_API_KEY}&prettyPrint=false"
        )
        session = await self._get_session()
        await self.rate_limiter.wait()
        headers = self._api_headers(
            referer=referer,
            user_agent=client.user_agent,
            client=client,
        )
        async with session.post(
            url,
            headers=headers,
            cookies=self._request_cookies(use_auth=use_cookies),
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

    _MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.6 Mobile/15E148 Safari/604.36"
    )

    async def _fetch_html_player(
        self,
        page_url: str,
        *,
        label: str,
        user_agent: str | None = None,
    ) -> dict[str, Any] | None:
        await self.ensure_session()
        session = await self._get_session()
        await self.rate_limiter.wait()
        headers = self.auth.build_headers(referer=page_url)
        if user_agent:
            headers["User-Agent"] = user_agent
        cookies = self._request_cookies(use_auth=self.auth.is_configured())
        async with session.get(
            page_url,
            headers=headers,
            cookies=cookies,
            allow_redirects=True,
        ) as resp:
            html = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))

        if self._is_bot_wall(html):
            logger.warning("youtube %s: bot-check page", label)
            return None

        for pattern in _PLAYER_RESPONSE_PATTERNS:
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if self._player_ok(data):
                data["_client"] = label
                return data
        return None

    async def fetch_player_via_html(self, video_id: str) -> dict[str, Any] | None:
        """ytInitialPlayerResponse — m.youtube, watch, shorts."""
        best: dict[str, Any] | None = None
        urls: list[tuple[str, str, str | None]] = [
            (
                f"https://m.youtube.com/watch?v={video_id}",
                "html_mweb",
                self._MOBILE_UA,
            ),
            (YouTubeLinkResolver.watch_url(video_id), "html_watch", None),
            (
                f"{self.settings.youtube_base_url}/shorts/{video_id}",
                "html_shorts",
                self._MOBILE_UA,
            ),
            (
                f"{self.settings.youtube_base_url}/embed/{video_id}",
                "html_embed",
                None,
            ),
        ]
        for page_url, label, ua in urls:
            data = await self._fetch_html_player(
                page_url, label=label, user_agent=ua
            )
            if not data:
                continue
            if not best:
                best = data
                continue
            if self._stream_url_count(data.get("streamingData")) > (
                self._stream_url_count(best.get("streamingData"))
            ):
                best = data
        return best

    async def fetch_player(
        self,
        video_id: str,
        *,
        referer: str | None = None,
    ) -> dict[str, Any]:
        ref = referer or YouTubeLinkResolver.watch_url(video_id)
        errors: list[str] = []

        for client in INNERTUBE_CLIENTS:
            if client.needs_cookies and not self.auth.is_configured():
                continue
            client_ref = ref
            if client.embed_url and "{video_id}" in client.embed_url:
                client_ref = client.embed_url.format(video_id=video_id)
            try:
                data = await self._innertube_post(
                    "player",
                    {"videoId": video_id},
                    referer=client_ref,
                    client=client,
                )
                if self._player_ok(data):
                    data["_client"] = client.name
                    return data
                errors.append(f"{client.name}: {self._playability_reason(data)}")
            except Exception as exc:
                errors.append(f"{client.name}: {exc}")

        hint = self._auth_hint()
        raise ValueError(
            "Не удалось получить видео YouTube. "
            + ("; ".join(errors[:4]) if errors else "нет доступных клиентов")
            + hint
        )

    def _auth_hint(self) -> str:
        if self.auth.is_configured():
            return (
                " Обновите YOUTUBE_SESSION_TOKEN в Railway "
                "(свежие cookies с youtube.com в том же браузере)."
            )
        return (
            " Добавьте YOUTUBE_SESSION_TOKEN в Railway "
            "(cookies SID, SAPISID, __Secure-1PSID с youtube.com)."
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
            cookies=self._request_cookies(use_auth=self.auth.is_configured()),
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
        clients = [
            c
            for c in (
                primary.get("_client"),
                secondary.get("_client"),
            )
            if c
        ]
        if clients:
            merged["_client"] = "+".join(dict.fromkeys(clients))
        return merged

    async def fetch_source_player(self, video_id: str) -> dict[str, Any]:
        """Исходные потоки — HTML + каскад InnerTube-клиентов."""
        canonical = YouTubeLinkResolver.watch_url(video_id)
        player: dict[str, Any] | None = None
        errors: list[str] = []

        html_player = await self.fetch_player_via_html(video_id)
        if html_player:
            player = html_player

        for client in INNERTUBE_CLIENTS:
            if client.needs_cookies and not self.auth.is_configured():
                continue
            client_ref = canonical
            if client.embed_url and "{video_id}" in client.embed_url:
                client_ref = client.embed_url.format(video_id=video_id)
            try:
                data = await self._innertube_post(
                    "player",
                    {"videoId": video_id},
                    referer=client_ref,
                    client=client,
                )
                if self._player_ok(data):
                    data["_client"] = client.name
                    player = (
                        self._merge_player_data(player, data)
                        if player
                        else data
                    )
                    if self._stream_url_count(
                        player.get("streamingData")
                    ) >= 3:
                        break
                else:
                    errors.append(
                        f"{client.name}: {self._playability_reason(data)}"
                    )
            except Exception as exc:
                errors.append(f"{client.name}: {exc}")
                logger.warning("youtube %s: %s", client.name, exc)

        if not player or not self._player_ok(player):
            hint = self._auth_hint()
            raise ValueError(
                "Не удалось получить видео YouTube. "
                + ("; ".join(errors[:4]) if errors else "нет потоков")
                + hint
            )

        if not player.get("_source"):
            player["_source"] = player.get("_client", "merged")
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
                self._request_cookies(use_auth=self.auth.is_configured()),
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