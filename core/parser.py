"""
EntityDeepCollector — парсинг GraphQL-ответов в структурированные модели.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.fetcher import ResolvedLink
from core.models import (
    ActivityRecord,
    ArchiveBundle,
    EntityMetadata,
    EntityType,
    MediaAsset,
    RelationEdge,
)

logger = logging.getLogger(__name__)


def _ts(unix: int | float | None) -> datetime | None:
    if unix is None:
        return None
    try:
        return datetime.fromtimestamp(float(unix), tz=timezone.utc)
    except (OSError, ValueError):
        return None


def _best_image(node: dict[str, Any]) -> str | None:
    """Выбирает URL максимального разрешения из display_resources."""
    resources = node.get("display_resources") or node.get("image_versions2", {}).get(
        "candidates", []
    )
    if not resources:
        return node.get("display_url") or node.get("thumbnail_src")
    return max(resources, key=lambda r: r.get("config_width", 0) * r.get("config_height", 0)).get(
        "src"
    ) or resources[-1].get("url")


def _parse_media_node(node: dict[str, Any]) -> list[MediaAsset]:
    """Извлекает MediaAsset из узла публикации (включая карусели)."""
    assets: list[MediaAsset] = []
    media_type = node.get("__typename", "") or node.get("media_type", "")

    # Карусель
    children = node.get("edge_sidecar_to_children", {}).get("edges", [])
    if children:
        for child_edge in children:
            child = child_edge.get("node", {})
            assets.extend(_parse_media_node(child))
        return assets

    is_video = node.get("is_video") or media_type in ("GraphVideo", "XDTVideoMedia")
    url = _best_image(node)
    video_url = node.get("video_url")

    assets.append(
        MediaAsset(
            id=str(node.get("id", "")),
            media_type="video" if is_video else "image",
            url=video_url or url or "",
            width=node.get("dimensions", {}).get("width"),
            height=node.get("dimensions", {}).get("height"),
            thumbnail_url=node.get("thumbnail_src") or url,
            duration_sec=node.get("video_duration"),
            caption=(
                node.get("edge_media_to_caption", {})
                .get("edges", [{}])[0]
                .get("node", {})
                .get("text")
                if node.get("edge_media_to_caption")
                else node.get("caption")
            ),
            taken_at=_ts(node.get("taken_at_timestamp") or node.get("taken_at")),
            extra={
                "shortcode": node.get("shortcode"),
                "product_type": node.get("product_type"),
            },
        )
    )
    return assets


class EntityDeepCollector:
    """Преобразует сырые GraphQL-данные в ArchiveBundle."""

    def parse_profile(
        self,
        resolved: ResolvedLink,
        profile_data: dict[str, Any],
        post_edges: list[dict[str, Any]],
    ) -> ArchiveBundle:
        user = (
            profile_data.get("data", {}).get("user")
            or profile_data.get("user")
            or {}
        )

        metadata = EntityMetadata(
            entity_id=str(user.get("id", "")),
            entity_type=EntityType.PROFILE,
            username=user.get("username"),
            display_name=user.get("full_name"),
            avatar_url=user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
            follower_count=user.get("edge_followed_by", {}).get("count"),
            following_count=user.get("edge_follow", {}).get("count"),
            publication_count=user.get("edge_owner_to_timeline_media", {}).get("count"),
            is_verified=user.get("is_verified", False),
            is_private=user.get("is_private", False),
            external_url=user.get("external_url"),
            biography=user.get("biography"),
            raw_fields={
                "category": user.get("category_name"),
                "business_email": user.get("business_email"),
                "is_business": user.get("is_business_account"),
            },
        )

        media: list[MediaAsset] = []
        relations: list[RelationEdge] = []

        for edge in post_edges:
            node = edge.get("node", {})
            media.extend(_parse_media_node(node))
            relations.append(
                RelationEdge(
                    relation_type="publication",
                    target_id=str(node.get("id", "")),
                    target_label=node.get("shortcode", ""),
                    metadata={
                        "likes": node.get("edge_liked_by", {}).get("count"),
                        "comments": node.get("edge_media_to_comment", {}).get("count"),
                    },
                )
            )

        # Связанные аккаунты
        for edge in user.get("edge_related_profiles", {}).get("edges", []):
            rel_user = edge.get("node", {})
            relations.append(
                RelationEdge(
                    relation_type="related_profile",
                    target_id=str(rel_user.get("id", "")),
                    target_label=rel_user.get("username", ""),
                )
            )

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.PROFILE,
            metadata=metadata,
            media=media,
            relations=relations,
            raw_graphql=[profile_data],
            collection_stats={
                "posts_collected": len(post_edges),
                "media_files": len(media),
            },
        )

    def parse_publication(
        self,
        resolved: ResolvedLink,
        media_data: dict[str, Any],
        comment_edges: list[dict[str, Any]] | None = None,
    ) -> ArchiveBundle:
        media_node = (
            media_data.get("data", {}).get("shortcode_media")
            or media_data.get("shortcode_media")
            or media_data.get("data", {}).get("xdt_shortcode_media")
            or {}
        )

        caption_edges = media_node.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0].get("node", {}).get("text") if caption_edges else None
        tags = [t for t in (caption or "").split() if t.startswith("#")]

        owner = media_node.get("owner", {})
        metadata = EntityMetadata(
            entity_id=str(media_node.get("id", "")),
            entity_type=EntityType.PUBLICATION,
            title=media_node.get("shortcode"),
            description=caption,
            username=owner.get("username"),
            display_name=owner.get("full_name"),
            created_at=_ts(media_node.get("taken_at_timestamp")),
            like_count=media_node.get("edge_media_preview_like", {}).get("count")
            or media_node.get("edge_liked_by", {}).get("count"),
            comment_count=media_node.get("edge_media_to_comment", {}).get("count"),
            view_count=media_node.get("video_view_count"),
            location=media_node.get("location", {}).get("name") if media_node.get("location") else None,
            tags=tags,
            raw_fields={
                "product_type": media_node.get("product_type"),
                "is_video": media_node.get("is_video"),
            },
        )

        media = _parse_media_node(media_node)
        relations: list[RelationEdge] = []
        activity: list[ActivityRecord] = []

        # Теги пользователей
        for edge in media_node.get("edge_media_to_tagged_user", {}).get("edges", []):
            tagged = edge.get("node", {}).get("user", {})
            relations.append(
                RelationEdge(
                    relation_type="tagged_user",
                    target_id=str(tagged.get("id", "")),
                    target_label=tagged.get("username", ""),
                )
            )

        # Ко-авторы
        for edge in media_node.get("edge_media_to_cohost", {}).get("edges", []):
            cohost = edge.get("node", {})
            relations.append(
                RelationEdge(
                    relation_type="cohost",
                    target_id=str(cohost.get("id", "")),
                    target_label=cohost.get("username", ""),
                )
            )

        # Комментарии как слой активности
        for edge in comment_edges or []:
            comment = edge.get("node", {})
            activity.append(
                ActivityRecord(
                    activity_type="comment",
                    actor=comment.get("owner", {}).get("username"),
                    content=comment.get("text"),
                    timestamp=_ts(comment.get("created_at")),
                    extra={"likes": comment.get("edge_liked_by", {}).get("count", 0)},
                )
            )

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.PUBLICATION,
            metadata=metadata,
            media=media,
            relations=relations,
            activity=activity,
            raw_graphql=[media_data],
            collection_stats={"comments_collected": len(activity)},
        )

    def parse_story(
        self,
        resolved: ResolvedLink,
        story_data: dict[str, Any],
    ) -> ArchiveBundle:
        reels = story_data.get("data", {}).get("xdt_api__v1__feed__reels_media", {})
        username = resolved.identifiers.get("username", "")
        story_id = resolved.identifiers.get("story_id", "")

        items: list[dict[str, Any]] = []
        for reel in reels.values() if isinstance(reels, dict) else []:
            for item in reel.get("items", []):
                if str(item.get("pk", "")) == story_id or not story_id:
                    items.append(item)

        media: list[MediaAsset] = []
        for item in items:
            is_video = item.get("media_type") == 2
            url = item.get("video_url") if is_video else _best_image(item)
            media.append(
                MediaAsset(
                    id=str(item.get("pk", "")),
                    media_type="video" if is_video else "image",
                    url=url or "",
                    taken_at=_ts(item.get("taken_at")),
                    extra={"story_type": item.get("story_cta")},
                )
            )

        metadata = EntityMetadata(
            entity_id=story_id,
            entity_type=EntityType.STORY,
            username=username,
            publication_count=len(items),
        )

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.STORY,
            metadata=metadata,
            media=media,
            raw_graphql=[story_data],
            collection_stats={"stories_collected": len(items)},
        )

    def parse_highlight(
        self,
        resolved: ResolvedLink,
        highlight_data: dict[str, Any],
    ) -> ArchiveBundle:
        highlight_id = resolved.identifiers.get("highlight_id", "")
        reels = (
            highlight_data.get("data", {})
            .get("xdt_api__v1__feed__reels_media__connection", {})
            .get("edges", [])
        )

        media: list[MediaAsset] = []
        title = None
        for edge in reels:
            node = edge.get("node", {})
            if str(node.get("id", "")) == highlight_id:
                title = node.get("title")
                for item in node.get("items", []):
                    is_video = item.get("media_type") == 2
                    url = item.get("video_url") if is_video else _best_image(item)
                    media.append(
                        MediaAsset(
                            id=str(item.get("pk", "")),
                            media_type="video" if is_video else "image",
                            url=url or "",
                            taken_at=_ts(item.get("taken_at")),
                        )
                    )

        metadata = EntityMetadata(
            entity_id=highlight_id,
            entity_type=EntityType.HIGHLIGHT,
            title=title,
            publication_count=len(media),
        )

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.HIGHLIGHT,
            metadata=metadata,
            media=media,
            raw_graphql=[highlight_data],
            collection_stats={"items_collected": len(media)},
        )

    def parse_collection(
        self,
        resolved: ResolvedLink,
        collection_edges: list[dict[str, Any]],
    ) -> ArchiveBundle:
        username = resolved.identifiers.get("username", "")
        collection_id = resolved.identifiers.get("collection_id", "")

        media: list[MediaAsset] = []
        relations: list[RelationEdge] = []

        for edge in collection_edges:
            node = edge.get("node", {})
            media.extend(_parse_media_node(node))
            relations.append(
                RelationEdge(
                    relation_type="saved_publication",
                    target_id=str(node.get("id", "")),
                    target_label=node.get("shortcode", ""),
                )
            )

        metadata = EntityMetadata(
            entity_id=collection_id,
            entity_type=EntityType.COLLECTION,
            username=username,
            publication_count=len(collection_edges),
        )

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.COLLECTION,
            metadata=metadata,
            media=media,
            relations=relations,
            collection_stats={
                "saved_items": len(collection_edges),
                "media_files": len(media),
            },
        )