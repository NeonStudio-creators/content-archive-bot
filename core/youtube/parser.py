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


def _parse_publish_date(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(value.replace("Z", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _format_duration(seconds: float | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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
        if duration:
            extra["duration_text"] = _format_duration(duration)
        if deep:
            extra["deep_collected"] = True

        preview_url = (
            extra.get("playback_best_url")
            or extra.get("hq_best_url")
            or thumbnail
            or ""
        )
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

        publish_date = micro.get("publishDate") or micro.get("uploadDate")
        created_at = _parse_publish_date(publish_date)

        metadata = EntityMetadata(
            entity_id=video_id,
            entity_type=EntityType.PUBLICATION,
            title=title or video_id,
            description=description or title,
            username=channel_id,
            display_name=channel or channel_id,
            avatar_url=None,
            view_count=view_count,
            like_count=None,
            comment_count=None,
            created_at=created_at,
            tags=[],
            raw_fields={
                "platform": "youtube",
                "channel_id": channel_id,
                "channel_name": channel,
                "video_title": title,
                "is_live": details.get("isLiveContent"),
                "category": micro.get("category"),
                "publish_date": publish_date,
                "duration_text": _format_duration(duration),
                "innertube_client": player.get("_client"),
                "has_audio": extra.get("has_audio"),
                "resolution": extra.get("resolution"),
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