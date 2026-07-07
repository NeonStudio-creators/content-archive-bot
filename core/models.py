"""
Модели данных для структурированного архива.
Нейтральные названия без привязки к конкретной платформе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    """Типы сущностей, поддерживаемые LinkResolver."""

    PROFILE = "profile"
    PUBLICATION = "publication"
    STORY = "story"
    COLLECTION = "collection"
    HIGHLIGHT = "highlight"
    UNKNOWN = "unknown"


@dataclass
class MediaAsset:
    """Единица медиа-контента с прямой ссылкой на файл."""

    id: str
    media_type: str  # image | video | carousel
    url: str
    width: int | None = None
    height: int | None = None
    thumbnail_url: str | None = None
    duration_sec: float | None = None
    caption: str | None = None
    taken_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityMetadata:
    """Полные метаданные сущности."""

    entity_id: str
    entity_type: EntityType
    title: str | None = None
    description: str | None = None
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    publication_count: int | None = None
    is_verified: bool = False
    is_private: bool = False
    external_url: str | None = None
    biography: str | None = None
    created_at: datetime | None = None
    like_count: int | None = None
    comment_count: int | None = None
    view_count: int | None = None
    location: str | None = None
    tags: list[str] = field(default_factory=list)
    raw_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelationEdge:
    """Связь между сущностями (контекст окружения)."""

    relation_type: str
    target_id: str
    target_label: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActivityRecord:
    """Дополнительный слой активности (комментарии, взаимодействия)."""

    activity_type: str
    actor: str | None
    content: str | None
    timestamp: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchiveBundle:
    """Полный результат глубокого сбора данных."""

    source_url: str
    resolved_type: EntityType
    metadata: EntityMetadata
    media: list[MediaAsset] = field(default_factory=list)
    relations: list[RelationEdge] = field(default_factory=list)
    activity: list[ActivityRecord] = field(default_factory=list)
    raw_graphql: list[dict[str, Any]] = field(default_factory=list)
    collection_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Сериализация для JSON-дампа."""

        def _dt(v: datetime | None) -> str | None:
            return v.isoformat() if v else None

        return {
            "source_url": self.source_url,
            "resolved_type": self.resolved_type.value,
            "metadata": {
                "entity_id": self.metadata.entity_id,
                "entity_type": self.metadata.entity_type.value,
                "title": self.metadata.title,
                "description": self.metadata.description,
                "username": self.metadata.username,
                "display_name": self.metadata.display_name,
                "avatar_url": self.metadata.avatar_url,
                "follower_count": self.metadata.follower_count,
                "following_count": self.metadata.following_count,
                "publication_count": self.metadata.publication_count,
                "is_verified": self.metadata.is_verified,
                "is_private": self.metadata.is_private,
                "external_url": self.metadata.external_url,
                "biography": self.metadata.biography,
                "created_at": _dt(self.metadata.created_at),
                "like_count": self.metadata.like_count,
                "comment_count": self.metadata.comment_count,
                "view_count": self.metadata.view_count,
                "location": self.metadata.location,
                "tags": self.metadata.tags,
            },
            "media": [
                {
                    "id": m.id,
                    "media_type": m.media_type,
                    "url": m.url,
                    "width": m.width,
                    "height": m.height,
                    "thumbnail_url": m.thumbnail_url,
                    "duration_sec": m.duration_sec,
                    "caption": m.caption,
                    "taken_at": _dt(m.taken_at),
                }
                for m in self.media
            ],
            "relations": [
                {
                    "relation_type": r.relation_type,
                    "target_id": r.target_id,
                    "target_label": r.target_label,
                    "metadata": r.metadata,
                }
                for r in self.relations
            ],
            "activity": [
                {
                    "activity_type": a.activity_type,
                    "actor": a.actor,
                    "content": a.content,
                    "timestamp": _dt(a.timestamp),
                    "extra": a.extra,
                }
                for a in self.activity
            ],
            "collection_stats": self.collection_stats,
        }