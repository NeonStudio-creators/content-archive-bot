"""
Сетевой слой YouTube: cookies + InnerTube API.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
import aiohttp

from config import Settings, is_cloud_deploy, secrets_hint
from core.session_bootstrap import merge_cookies, parse_set_cookies
from core.youtube.auth import YouTubeSessionAuthManager
from core.youtube.hq_meta import _format_url
from core.youtube.html_extract import extract_player_response
from core.youtube.innertube_clients import (
    ANON_INNERTUBE_CLIENTS,
    AUTH_INNERTUBE_CLIENTS,
    INNERTUBE_CLIENTS,
    InnertubeClient,
    build_client_context,
)
from core.youtube.resolver import YouTubeLinkResolver
from core.youtube.mirror_fallback import fetch_via_mirrors
from core.youtube.session_verify import YouTubeSessionVerify
from core.youtube.ytdlp_fallback import fetch_via_ytdlp, probe_ytdlp
from utils.rate_limit import QuietRateLimiter

logger = logging.getLogger(__name__)

INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLvilw_F_7xPOSmNg"

_BOT_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "unusual traffic",
    "not a bot",
)

@dataclass
class YouTubeFetcher:
    settings: Settings
    auth: YouTubeSessionAuthManager
    rate_limiter: QuietRateLimiter
    _session: aiohttp.ClientSession | None = field(default=None, init=False)
    _bootstrapped: bool = field(default=False, init=False)
    _auth_bootstrapped: bool = field(default=False, init=False)
    _visitor_id: str = field(default="", init=False)
    _auth_refresh_callback: Callable[[], Awaitable[None]] | None = field(
        default=None, init=False, repr=False
    )

    def set_auth_refresh_callback(
        self,
        callback: Callable[[], Awaitable[None]] | None,
    ) -> None:
        self._auth_refresh_callback = callback

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

    @staticmethod
    def _consent_cookies() -> dict[str, str]:
        return {"SOCS": "CAI", "CONSENT": "YES+1"}

    @staticmethod
    def _extract_visitor_id(html: str) -> str:
        for pattern in (
            r'"VISITOR_DATA":"([^"]+)"',
            r'"visitorData":"([^"]+)"',
        ):
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return ""

    async def _bootstrap_visitor(self, *, force: bool = False) -> None:
        if self._bootstrapped and not force:
            return
        if force:
            self._bootstrapped = False
        session = await self._get_session()
        try:
            await self.rate_limiter.wait()
            headers = {
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            }
            for page_url in (
                f"{self.settings.youtube_base_url}/",
                "https://m.youtube.com/",
            ):
                async with session.get(
                    page_url,
                    headers=headers,
                    cookies=self._consent_cookies(),
                    allow_redirects=True,
                ) as resp:
                    html = await resp.text()
                    self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
                    visitor = self._extract_visitor_id(html)
                    if visitor:
                        self._visitor_id = visitor
                        break
        except Exception as exc:
            logger.warning("youtube visitor bootstrap failed: %s", exc)
        finally:
            self._bootstrapped = True

    async def bootstrap_auth_session(self, *, force: bool = False) -> bool:
        """Прогрев с cookies: Set-Cookie → runtime + .token_cache.json."""
        if not self.auth.is_configured():
            return False
        if self._auth_bootstrapped and not force:
            return True
        if force:
            self._auth_bootstrapped = False

        await self._bootstrap_visitor(force=force)
        session = await self._get_session()
        pages = (
            f"{self.settings.youtube_base_url}/",
            f"{self.settings.youtube_base_url}/feed/you",
            "https://m.youtube.com/",
            f"{self.settings.youtube_base_url}/account",
        )
        try:
            for page_url in pages:
                await self.rate_limiter.wait()
                headers = self.auth.build_headers(referer=page_url)
                async with session.get(
                    page_url,
                    headers=headers,
                    cookies=self._request_cookies(use_auth=True),
                    allow_redirects=True,
                ) as resp:
                    html = await resp.text()
                    merged = merge_cookies(
                        parse_set_cookies(resp.headers),
                        self.auth.build_cookies(),
                    )
                    self.auth.update_runtime_cookies(merged)
                    visitor = self._extract_visitor_id(html)
                    if visitor:
                        self._visitor_id = visitor
            logger.info(
                "youtube auth bootstrap: cookies=%s visitor=%s",
                list(self.auth.build_cookies().keys()),
                "OK" if self._visitor_id else "MISSING",
            )
            self._auth_bootstrapped = True
            return True
        except Exception as exc:
            logger.warning("youtube auth bootstrap failed: %s", exc)
            self._auth_bootstrapped = True
            return False

    async def ensure_session(self, *, force: bool = False) -> None:
        await self._bootstrap_visitor(force=force)
        if self.auth.is_configured():
            await self.bootstrap_auth_session(force=force)

    def _request_cookies(self, *, use_auth: bool) -> dict[str, str]:
        cookies = dict(self._consent_cookies())
        if use_auth and self.auth.is_configured():
            cookies.update(self.auth.build_cookies())
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
        headers["X-Youtube-Client-Name"] = str(client.client_id)
        headers["X-Youtube-Client-Version"] = (
            self.settings.youtube_client_version
            if client.name == "WEB"
            else client.version
        )
        if self._visitor_id:
            headers["X-Goog-Visitor-Id"] = self._visitor_id
        return headers

    async def _innertube_post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        referer: str,
        client: InnertubeClient,
        use_auth: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_session()
        if use_auth and not self.auth.is_configured():
            raise ValueError("YouTube cookies не настроены")
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
            "playbackContext": {
                "contentPlaybackContext": {
                    "html5Preference": "HTML5_PREF_WANTS",
                },
            },
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
            cookies=self._request_cookies(use_auth=use_auth),
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
        use_auth: bool = False,
    ) -> dict[str, Any] | None:
        await self.ensure_session()
        session = await self._get_session()
        await self.rate_limiter.wait()
        headers = self.auth.build_headers(referer=page_url)
        if user_agent:
            headers["User-Agent"] = user_agent
        cookies = self._request_cookies(use_auth=use_auth)
        async with session.get(
            page_url,
            headers=headers,
            cookies=cookies,
            allow_redirects=True,
        ) as resp:
            html = await resp.text()
            self.auth.update_runtime_cookies(parse_set_cookies(resp.headers))
            visitor = self._extract_visitor_id(html)
            if visitor:
                self._visitor_id = visitor

        if self._is_bot_wall(html):
            logger.warning("youtube %s: bot-check page", label)
            return None

        data = extract_player_response(html)
        if data and self._player_ok(data):
            data["_client"] = label
            return data
        if data:
            logger.warning(
                "youtube %s: player parsed but no streams (%s)",
                label,
                self._playability_reason(data),
            )
        else:
            logger.warning("youtube %s: ytInitialPlayerResponse not found", label)
        return None

    async def fetch_player_via_html(
        self,
        video_id: str,
        *,
        use_auth: bool = False,
    ) -> dict[str, Any] | None:
        """ytInitialPlayerResponse — m.youtube, watch, shorts."""
        best: dict[str, Any] | None = None
        urls: list[tuple[str, str, str | None]] = [
            (YouTubeLinkResolver.watch_url(video_id), "html_watch", None),
            (
                f"https://m.youtube.com/watch?v={video_id}",
                "html_mweb",
                self._MOBILE_UA,
            ),
            (
                f"{self.settings.youtube_base_url}/embed/{video_id}",
                "html_embed",
                None,
            ),
            (
                f"{self.settings.youtube_base_url}/shorts/{video_id}",
                "html_shorts",
                self._MOBILE_UA,
            ),
        ]
        for page_url, label, ua in urls:
            data = await self._fetch_html_player(
                page_url,
                label=label,
                user_agent=ua,
                use_auth=use_auth,
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
            use_auth = client.needs_cookies
            if use_auth and not self.auth.is_configured():
                continue
            client_ref = ref
            if client.embed_url and "{video_id}" in client.embed_url:
                client_ref = client.embed_url.format(video_id=video_id)
            elif client.embed_url:
                client_ref = client.embed_url
            try:
                data = await self._innertube_post(
                    "player",
                    {"videoId": video_id},
                    referer=client_ref,
                    client=client,
                    use_auth=use_auth,
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
        where = secrets_hint()
        if self.auth.is_configured():
            cloud = (
                " На облачном IP (Railway) YouTube часто блокирует даже свежие cookies — "
                "запускайте бота на домашнем ПК/VPS."
                if is_cloud_deploy()
                else ""
            )
            return (
                " Cookies устарели или неполные — обновите YOUTUBE_SESSION_TOKEN в "
                f"{where}: откройте youtube.com в Chrome (вы залогинены) → F12 → "
                "Application → Cookies → скопируйте SID, SAPISID, __Secure-1PSID, "
                f"__Secure-1PAPISID.{cloud}"
            )
        return (
            f" Добавьте YOUTUBE_SESSION_TOKEN в {where} "
            "(SID + SAPISID + __Secure-1PSID + __Secure-1PAPISID с youtube.com)."
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
            sec_val = secondary.get(key)
            if not sec_val:
                continue
            pri_val = merged.get(key)
            if not pri_val:
                merged[key] = sec_val
            elif key == "videoDetails" and isinstance(pri_val, dict) and isinstance(
                sec_val, dict
            ):
                filled = dict(sec_val)
                for sub_key, sub_val in pri_val.items():
                    if sub_val not in (None, "", [], {}):
                        filled[sub_key] = sub_val
                merged[key] = filled
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

    async def _innertube_cascade(
        self,
        video_id: str,
        canonical: str,
        player: dict[str, Any] | None,
        errors: list[str],
        *,
        clients: tuple[InnertubeClient, ...],
        use_auth: bool,
        phase: str,
    ) -> dict[str, Any] | None:
        for client in clients:
            client_ref = canonical
            if client.embed_url and "{video_id}" in client.embed_url:
                client_ref = client.embed_url.format(video_id=video_id)
            elif client.embed_url:
                client_ref = client.embed_url
            try:
                data = await self._innertube_post(
                    "player",
                    {"videoId": video_id},
                    referer=client_ref,
                    client=client,
                    use_auth=use_auth,
                )
                if self._player_ok(data):
                    data["_client"] = f"{phase}{client.name}"
                    player = (
                        self._merge_player_data(player, data)
                        if player
                        else data
                    )
                    if self._stream_url_count(
                        player.get("streamingData")
                    ) >= 3:
                        return player
                else:
                    errors.append(
                        f"{phase}{client.name}: {self._playability_reason(data)}"
                    )
            except Exception as exc:
                errors.append(f"{phase}{client.name}: {exc}")
                logger.warning("youtube %s%s: %s", phase, client.name, exc)
        return player

    def _finalize_player(
        self,
        player: dict[str, Any],
        video_id: str,
        canonical: str,
    ) -> dict[str, Any]:
        if not player.get("_source"):
            player["_source"] = player.get("_client", "merged")
        player["_video_id"] = video_id
        player["_canonical_url"] = canonical
        return player

    async def _fetch_source_player_once(self, video_id: str) -> dict[str, Any]:
        """Один проход: HTML + InnerTube."""
        canonical = YouTubeLinkResolver.watch_url(video_id)
        player: dict[str, Any] | None = None
        errors: list[str] = []

        html_anon = await self.fetch_player_via_html(video_id, use_auth=False)
        if html_anon:
            player = html_anon
        else:
            errors.append("HTML(anon): bot-check или нет потоков")

        player = await self._innertube_cascade(
            video_id,
            canonical,
            player,
            errors,
            clients=ANON_INNERTUBE_CLIENTS,
            use_auth=False,
            phase="",
        )

        if player and self._player_ok(player):
            return self._finalize_player(player, video_id, canonical)

        if self.auth.is_configured():
            html_auth = await self.fetch_player_via_html(video_id, use_auth=True)
            if html_auth:
                player = (
                    self._merge_player_data(player, html_auth)
                    if player
                    else html_auth
                )
            else:
                errors.append("HTML(auth): bot-check или устаревшие cookies")

            player = await self._innertube_cascade(
                video_id,
                canonical,
                player,
                errors,
                clients=AUTH_INNERTUBE_CLIENTS,
                use_auth=True,
                phase="auth:",
            )
        else:
            errors.append("Cookies: не заданы")

        if not player or not self._player_ok(player):
            raise ValueError(
                "Не удалось получить видео YouTube. "
                + ("; ".join(errors[:8]) if errors else "нет потоков")
                + self._auth_hint()
            )

        return self._finalize_player(player, video_id, canonical)

    async def _fetch_external_fallback(
        self,
        video_id: str,
        canonical: str,
    ) -> dict[str, Any] | None:
        cookies = (
            self._request_cookies(use_auth=True)
            if self.auth.is_configured()
            else None
        )
        for cookie_try in (
            cookies,
            None,
        ) if cookies else (None,):
            player = await fetch_via_ytdlp(
                video_id,
                canonical,
                cookies=cookie_try,
            )
            if player and self._player_ok(player):
                logger.info(
                    "youtube fallback: yt-dlp OK (cookies=%s)",
                    bool(cookie_try),
                )
                return player

        session = await self._get_session()
        player = await fetch_via_mirrors(session, video_id)
        if player and self._player_ok(player):
            logger.info("youtube fallback: mirror OK (%s)", player.get("_client"))
            return player
        return None

    async def fetch_source_player(self, video_id: str) -> dict[str, Any]:
        """Исходные потоки — InnerTube, затем yt-dlp / mirror."""
        await self.ensure_session()
        canonical = YouTubeLinkResolver.watch_url(video_id)
        last_error: ValueError | None = None

        for attempt in range(2):
            try:
                return await self._fetch_source_player_once(video_id)
            except ValueError as exc:
                last_error = exc
            except Exception as exc:
                logger.warning("youtube innertube pass failed: %s", exc)
                last_error = ValueError(str(exc))
            if attempt == 0 and self.auth.is_configured():
                logger.info("youtube: refresh cookies and retry")
                if self._auth_refresh_callback:
                    await self._auth_refresh_callback()
                else:
                    await self.bootstrap_auth_session(force=True)

        fallback = await self._fetch_external_fallback(video_id, canonical)
        if fallback:
            return self._finalize_player(fallback, video_id, canonical)

        hint = (
            " InnerTube и cookies не сработали; yt-dlp/mirror тоже недоступны."
            + self._auth_hint()
        )
        if last_error:
            raise ValueError(str(last_error) + hint) from last_error
        raise ValueError("Не удалось получить видео YouTube." + hint)

    async def verify_session(
        self,
        *,
        test_video_id: str = "dQw4w9WgXcQ",
    ) -> YouTubeSessionVerify:
        result = YouTubeSessionVerify(
            configured=self.auth.is_configured(),
            cookie_count=len(self.auth.build_cookies()),
            session_ok=False,
            visitor_ok=bool(self._visitor_id),
        )
        await self.bootstrap_auth_session(force=True)
        result.configured = self.auth.is_configured()
        result.cookie_count = len(self.auth.build_cookies())
        diag = self.auth.cookie_diagnostic()
        if not result.configured:
            if diag["missing"]:
                result.errors.append(
                    "Не хватает: " + ", ".join(diag["missing"])
                )
            if diag["env_len"] == 0:
                result.errors.append(
                    f"YOUTUBE_SESSION_TOKEN пуст ({secrets_hint()})"
                )

        result.visitor_ok = bool(self._visitor_id)
        result.session_ok = self.auth.is_configured()

        if result.configured:
            mweb = next(c for c in AUTH_INNERTUBE_CLIENTS if c.name == "MWEB")
            try:
                data = await self._innertube_post(
                    "player",
                    {"videoId": test_video_id},
                    referer=YouTubeLinkResolver.watch_url(test_video_id),
                    client=mweb,
                    use_auth=True,
                )
                if self._player_ok(data):
                    result.test_streams = self._stream_url_count(
                        data.get("streamingData")
                    )
                    result.client = "MWEB"
                else:
                    result.errors.append(
                        f"MWEB: {self._playability_reason(data)}"
                    )
            except Exception as exc:
                result.errors.append(f"MWEB: {exc}")

        ytdlp_ok, ytdlp_msg = await probe_ytdlp()
        if ytdlp_ok:
            ytdlp_player = await fetch_via_ytdlp(
                test_video_id,
                YouTubeLinkResolver.watch_url(test_video_id),
                cookies=self._request_cookies(use_auth=True)
                if self.auth.is_configured()
                else None,
            )
            if ytdlp_player and self._player_ok(ytdlp_player):
                result.test_streams = self._stream_url_count(
                    ytdlp_player.get("streamingData")
                )
                result.client = "ytdlp"
            else:
                result.errors.append(f"yt-dlp: не получил потоки ({ytdlp_msg})")
        else:
            result.errors.append(f"yt-dlp: {ytdlp_msg}")

        if not result.test_streams:
            for client in ANON_INNERTUBE_CLIENTS[:2]:
                try:
                    data = await self._innertube_post(
                        "player",
                        {"videoId": test_video_id},
                        referer=YouTubeLinkResolver.watch_url(test_video_id),
                        client=client,
                        use_auth=False,
                    )
                    if self._player_ok(data):
                        result.test_streams = self._stream_url_count(
                            data.get("streamingData")
                        )
                        result.client = client.name
                        break
                    result.errors.append(
                        f"{client.name}: {self._playability_reason(data)}"
                    )
                except Exception as exc:
                    result.errors.append(f"{client.name}: {exc}")

        return result

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