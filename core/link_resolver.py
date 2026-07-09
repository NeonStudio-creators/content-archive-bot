"""
Единый разбор ссылок Instagram и TikTok.
"""

from __future__ import annotations

import re

from core.fetcher import LinkResolver as InstagramLinkResolver
from core.fetcher import ResolvedLink
from core.tiktok.resolver import TikTokLinkResolver


class LinkResolver:
    """Фасад: определяет платформу и делегирует парсер URL."""

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for pattern in (
            r"https?://(?:www\.)?instagram\.com/[^\s<>\"']+",
            r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/[^\s<>\"']+",
        ):
            for raw in re.findall(pattern, text, re.I):
                url = LinkResolver.clean_url(raw)
                if url not in seen:
                    seen.add(url)
                    found.append(url)
        return found

    @staticmethod
    def clean_url(url: str) -> str:
        lower = url.lower()
        if "tiktok.com" in lower:
            return TikTokLinkResolver.clean_url(url)
        return InstagramLinkResolver.clean_url(url)

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        clean = cls.clean_url(url)
        if "tiktok.com" in clean.lower():
            return TikTokLinkResolver.resolve(clean)
        return InstagramLinkResolver.resolve(clean)