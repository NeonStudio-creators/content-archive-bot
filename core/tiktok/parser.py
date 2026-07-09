"""
Парсинг ответов TikTok в ArchiveBundle.
"""

from __future__ import annotations

import re
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
from core.tiktok.audio_meta import extract_audio_sources
from core.tiktok.hq_meta import build_hq_downloads
from core.tiktok.profile_adapter import (
    extract_avatar_from_scope,
    extract_avatar_url,
    extract_stats_from_scope,
    extract_user_from_scope,
)
from utils.dict_utils import safe_dict


def _ts(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OSError, ValueError):
        return None


def _tags_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(re.findall(r"#(\w+)", text)))


class TikTokParser:
    """Преобразует сырые данные TikTok в ArchiveBundle."""

    def parse_video_quick(
        self,
        resolved: ResolvedLink,
        item: dict[str, Any],
    ) -> ArchiveBundle:
        return self._parse_video(resolved, item, deep=False)

    def parse_video_deep(
        self,
        resolved: ResolvedLink,
        item: dict[str, Any],
        *,
        comments: list[dict[str, Any]] | None = None,
    ) -> ArchiveBundle:
        bundle = self._parse_video(resolved, item, deep=True)
        if comments:
            for row in comments[:30]:
                bundle.activity.append(
                    ActivityRecord(
                        activity_type="comment",
                        actor=safe_dict(row.get("user")).get("unique_id"),
                        content=row.get("text"),
                        extra={"likes": row.get("digg_count", 0)},
                    )
                )
        return bundle

    def _parse_video(
        self,
        resolved: ResolvedLink,
        item: dict[str, Any],
        *,
        deep: bool,
    ) -> ArchiveBundle:
        source = item.get("_source", "mirror")
        canonical = item.get("_canonical_url") or resolved.original_url

        if source == "html":
            author = safe_dict(item.get("author"))
            stats = safe_dict(item.get("stats"))
            video = safe_dict(item.get("video"))
            music = safe_dict(item.get("music"))
            play_url = item.get("play") or video.get("playAddr")
            download_addr = video.get("downloadAddr")
            bitrate_info = item.get("bitrateInfo") or video.get("bitrateInfo") or []
            title = item.get("desc") or ""
            username = author.get("uniqueId")
            display = author.get("nickname")
            avatar = extract_avatar_url(author)
            video_id = str(item.get("id", ""))
            view_count = stats.get("playCount")
            like_count = stats.get("diggCount")
            comment_count = stats.get("commentCount")
            share_count = stats.get("shareCount")
            created = _ts(item.get("createTime"))
            duration = video.get("duration")
            cover = video.get("cover") or video.get("originCover")
            width = video.get("width")
            height = video.get("height")
            mirror_item = {
                "id": video_id,
                "title": title,
                "play": play_url,
                "hdplay": item.get("hdplay") or download_addr,
                "wmplay": item.get("wmplay"),
                "bitrateInfo": bitrate_info,
                "cover": cover,
                "duration": duration,
                "play_count": view_count,
                "digg_count": like_count,
                "comment_count": comment_count,
                "share_count": share_count,
                "create_time": item.get("createTime"),
                "author": {
                    "unique_id": username,
                    "nickname": display,
                    "avatar": avatar,
                },
                "music_info": {
                    "title": music.get("title"),
                    "author": music.get("authorName"),
                    "play": music.get("playUrl"),
                    "duration": music.get("duration"),
                    "original": music.get("original"),
                },
                "video": video,
            }
        else:
            mirror_item = item
            video_id = str(item.get("id", resolved.identifiers.get("video_id", "")))
            title = item.get("title") or item.get("desc") or ""
            author = safe_dict(item.get("author"))
            username = author.get("unique_id") or resolved.identifiers.get("username")
            display = author.get("nickname")
            avatar = author.get("avatar")
            view_count = item.get("play_count")
            like_count = item.get("digg_count")
            comment_count = item.get("comment_count")
            share_count = item.get("share_count")
            created = _ts(item.get("create_time"))
            duration = item.get("duration")
            cover = item.get("cover") or item.get("origin_cover")
            width = safe_dict(item.get("video")).get("width")
            height = safe_dict(item.get("video")).get("height")

        extra = build_hq_downloads(mirror_item)
        extra.update(extract_audio_sources(mirror_item))
        for key in ("play", "hdplay", "wmplay", "cover"):
            val = mirror_item.get(key)
            if isinstance(val, str) and val.startswith("http"):
                extra[key] = val
        extra["source"] = source
        if deep:
            extra["deep_collected"] = True

        preview_url = (
            extra.get("hq_best_url")
            or mirror_item.get("hdplay")
            or mirror_item.get("play")
            or cover
            or ""
        )

        media = [
            MediaAsset(
                id=video_id,
                media_type="video",
                url=preview_url,
                width=extra.get("width") or width,
                height=extra.get("height") or height,
                thumbnail_url=cover,
                duration_sec=float(duration) if duration else None,
                caption=title,
                taken_at=created,
                extra=extra,
            )
        ]

        metadata = EntityMetadata(
            entity_id=video_id,
            entity_type=EntityType.PUBLICATION,
            title=video_id,
            description=title,
            username=username,
            display_name=display,
            avatar_url=avatar,
            created_at=created,
            like_count=like_count,
            comment_count=comment_count,
            view_count=view_count,
            tags=_tags_from_text(title),
            raw_fields={
                "share_count": share_count,
                "collect_count": mirror_item.get("collect_count"),
                "region": mirror_item.get("region"),
                "owner_bio": None,
                "platform": "tiktok",
            },
        )

        return ArchiveBundle(
            source_url=canonical,
            resolved_type=EntityType.PUBLICATION,
            metadata=metadata,
            media=media,
            raw_graphql=[mirror_item] if deep else [],
        )

    def parse_profile(
        self,
        resolved: ResolvedLink,
        payload: dict[str, Any],
        *,
        post_items: list[dict[str, Any]] | None = None,
    ) -> ArchiveBundle:
        scope = payload.get("scope") or {}
        user = extract_user_from_scope(scope)
        stats = extract_stats_from_scope(scope)
        username = user.get("uniqueId") or resolved.identifiers.get("username", "")
        avatar = extract_avatar_url(user) or extract_avatar_from_scope(scope)

        bio_link = safe_dict(user.get("bioLink"))
        external_url = bio_link.get("link")

        metadata = EntityMetadata(
            entity_id=str(user.get("id", "")),
            entity_type=EntityType.PROFILE,
            username=username,
            display_name=user.get("nickname"),
            avatar_url=avatar,
            follower_count=stats.get("followerCount"),
            following_count=stats.get("followingCount"),
            publication_count=stats.get("videoCount"),
            like_count=stats.get("heartCount") or stats.get("heart"),
            is_verified=user.get("verified", False),
            is_private=user.get("privateAccount", False),
            external_url=external_url,
            biography=user.get("signature"),
            raw_fields={
                "sec_uid": user.get("secUid"),
                "avatar_medium": user.get("avatarMedium"),
                "avatar_larger": user.get("avatarLarger"),
                "bio_link": bio_link,
                "platform": "tiktok",
            },
        )

        media: list[MediaAsset] = []
        relations: list[RelationEdge] = []
        if avatar:
            media.append(
                MediaAsset(
                    id=f"avatar_{metadata.entity_id}",
                    media_type="image",
                    url=avatar,
                    extra={"source": "avatar"},
                )
            )

        aggregate_views = 0
        aggregate_likes = 0
        for node in post_items or []:
            vid = str(node.get("id", ""))
            sc = node.get("author", {}).get("unique_id") if isinstance(node.get("author"), dict) else username
            stats_node = safe_dict(node.get("stats"))
            views = stats_node.get("playCount") or node.get("play_count") or 0
            likes = stats_node.get("diggCount") or node.get("digg_count") or 0
            aggregate_views += int(views or 0)
            aggregate_likes += int(likes or 0)
            relations.append(
                RelationEdge(
                    relation_type="publication",
                    target_id=vid,
                    target_label=vid,
                    metadata={
                        "caption": (node.get("desc") or node.get("title") or "")[:120],
                        "views": views,
                        "likes": likes,
                        "username": sc,
                    },
                )
            )

        collection_stats = {
            "posts_collected": len(relations),
            "media_files": len(media),
        }
        if aggregate_views:
            metadata.raw_fields["aggregate_views"] = aggregate_views
        if aggregate_likes:
            metadata.raw_fields["aggregate_likes"] = aggregate_likes

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.PROFILE,
            metadata=metadata,
            media=media,
            relations=relations,
            raw_graphql=[payload],
            collection_stats=collection_stats,
        )