"""
EntityDeepCollector — парсинг GraphQL-ответов в структурированные модели.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.fetcher import ResolvedLink
from core.profile_adapter import (
    extract_avatar_from_profile_payload,
    extract_avatar_url,
)
from core.video_meta import build_video_technical, extract_video_versions, pick_best_version
from utils.dict_utils import dig, safe_dict
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


def _caption_text(node: dict[str, Any]) -> str | None:
    cap = node.get("caption")
    if isinstance(cap, str):
        return cap
    if isinstance(cap, dict):
        return cap.get("text")
    edges = safe_dict(node.get("edge_media_to_caption")).get("edges", [])
    if edges:
        return safe_dict(safe_dict(edges[0]).get("node")).get("text")
    return None


def _edge_count(node: dict[str, Any], *path: str) -> int | None:
    cur: Any = node
    for key in path:
        cur = safe_dict(cur).get(key)
    return cur if isinstance(cur, int) else None


def _best_image(node: dict[str, Any]) -> str | None:
    """Выбирает URL максимального разрешения из display_resources."""
    resources = node.get("display_resources") or safe_dict(
        node.get("image_versions2")
    ).get("candidates", [])
    if not resources:
        return node.get("display_url") or node.get("thumbnail_src")
    return max(resources, key=lambda r: r.get("config_width", 0) * r.get("config_height", 0)).get(
        "src"
    ) or resources[-1].get("url")


def _parse_mobile_media_item(item: dict[str, Any]) -> MediaAsset | None:
    """MediaAsset из mobile API (pk, code, image_versions2)."""
    pk = item.get("pk") or item.get("id")
    if not pk:
        return None

    media_type = item.get("media_type", 1)
    is_video = media_type == 2 or bool(item.get("video_versions"))

    url: str | None = None
    thumb: str | None = None
    width = height = None

    if is_video and item.get("video_versions"):
        best = item["video_versions"][-1]
        url = best.get("url")
        width = best.get("width")
        height = best.get("height")
    candidates = safe_dict(item.get("image_versions2")).get("candidates", [])
    if candidates:
        best_img = max(
            candidates,
            key=lambda c: c.get("width", 0) * c.get("height", 0),
        )
        thumb = best_img.get("url")
        if not url:
            url = thumb
            width = best_img.get("width")
            height = best_img.get("height")

    cap = item.get("caption")
    caption = cap.get("text") if isinstance(cap, dict) else cap

    return MediaAsset(
        id=str(pk),
        media_type="video" if is_video else "image",
        url=url or "",
        width=width,
        height=height,
        thumbnail_url=thumb,
        duration_sec=item.get("video_duration"),
        caption=caption,
        taken_at=_ts(item.get("taken_at")),
        extra={
            "shortcode": item.get("code"),
            "product_type": item.get("product_type"),
            "source": "mobile_api",
        },
    )


def _parse_edge_media(node: dict[str, Any]) -> list[MediaAsset]:
    """Универсальный парсер узла поста (GraphQL или mobile)."""
    if node.get("pk") or (node.get("code") and not node.get("shortcode")):
        asset = _parse_mobile_media_item(node)
        return [asset] if asset else []
    return _parse_media_node(node)


def _parse_story_item(item: dict[str, Any]) -> MediaAsset | None:
    is_video = item.get("media_type") == 2
    url = item.get("video_url") if is_video else _best_image(item)
    if not url:
        return None
    return MediaAsset(
        id=str(item.get("pk", "")),
        media_type="video" if is_video else "image",
        url=url,
        taken_at=_ts(item.get("taken_at")),
        extra={"story_type": item.get("story_cta"), "source": "story"},
    )


def _parse_profile_stories(
    profile_data: dict[str, Any], username: str
) -> tuple[list[MediaAsset], list[RelationEdge]]:
    """Активные сторис из ответа fetch_web_profile (include_reel=True)."""
    reels = safe_dict(dig(profile_data, "data", "user")).get(
        "reel"
    ) or profile_data.get("data", {}).get(
        "xdt_api__v1__feed__reels_media", {}
    )
    if isinstance(reels, dict) and "items" in reels:
        reel_list = [reels]
    elif isinstance(reels, dict):
        reel_list = list(reels.values())
    else:
        reel_list = []

    media: list[MediaAsset] = []
    relations: list[RelationEdge] = []

    for reel in reel_list:
        if not isinstance(reel, dict):
            continue
        owner = safe_dict(reel.get("user")).get("username") or username
        for item in reel.get("items", []) or []:
            asset = _parse_story_item(item)
            if asset:
                media.append(asset)
                relations.append(
                    RelationEdge(
                        relation_type="story",
                        target_id=asset.id,
                        target_label=owner,
                        metadata={"expires_at": item.get("expiring_at")},
                    )
                )
    return media, relations


def _parse_media_node(node: dict[str, Any]) -> list[MediaAsset]:
    """Извлекает MediaAsset из узла публикации (включая карусели)."""
    assets: list[MediaAsset] = []
    media_type = node.get("__typename", "") or node.get("media_type", "")

    # Карусель
    children = safe_dict(node.get("edge_sidecar_to_children")).get("edges", [])
    if children:
        for child_edge in children:
            child = safe_dict(child_edge.get("node"))
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

    dims = safe_dict(node.get("dimensions"))
    width = dims.get("width") or node.get("original_width") or (best_v or {}).get("width")
    height = dims.get("height") or node.get("original_height") or (best_v or {}).get("height")

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
            caption=_caption_text(node),
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
        highlight_edges: list[dict[str, Any]] | None = None,
        highlight_media: list[MediaAsset] | None = None,
        extra_activity: list[ActivityRecord] | None = None,
        raw_responses: list[dict[str, Any]] | None = None,
    ) -> ArchiveBundle:
        user = safe_dict(dig(profile_data, "data", "user")) or safe_dict(
            profile_data.get("user")
        )
        username = user.get("username") or resolved.identifiers.get("username", "")

        bio_links_raw = user.get("bio_links") or []
        bio_links = []
        for link in bio_links_raw:
            if isinstance(link, dict):
                bio_links.append(
                    {
                        "url": link.get("url") or link.get("lynx_url"),
                        "title": link.get("title") or link.get("link_type"),
                    }
                )
            elif isinstance(link, str):
                bio_links.append({"url": link, "title": None})

        avatar_url = extract_avatar_url(user) or extract_avatar_from_profile_payload(
            profile_data
        )

        metadata = EntityMetadata(
            entity_id=str(user.get("id", "")),
            entity_type=EntityType.PROFILE,
            username=username,
            display_name=user.get("full_name"),
            avatar_url=avatar_url,
            follower_count=_edge_count(user, "edge_followed_by", "count"),
            following_count=_edge_count(user, "edge_follow", "count"),
            publication_count=_edge_count(user, "edge_owner_to_timeline_media", "count"),
            is_verified=user.get("is_verified", False),
            is_private=user.get("is_private", False),
            external_url=user.get("external_url"),
            biography=user.get("biography"),
            raw_fields={
                "category": user.get("category_name") or user.get("category"),
                "business_email": user.get("business_email"),
                "business_phone": user.get("business_phone_number"),
                "is_business": user.get("is_business_account"),
                "is_professional": user.get("is_professional_account"),
                "pronouns": user.get("pronouns"),
                "bio_links": bio_links,
                "highlight_reel_count": user.get("highlight_reel_count"),
                "reels_total": _edge_count(
                    user, "edge_felix_video_timeline", "count"
                ),
                "tagged_total": _edge_count(
                    user, "edge_user_to_photos_of_you", "count"
                ),
                "has_active_story": bool(
                    safe_dict(user.get("reel")).get("has_reel_media_to_watch")
                    or user.get("has_ar_effects")
                ),
                "mutual_followers_count": user.get("mutual_followers_count"),
                "is_joined_recently": user.get("is_joined_recently"),
                "profile_pic_url": user.get("profile_pic_url"),
                "hd_profile_pic_url_info": user.get("hd_profile_pic_url_info"),
            },
        )

        media: list[MediaAsset] = []
        relations: list[RelationEdge] = []

        avatar_url = metadata.avatar_url
        if avatar_url:
            media.append(
                MediaAsset(
                    id=f"avatar_{metadata.entity_id}",
                    media_type="image",
                    url=avatar_url,
                    extra={"source": "avatar"},
                )
            )

        story_media, story_relations = _parse_profile_stories(
            profile_data, username
        )
        media.extend(story_media)
        relations.extend(story_relations)

        if highlight_media:
            media.extend(highlight_media)

        total_likes = 0
        total_comments = 0
        total_views = 0

        for edge in post_edges:
            node = safe_dict(edge.get("node"))
            media.extend(_parse_edge_media(node))
            likes = _edge_count(node, "edge_liked_by", "count") or 0
            comments = _edge_count(node, "edge_media_to_comment", "count") or 0
            views = node.get("video_view_count") or 0
            total_likes += likes
            total_comments += comments
            total_views += views
            relations.append(
                RelationEdge(
                    relation_type="publication",
                    target_id=str(node.get("id", "")),
                    target_label=node.get("shortcode", ""),
                    metadata={
                        "likes": likes,
                        "comments": comments,
                        "views": views,
                        "caption": (_caption_text(node) or "")[:120],
                        "is_video": node.get("is_video"),
                    },
                )
            )

        for edge in reel_edges or []:
            node = safe_dict(edge.get("node"))
            media.extend(_parse_edge_media(node))
            likes = _edge_count(node, "edge_liked_by", "count") or 0
            comments = _edge_count(node, "edge_media_to_comment", "count") or 0
            views = node.get("video_view_count") or 0
            total_likes += likes
            total_comments += comments
            total_views += views
            relations.append(
                RelationEdge(
                    relation_type="reel",
                    target_id=str(node.get("id", "")),
                    target_label=node.get("shortcode", ""),
                    metadata={
                        "product_type": node.get("product_type"),
                        "likes": likes,
                        "comments": comments,
                        "views": views,
                        "caption": (_caption_text(node) or "")[:120],
                    },
                )
            )

        for edge in tagged_edges or []:
            node = safe_dict(edge.get("node"))
            media.extend(_parse_edge_media(node))
            owner = safe_dict(node.get("owner")).get("username") or safe_dict(
                node.get("user")
            ).get("username")
            relations.append(
                RelationEdge(
                    relation_type="tagged_in",
                    target_id=str(node.get("id", node.get("pk", ""))),
                    target_label=node.get("shortcode", node.get("code", "")),
                    metadata={
                        "owner": owner,
                        "likes": _edge_count(node, "edge_liked_by", "count")
                        or node.get("like_count"),
                    },
                )
            )

        for edge in highlight_edges or []:
            node = safe_dict(edge.get("node"))
            hid = str(node.get("id", ""))
            relations.append(
                RelationEdge(
                    relation_type="highlight",
                    target_id=hid,
                    target_label=node.get("title", hid),
                    metadata={
                        "cover_url": (
                            safe_dict(node.get("cover_media")).get(
                                "thumbnail_src"
                            )
                            or safe_dict(
                                node.get("cover_media_cropped_thumbnail")
                            ).get("url")
                        ),
                        "items_count": node.get("media_count"),
                    },
                )
            )

        for edge in safe_dict(user.get("edge_related_profiles")).get("edges", []):
            rel_user = safe_dict(edge.get("node"))
            relations.append(
                RelationEdge(
                    relation_type="related_profile",
                    target_id=str(rel_user.get("id", "")),
                    target_label=rel_user.get("username", ""),
                    metadata={"is_verified": rel_user.get("is_verified")},
                )
            )

        metadata.raw_fields["aggregate_likes"] = total_likes
        metadata.raw_fields["aggregate_comments"] = total_comments
        metadata.raw_fields["aggregate_views"] = total_views

        graphql_archive = [profile_data]
        if raw_responses:
            graphql_archive.extend(raw_responses)

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.PROFILE,
            metadata=metadata,
            media=media,
            relations=relations,
            activity=extra_activity or [],
            raw_graphql=graphql_archive,
            collection_stats={
                "posts_collected": len(post_edges),
                "reels_collected": len(reel_edges or []),
                "tagged_collected": len(tagged_edges or []),
                "stories_collected": len(story_media),
                "highlights_collected": len(highlight_edges or []),
                "highlight_items": len(highlight_media or []),
                "related_profiles": sum(
                    1 for r in relations if r.relation_type == "related_profile"
                ),
                "media_files": len(media),
                "comments_sampled": sum(
                    1 for a in (extra_activity or [])
                    if a.activity_type == "comment"
                ),
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
        data_block = safe_dict(media_data.get("data"))
        media_node = (
            safe_dict(data_block.get("shortcode_media"))
            or safe_dict(data_block.get("xdt_shortcode_media"))
            or safe_dict(media_data.get("shortcode_media"))
            or safe_dict(media_data.get("xdt_shortcode_media"))
        )

        caption = _caption_text(media_node)
        tags = [t for t in (caption or "").split() if t.startswith("#")]
        mentions = [t[1:] for t in (caption or "").split() if t.startswith("@")]

        owner = safe_dict(media_node.get("owner"))
        loc = safe_dict(media_node.get("location"))
        metadata = EntityMetadata(
            entity_id=str(media_node.get("id", "")),
            entity_type=EntityType.PUBLICATION,
            title=media_node.get("shortcode"),
            description=caption,
            username=owner.get("username"),
            display_name=owner.get("full_name"),
            created_at=_ts(media_node.get("taken_at_timestamp")),
            like_count=_edge_count(media_node, "edge_media_preview_like", "count")
            or _edge_count(media_node, "edge_liked_by", "count"),
            comment_count=_edge_count(media_node, "edge_media_to_comment", "count"),
            view_count=media_node.get("video_view_count"),
            location=loc.get("name"),
            tags=tags,
            raw_fields={
                "product_type": media_node.get("product_type"),
                "is_video": media_node.get("is_video"),
                "mentions": mentions,
                "accessibility_caption": media_node.get("accessibility_caption"),
                "music_info": media_node.get("clips_music_attribution_info")
                or media_node.get("music_info"),
                "sponsor_tags": [
                    safe_dict(t.get("sponsor")).get("username")
                    for t in safe_dict(
                        media_node.get("edge_media_to_sponsor_user")
                    ).get("edges", [])
                ],
            },
        )

        if owner_profile:
            owner_user = safe_dict(dig(owner_profile, "data", "user"))
            if owner_user:
                metadata.raw_fields["owner_followers"] = _edge_count(
                    owner_user, "edge_followed_by", "count"
                )
                metadata.raw_fields["owner_posts"] = _edge_count(
                    owner_user, "edge_owner_to_timeline_media", "count"
                )
                metadata.raw_fields["owner_bio"] = owner_user.get("biography")

        media = _parse_media_node(media_node)
        video_assets = [a for a in media if a.media_type == "video"]
        if video_assets:
            metadata.raw_fields["video_technical"] = video_assets[0].extra

        relations: list[RelationEdge] = []
        activity: list[ActivityRecord] = []

        # Теги пользователей
        for edge in safe_dict(media_node.get("edge_media_to_tagged_user")).get(
            "edges", []
        ):
            tagged = safe_dict(safe_dict(edge.get("node")).get("user"))
            relations.append(
                RelationEdge(
                    relation_type="tagged_user",
                    target_id=str(tagged.get("id", "")),
                    target_label=tagged.get("username", ""),
                )
            )

        # Ко-авторы
        for edge in safe_dict(media_node.get("edge_media_to_cohost")).get("edges", []):
            cohost = safe_dict(edge.get("node"))
            relations.append(
                RelationEdge(
                    relation_type="cohost",
                    target_id=str(cohost.get("id", "")),
                    target_label=cohost.get("username", ""),
                )
            )

        # Комментарии
        for edge in comment_edges or []:
            comment = safe_dict(edge.get("node"))
            activity.append(
                ActivityRecord(
                    activity_type="comment",
                    actor=safe_dict(comment.get("owner")).get("username"),
                    content=comment.get("text"),
                    timestamp=_ts(comment.get("created_at")),
                    extra={
                        "likes": _edge_count(comment, "edge_liked_by", "count") or 0
                    },
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