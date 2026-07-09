"""
Парсинг ответов YouTube в ArchiveBundle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.fetcher import ResolvedLink
from core.models import ArchiveBundle, EntityMetadata, EntityType, MediaAsset
from core.youtube.audio_meta import extract_audio_sources
from core.youtube.hq_meta import build_hq_downloads


def _parse_count(text: str | None) -> int | None:
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


class YouTubeParser:
    def parse_video_quick(
        self,
        resolved: ResolvedLink,
        player: dict[str, Any],
    ) -> ArchiveBundle:
        return self._parse_video(resolved, player, deep=False)

    def parse_video_deep(
        self,
        resolved: ResolvedLink,
        player: dict[str, Any],
    ) -> ArchiveBundle:
        return self._parse_video(resolved, player, deep=True)

    def _parse_video(
        self,
        resolved: ResolvedLink,
        player: dict[str, Any],
        *,
        deep: bool,
    ) -> ArchiveBundle:
        details = player.get("videoDetails") or {}
        micro = player.get("microformat", {}).get("playerMicroformatRenderer") or {}

        video_id = (
            player.get("_video_id")
            or details.get("videoId")
            or resolved.identifiers.get("video_id", "")
        )
        title = details.get("title") or ""
        description = details.get("shortDescription") or ""
        channel = details.get("author") or ""
        channel_id = details.get("channelId") or micro.get("externalChannelId")
        view_count = None
        if details.get("viewCount"):
            try:
                view_count = int(details["viewCount"])
            except (TypeError, ValueError):
                pass

        thumbs = details.get("thumbnail", {}).get("thumbnails") or []
        thumbnail = thumbs[-1]["url"] if thumbs else None

        duration = None
        if details.get("lengthSeconds"):
            try:
                duration = float(details["lengthSeconds"])
            except (TypeError, ValueError):
                pass

        extra = build_hq_downloads(player)
        extra.update(extract_audio_sources(player))
        extra["source"] = player.get("_client", "innertube")
        extra["platform"] = "youtube"
        if deep:
            extra["deep_collected"] = True

        preview_url = extra.get("hq_best_url") or thumbnail or ""
        media = [
            MediaAsset(
                id=video_id,
                media_type="video",
                url=preview_url,
                width=extra.get("width"),
                height=extra.get("height"),
                thumbnail_url=thumbnail,
                duration_sec=duration,
                caption=title,
                extra=extra,
            )
        ]

        metadata = EntityMetadata(
            entity_id=video_id,
            entity_type=EntityType.PUBLICATION,
            title=video_id,
            description=description or title,
            username=channel_id,
            display_name=channel,
            avatar_url=None,
            view_count=view_count,
            like_count=None,
            comment_count=None,
            created_at=None,
            tags=[],
            raw_fields={
                "platform": "youtube",
                "channel_id": channel_id,
                "channel_name": channel,
                "is_live": details.get("isLiveContent"),
                "category": micro.get("category"),
                "publish_date": micro.get("publishDate"),
            },
        )

        return ArchiveBundle(
            source_url=player.get("_canonical_url") or resolved.original_url,
            resolved_type=EntityType.PUBLICATION,
            metadata=metadata,
            media=media,
            raw_graphql=[player] if deep else [],
        )

    def parse_profile(
        self,
        resolved: ResolvedLink,
        payload: dict[str, Any],
    ) -> ArchiveBundle:
        handle = payload.get("handle") or resolved.identifiers.get("handle")
        channel_id = payload.get("channel_id") or resolved.identifiers.get(
            "channel_id"
        )
        title = payload.get("title") or handle or channel_id or ""
        avatar = payload.get("avatar")
        subs = _parse_count(payload.get("subscriber_text"))

        metadata = EntityMetadata(
            entity_id=channel_id or handle or "",
            entity_type=EntityType.PROFILE,
            username=handle or channel_id,
            display_name=title,
            avatar_url=avatar,
            follower_count=subs,
            biography=None,
            raw_fields={
                "platform": "youtube",
                "channel_id": channel_id,
                "handle": handle,
                "subscriber_text": payload.get("subscriber_text"),
            },
        )

        media: list[MediaAsset] = []
        if avatar:
            media.append(
                MediaAsset(
                    id=f"avatar_{metadata.entity_id}",
                    media_type="image",
                    url=avatar,
                    extra={"source": "avatar"},
                )
            )

        return ArchiveBundle(
            source_url=resolved.original_url,
            resolved_type=EntityType.PROFILE,
            metadata=metadata,
            media=media,
            raw_graphql=[payload],
        )