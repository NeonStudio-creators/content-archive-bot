"""Модели ответов Stats API."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from api.middleware import API_VERSION


@dataclass
class StatsPayload:
    """Подписчики, просмотры и связанные метрики."""

    followers: int | None = None
    following: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    publications: int | None = None
    aggregate_views: int | None = None
    forwards: int | None = None
    reactions: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class StatsResponse:
    ok: bool
    platform: str
    entity_type: str
    url: str
    username: str | None = None
    display_name: str | None = None
    stats: StatsPayload = field(default_factory=StatsPayload)
    error: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": self.ok,
            "api_version": API_VERSION,
            "platform": self.platform,
            "entity_type": self.entity_type,
            "url": self.url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.username:
            body["username"] = self.username
        if self.display_name:
            body["display_name"] = self.display_name
        stats = self.stats.to_dict()
        if stats:
            body["stats"] = stats
        if self.error:
            body["error"] = self.error
        if self.extra:
            body["extra"] = self.extra
        return body