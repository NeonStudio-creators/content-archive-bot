"""
EntityDeepCollector — парсинг GraphQL-ответов в структурированные модели.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.fetcher import ResolvedLink
from core.video_meta import build_video_technical, pick_best_version, extract_video_versions
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

    is_video = bool(
        node.get("is_video")
        or node.get("media_type") == 2
        or media_type in ("GraphVideo", "XDTVideoMedia")
        or node.get("video_url")
    )

    url = _best_image(node)
    versions = extract_video_versions(node)
    best_v = pick_best_version(versions)
    video_url = (best_v or {}).get("url") or node.get("video_url")

    width = (
        node.get("dimensions", {}).get("width")
        or node.get("original_width")
        or (best_v or {}).get("width")
    )
    height = (
        node.get("dimensions", {}).get("height")
        or node.get("original_height")
        or (best_v or {}).get("height")
    )

    extra: dict[str, Any] = {
        "shortcode": node.get("shortcode"),
        "product_type": node.get("product_type"),
    }

    if is_video:
        tech = build_video_technical(node)
        extra.update(tech)
        if tech.get("width"):
            width = tech["width"]
        if tech.get("height"):
            height = tech["height"]
        if tech.get("video_url_best"):
            video_url = tech["video_url_best"]

    assets.append(
        MediaAsset(
            id=str(node.get("id", "")),
            media_type="video" if is_video else "image",
            url=video_url or url or "",
            width=width,
            height=height,
            thumbnail_url=node.get("thumbnail_src") or url,
            duration_sec=node.get("video_duration") or extra.get("duration_sec"),
            caption=(
                node.get("edge_media_to_caption", {})
                .get("edges", [{}])[0]
                .get("node", {})
                .get("text")
                if node.get("edge_media_to_caption")
                else node.get("caption")
            ),
            taken_at=_ts(node.get("taken_at_timestamp") or node.get("taken_at")),
            extra=extra,
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
        reel_edges: list[dict[str, Any]] | None = None,
        tagged_edges: list[dict[str, Any]] | None = None,
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
                "is_professional": user.get("is_professional_account"),
                "pronouns": user.get("pronouns"),
                "bio_links": [
                    l.get("url") for l in user.get("bio_links", [])
                ],
                "highlight_reel_count": user.get("highlight_reel_count"),
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

        for edge in reel_edges or []:
            node = edge.get("node", {})
            media.extend(_parse_media_node(node))
            relations.append(
                RelationEdge(
                    relation_type="reel",
                    target_id=str(node.get("id", "")),
                    target_label=node.get("shortcode", ""),
                    metadata={"product_type": node.get("product_type")},
                )
            )

        for edge in tagged_edges or []:
            node = edge.get("node", {})
            relations.append(
                RelationEdge(
                    relation_type="tagged_in",
                    target_id=str(node.get("id", "")),
                    target_label=node.get("shortcode", ""),
                    metadata={
                        "owner": (node.get("owner") or {}).get("username"),
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
                    metadata={"is_verified": rel_user.get("is_verified")},
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
                "reels_collected": len(reel_edges or []),
                "tagged_collected": len(tagged_edges or []),
                "media_files": len(media),
            },
        )

    def parse_publication(
        self,
        resolved: ResolvedLink,
        media_data: dict[str, Any],
        comment_edges: list[dict[str, Any]] | None = None,
        *,
        likers: list[dict[str, Any]] | None = None,
        owner_profile: dict[str, Any] | None = None,
    ) -> ArchiveBundle:
        media_node = (
            media_data.get("data", {}).get("shortcode_media")
            or media_data.get("data", {}).get("xdt_shortcode_media")
            or media_data.get("shortcode_media")
            or media_data.get("xdt_shortcode_media")
            or {}
        )

        caption_edges = media_node.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0].get("node", {}).get("text") if caption_edges else None
        tags = [t for t in (caption or "").split() if t.startswith("#")]
        mentions = [t[1:] for t in (caption or "").split() if t.startswith("@")]

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
                "mentions": mentions,
                "accessibility_caption": media_node.get("accessibility_caption"),
                "music_info": media_node.get("clips_music_attribution_info")
                or media_node.get("music_info"),
                "sponsor_tags": [
                    t.get("sponsor", {}).get("username")
                    for t in media_node.get("edge_media_to_sponsor_user", {})
                    .get("edges", [])
                ],
            },
        )

        if owner_profile:
            owner_user = owner_profile.get("data", {}).get("user", {})
            if owner_user:
                metadata.raw_fields["owner_followers"] = owner_user.get(
                    "edge_followed_by", {}
                ).get("count")
                metadata.raw_fields["owner_posts"] = owner_user.get(
                    "edge_owner_to_timeline_media", {}
                ).get("count")
                metadata.raw_fields["owner_bio"] = owner_user.get("biography")

        media = _parse_media_node(media_node)
        video_assets = [a for a in media if a.media_type == "video"]
        if video_assets:
            metadata.raw_fields["video_technical"] = video_assets[0].extra

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

        # Комментарии
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

        # Лайкнувшие
        for liker in likers or []:
            activity.append(
                ActivityRecord(
                    activity_type="like",
                    actor=liker.get("username"),
                    content=None,
                    extra={
                        "full_name": liker.get("full_name"),
                        "is_verified": liker.get("is_verified"),
                    },
                )
            )
            relations.append(
                RelationEdge(
                    relation_type="liker",
                    target_id=str(liker.get("pk", "")),
                    target_label=liker.get("username", ""),
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
            collection_stats={
                "comments_collected": sum(
                    1 for a in activity if a.activity_type == "comment"
                ),
                "likers_collected": len(likers or []),
                "tagged_users": sum(
                    1 for r in relations if r.relation_type == "tagged_user"
                ),
            },
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