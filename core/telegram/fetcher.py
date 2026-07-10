"""
Telethon MTProto — подписчики и просмотры Telegram-каналов.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Message

from config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_POST_LIMIT = 10


def _reaction_total(message: Message) -> int | None:
    reactions = message.reactions
    if not reactions or not getattr(reactions, "results", None):
        return None
    return sum(r.count for r in reactions.results)


def _channel_peer_id(internal_id: int) -> int:
    """t.me/c/1234567890 → -1001234567890 для Telethon."""
    if internal_id < 0:
        return internal_id
    return int(f"-100{internal_id}")


class TelegramChannelFetcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: TelegramClient | None = None
        self._lock = asyncio.Lock()

    def is_configured(self) -> bool:
        return bool(self.settings.telegram_session)

    def _require_config(self) -> None:
        if not self.settings.telegram_session:
            raise ValueError(
                "Задайте TELEGRAM_SESSION. Без VPN: python scripts/telegram_login.py"
            )

    def _build_client(self) -> TelegramClient:
        self._require_config()
        return TelegramClient(
            StringSession(self.settings.telegram_session),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
            device_model="ContentExplorer",
            system_version="1.0",
            app_version="1.0",
        )

    async def ensure_connected(self) -> TelegramClient:
        async with self._lock:
            if self._client is None:
                self._client = self._build_client()
            if not self._client.is_connected():
                await self._client.connect()
            if not await self._client.is_user_authorized():
                raise ValueError(
                    "TELEGRAM_SESSION недействителен — повторите scripts/telegram_login.py"
                )
            return self._client

    async def close(self) -> None:
        async with self._lock:
            if self._client and self._client.is_connected():
                await self._client.disconnect()
            self._client = None

    async def _resolve_entity(
        self,
        *,
        username: str | None = None,
        channel_id: str | None = None,
    ) -> Channel:
        client = await self.ensure_connected()
        if channel_id:
            peer = _channel_peer_id(int(channel_id))
            entity = await client.get_entity(peer)
        elif username:
            entity = await client.get_entity(username.lstrip("@"))
        else:
            raise ValueError("Не указан канал Telegram")
        if not isinstance(entity, Channel):
            raise ValueError("Ссылка не ведёт на канал")
        return entity

    async def _channel_meta(self, entity: Channel) -> dict[str, Any]:
        client = await self.ensure_connected()
        full = await client(GetFullChannelRequest(channel=entity))
        username = getattr(entity, "username", None)
        return {
            "username": username,
            "title": getattr(entity, "title", None),
            "channel_id": str(entity.id),
            "participants_count": full.full_chat.participants_count,
            "about": (full.full_chat.about or "")[:500] or None,
        }

    @staticmethod
    def _message_row(msg: Message) -> dict[str, Any]:
        return {
            "id": msg.id,
            "views": msg.views,
            "forwards": msg.forwards,
            "reactions": _reaction_total(msg),
            "date": msg.date.isoformat() if msg.date else None,
            "text": (msg.message or "")[:300] or None,
        }

    async def fetch_channel(
        self,
        *,
        username: str | None = None,
        channel_id: str | None = None,
        post_limit: int = _DEFAULT_POST_LIMIT,
    ) -> dict[str, Any]:
        entity = await self._resolve_entity(username=username, channel_id=channel_id)
        meta = await self._channel_meta(entity)
        client = await self.ensure_connected()

        posts: list[dict[str, Any]] = []
        aggregate_views = 0
        async for msg in client.iter_messages(entity, limit=post_limit):
            if not isinstance(msg, Message):
                continue
            row = self._message_row(msg)
            posts.append(row)
            aggregate_views += int(row.get("views") or 0)

        meta["posts"] = posts
        meta["aggregate_views"] = aggregate_views
        meta["posts_sampled"] = len(posts)
        return meta

    async def fetch_post(
        self,
        *,
        message_id: int,
        username: str | None = None,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        entity = await self._resolve_entity(username=username, channel_id=channel_id)
        client = await self.ensure_connected()
        msg = await client.get_messages(entity, ids=message_id)
        if not msg or not isinstance(msg, Message):
            raise ValueError(f"Пост {message_id} не найден")

        meta = await self._channel_meta(entity)
        row = self._message_row(msg)
        return {
            **meta,
            "post": row,
            "message_id": message_id,
        }