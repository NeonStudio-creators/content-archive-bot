"""
Разбор URL TikTok.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from core.fetcher import ResolvedLink
from core.models import EntityType
from core.platforms import Platform

TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "m.tiktok.com",
}

URL_PATTERNS: list[tuple[re.Pattern[str], EntityType, str]] = [
    (
        re.compile(
            r"(?:https?://)?(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/@([^/]+)/video/(\d+)",
            re.I,
        ),
        EntityType.PUBLICATION,
        "video",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/@[^/]+/photo/(\d+)",
            re.I,
        ),
        EntityType.PUBLICATION,
        "photo",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/@([A-Za-z0-9_.]+)/?$",
            re.I,
        ),
        EntityType.PROFILE,
        "username",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:vm|vt)\.tiktok\.com/([A-Za-z0-9]+)/?",
            re.I,
        ),
        EntityType.PUBLICATION,
        "short",
    ),
    (
        re.compile(
            r"(?:https?://)?(?:www\.|m\.)?tiktok\.com/t/([A-Za-z0-9]+)/?",
            re.I,
        ),
        EntityType.PUBLICATION,
        "short",
    ),
]


class TikTokLinkResolver:
    """Определяет тип сущности TikTok и извлекает идентификаторы."""

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        pattern = re.compile(
            r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/[^\s<>\"']+",
            re.I,
        )
        return [TikTokLinkResolver.clean_url(u) for u in pattern.findall(text)]

    @staticmethod
    def clean_url(url: str) -> str:
        url = url.strip()
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        if host not in {h.removeprefix("www.") for h in TIKTOK_HOSTS}:
            return url
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        url = cls.clean_url(url)
        parsed = urlparse(url)
        if parsed.netloc.lower().removeprefix("www.") not in {
            h.removeprefix("www.") for h in TIKTOK_HOSTS
        } and "tiktok.com" not in parsed.netloc.lower():
            return None

        for regex, entity_type, key in URL_PATTERNS:
            match = regex.search(url)
            if not match:
                continue

            if key == "username":
                username = match.group(1).lower()
                if username in {"foryou", "following", "live", "explore"}:
                    continue
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={"username": username},
                    platform=Platform.TIKTOK,
                )

            if key == "video":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={
                        "video_id": match.group(2),
                        "username": match.group(1).lower(),
                    },
                    platform=Platform.TIKTOK,
                )

            if key == "photo":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={"video_id": match.group(1)},
                    platform=Platform.TIKTOK,
                )

            if key == "short":
                return ResolvedLink(
                    original_url=url,
                    entity_type=entity_type,
                    identifiers={"short_code": match.group(1)},
                    platform=Platform.TIKTOK,
                )

        return None

    @staticmethod
    def extract_video_id(url: str) -> str | None:
        for pattern in (
            r"/video/(\d+)",
            r"/v/(\d+)",
            r"/photo/(\d+)",
        ):
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    @classmethod
    def video_page_url(
        cls,
        video_id: str,
        username: str | None = None,
        *,
        prefer_mobile: bool = False,
    ) -> str:
        """Канонический URL страницы видео для API и mirror."""
        vid = str(video_id).strip()
        user = (username or "").strip().lstrip("@")
        if user and not prefer_mobile:
            return f"https://www.tiktok.com/@{user}/video/{vid}"
        return f"https://m.tiktok.com/v/{vid}.html"