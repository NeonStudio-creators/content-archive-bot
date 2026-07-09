"""
Разбор URL YouTube.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.fetcher import ResolvedLink
from core.models import EntityType
from core.platforms import Platform

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

URL_PATTERNS: list[tuple[re.Pattern[str], EntityType, str]] = [
    (
        re.compile(
            r"(?:https?://)?(?:www\.|m\.)?youtube\.com/watch\?",
            re.I,
        ),
        EntityType.PUBLICATION,
        "watch",
    ),
    (
        re.compile(
            r"(?:https?://)?youtu\.be/([A-Za-z0-9_-]{11})",
            re.I,
        ),
        EntityType.PUBLICATION,
        "short",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|m\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})",
            re.I,
        ),
        EntityType.PUBLICATION,
        "shorts",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|m\.)?youtube\.com/live/([A-Za-z0-9_-]{11})",
            re.I,
        ),
        EntityType.PUBLICATION,
        "live",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|m\.)?youtube\.com/@([A-Za-z0-9_.-]+)/?$",
            re.I,
        ),
        EntityType.PROFILE,
        "handle",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|m\.)?youtube\.com/channel/([A-Za-z0-9_-]+)/?$",
            re.I,
        ),
        EntityType.PROFILE,
        "channel_id",
    ),
]


class YouTubeLinkResolver:
    @staticmethod
    def is_youtube_url(url: str) -> bool:
        parsed = urlparse(url.lower())
        host = parsed.netloc.removeprefix("www.")
        return host in YOUTUBE_HOSTS or "youtube.com" in host

    @staticmethod
    def clean_url(url: str) -> str:
        url = url.strip()
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host == "youtu.be":
            return url.split("?")[0].rstrip("/")
        path = parsed.path.rstrip("/") or "/"
        if "/watch" in path:
            qs = parse_qs(parsed.query)
            vid = (qs.get("v") or [None])[0]
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    @staticmethod
    def extract_video_id(url: str) -> str | None:
        clean = YouTubeLinkResolver.clean_url(url)
        parsed = urlparse(clean)
        host = parsed.netloc.lower().removeprefix("www.")
        if host == "youtu.be":
            vid = parsed.path.strip("/").split("/")[0]
            return vid if len(vid) == 11 else None
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            return qs["v"][0]
        for pattern in (
            r"/shorts/([A-Za-z0-9_-]{11})",
            r"/live/([A-Za-z0-9_-]{11})",
            r"/embed/([A-Za-z0-9_-]{11})",
        ):
            match = re.search(pattern, parsed.path)
            if match:
                return match.group(1)
        return None

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        clean = cls.clean_url(url)
        if not cls.is_youtube_url(clean):
            return None

        parsed = urlparse(clean)
        if "/watch" in parsed.path:
            video_id = cls.extract_video_id(clean)
            if video_id:
                return ResolvedLink(
                    original_url=clean,
                    entity_type=EntityType.PUBLICATION,
                    identifiers={"video_id": video_id},
                    platform=Platform.YOUTUBE,
                )

        for regex, entity_type, key in URL_PATTERNS:
            match = regex.search(clean)
            if not match:
                continue
            if key in ("short", "shorts", "live"):
                return ResolvedLink(
                    original_url=clean,
                    entity_type=entity_type,
                    identifiers={"video_id": match.group(1)},
                    platform=Platform.YOUTUBE,
                )
            if key == "handle":
                handle = match.group(1)
                if handle.lower() in {"feed", "gaming", "shorts", "live"}:
                    continue
                return ResolvedLink(
                    original_url=clean,
                    entity_type=entity_type,
                    identifiers={"username": handle, "handle": handle},
                    platform=Platform.YOUTUBE,
                )
            if key == "channel_id":
                return ResolvedLink(
                    original_url=clean,
                    entity_type=entity_type,
                    identifiers={"channel_id": match.group(1)},
                    platform=Platform.YOUTUBE,
                )

        video_id = cls.extract_video_id(clean)
        if video_id:
            return ResolvedLink(
                original_url=clean,
                entity_type=EntityType.PUBLICATION,
                identifiers={"video_id": video_id},
                platform=Platform.YOUTUBE,
            )
        return None

    @staticmethod
    def watch_url(video_id: str) -> str:
        return f"https://www.youtube.com/watch?v={video_id}"

    @staticmethod
    def channel_url(handle: str | None = None, channel_id: str | None = None) -> str:
        if handle:
            return f"https://www.youtube.com/@{handle.lstrip('@')}"
        if channel_id:
            return f"https://www.youtube.com/channel/{channel_id}"
        return "https://www.youtube.com/"