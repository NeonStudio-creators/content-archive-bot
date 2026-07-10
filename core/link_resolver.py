"""
Единый разбор ссылок Instagram, TikTok, YouTube и Telegram.
"""

from __future__ import annotations

import re

from core.fetcher import LinkResolver as InstagramLinkResolver
from core.fetcher import ResolvedLink
from core.telegram.resolver import TelegramLinkResolver
from core.tiktok.resolver import TikTokLinkResolver
from core.youtube.resolver import YouTubeLinkResolver


class LinkResolver:
    """Фасад: определяет платформу и делегирует парсер URL."""

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for pattern in (
            r"https?://(?:www\.)?instagram\.com/[^\s<>\"']+",
            r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/[^\s<>\"']+",
            r"https?://(?:www\.|m\.)?youtube\.com/[^\s<>\"']+",
            r"https?://youtu\.be/[^\s<>\"']+",
            r"https?://(?:www\.)?(?:t\.me|telegram\.me)/[^\s<>\"']+",
            r"@[A-Za-z0-9_]{4,}",
        ):
            for raw in re.findall(pattern, text, re.I):
                url = LinkResolver.clean_url(raw)
                if url not in seen:
                    seen.add(url)
                    found.append(url)
        return found

    @staticmethod
    def clean_url(url: str) -> str:
        text = url.strip()
        if text.startswith("@"):
            return text
        lower = text.lower()
        if "tiktok.com" in lower:
            return TikTokLinkResolver.clean_url(text)
        if YouTubeLinkResolver.is_youtube_url(text):
            return YouTubeLinkResolver.clean_url(text)
        if TelegramLinkResolver.is_telegram_url(text):
            return TelegramLinkResolver.clean_url(text)
        return InstagramLinkResolver.clean_url(text)

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        clean = cls.clean_url(url)
        if clean.startswith("@"):
            return TelegramLinkResolver.resolve(clean)
        lower = clean.lower()
        if "tiktok.com" in lower:
            return TikTokLinkResolver.resolve(clean)
        if YouTubeLinkResolver.is_youtube_url(clean):
            return YouTubeLinkResolver.resolve(clean)
        if TelegramLinkResolver.is_telegram_url(clean):
            return TelegramLinkResolver.resolve(clean)
        return InstagramLinkResolver.resolve(clean)