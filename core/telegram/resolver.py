"""
Разбор ссылок Telegram-каналов: t.me, telegram.me, @username.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from core.fetcher import ResolvedLink
from core.models import EntityType
from core.platforms import Platform

TELEGRAM_HOSTS = {"t.me", "telegram.me", "www.telegram.me"}

TELEGRAM_RESERVED = frozenset({
    "joinchat",
    "addstickers",
    "addemoji",
    "addlist",
    "iv",
    "socks",
    "share",
    "login",
    "proxy",
    "contact",
    "confirmphone",
    "setlanguage",
    "bg",
    "m",
    "s",
    "c",
})

_POST_USERNAME = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)/(\d+)/?$",
    re.I,
)
_PRIVATE_POST = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/c/(\d+)/(\d+)/?$",
    re.I,
)
_PROFILE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]+)/?$",
    re.I,
)
_AT_USERNAME = re.compile(r"^@([A-Za-z0-9_]{4,})$")


class TelegramLinkResolver:
    @staticmethod
    def is_telegram_url(url: str) -> bool:
        text = url.strip()
        if text.startswith("@"):
            return bool(_AT_USERNAME.match(text))
        parsed = urlparse(text if "://" in text else f"https://{text}")
        host = parsed.netloc.lower().removeprefix("www.")
        return host in TELEGRAM_HOSTS

    @staticmethod
    def clean_url(url: str) -> str:
        text = url.strip()
        if text.startswith("@"):
            return text
        if not text.startswith("http"):
            text = f"https://{text}"
        parsed = urlparse(text)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{host}{path}"

    @classmethod
    def resolve(cls, url: str) -> ResolvedLink | None:
        raw = url.strip()
        if raw.startswith("@"):
            match = _AT_USERNAME.match(raw)
            if match:
                username = match.group(1)
                return ResolvedLink(
                    original_url=f"https://t.me/{username}",
                    entity_type=EntityType.PROFILE,
                    identifiers={"username": username},
                    platform=Platform.TELEGRAM,
                )
            return None

        clean = cls.clean_url(raw)

        match = _PRIVATE_POST.match(clean)
        if match:
            return ResolvedLink(
                original_url=clean,
                entity_type=EntityType.PUBLICATION,
                identifiers={
                    "channel_id": match.group(1),
                    "message_id": match.group(2),
                },
                platform=Platform.TELEGRAM,
            )

        match = _POST_USERNAME.match(clean)
        if match:
            username = match.group(1).lower()
            if username not in TELEGRAM_RESERVED:
                return ResolvedLink(
                    original_url=clean,
                    entity_type=EntityType.PUBLICATION,
                    identifiers={
                        "username": username,
                        "message_id": match.group(2),
                    },
                    platform=Platform.TELEGRAM,
                )

        match = _PROFILE.match(clean)
        if match:
            username = match.group(1).lower()
            if username in TELEGRAM_RESERVED:
                return None
            return ResolvedLink(
                original_url=clean,
                entity_type=EntityType.PROFILE,
                identifiers={"username": username},
                platform=Platform.TELEGRAM,
            )

        return None

    @staticmethod
    def channel_url(username: str) -> str:
        return f"https://t.me/{username.lstrip('@')}"

    @staticmethod
    def post_url(username: str, message_id: int | str) -> str:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"