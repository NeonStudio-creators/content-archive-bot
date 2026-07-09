"""
Fallback Piped / Invidious когда InnerTube недоступен с IP датацентра.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


def _piped_quality_height(quality: object) -> int | None:
    if isinstance(quality, int):
        return quality
    if not isinstance(quality, str):
        return None
    digits = "".join(ch for ch in quality if ch.isdigit())
    return int(digits) if digits else None

PIPED_API_BASES: tuple[str, ...] = (
    "https://pipedapi.in.projectsegfau.lt",
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.privacyredirect.com",
)

INVIDIOUS_API_BASES: tuple[str, ...] = (
    "https://vid.puffyan.us",
    "https://invidious.nerdvpn.de",
    "https://yt.artemislena.eu",
    "https://yewtu.be",
)


def _piped_to_player(data: dict[str, Any], *, video_id: str) -> dict[str, Any]:
    progressive: list[dict[str, Any]] = []
    adaptive: list[dict[str, Any]] = []

    for stream in data.get("videoStreams") or []:
        url = stream.get("url") or ""
        if not url.startswith("http"):
            continue
        progressive.append(
            {
                "url": url,
                "mimeType": "video/mp4; codecs=\"avc1.4D401E, mp4a.40.2\"",
                "width": None,
                "height": _piped_quality_height(stream.get("quality")),
                "bitrate": int((stream.get("bitrate") or 0) * 1000) or None,
            }
        )

    for stream in data.get("audioStreams") or []:
        url = stream.get("url") or ""
        if not url.startswith("http"):
            continue
        adaptive.append(
            {
                "url": url,
                "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
                "bitrate": int((stream.get("bitrate") or 0) * 1000) or None,
            }
        )

    thumb = data.get("thumbnailUrl")
    return {
        "videoDetails": {
            "videoId": video_id,
            "title": data.get("title") or "",
            "shortDescription": data.get("description") or "",
            "author": data.get("uploader") or "",
            "channelId": data.get("uploaderUrl", "").split("/")[-1],
            "viewCount": str(data.get("views") or 0),
            "lengthSeconds": str(int(data.get("duration") or 0)),
            "thumbnail": {"thumbnails": [{"url": thumb}]} if thumb else {},
        },
        "streamingData": {
            "formats": progressive,
            "adaptiveFormats": adaptive,
        },
        "playabilityStatus": {"status": "OK"},
        "_client": "piped",
    }


def _invidious_to_player(data: dict[str, Any], *, video_id: str) -> dict[str, Any]:
    progressive: list[dict[str, Any]] = []
    adaptive: list[dict[str, Any]] = []

    for stream in data.get("formatStreams") or []:
        url = stream.get("url") or ""
        if not url.startswith("http"):
            continue
        progressive.append(
            {
                "url": url,
                "mimeType": "video/mp4; codecs=\"avc1.4D401E, mp4a.40.2\"",
                "width": stream.get("size") or stream.get("resolution"),
                "height": None,
                "bitrate": stream.get("bitrate"),
            }
        )

    for stream in data.get("adaptiveFormats") or []:
        url = stream.get("url") or ""
        if not url.startswith("http"):
            continue
        mime = stream.get("type") or "video/mp4"
        adaptive.append(
            {
                "url": url,
                "mimeType": mime,
                "width": stream.get("width"),
                "height": stream.get("height"),
                "bitrate": stream.get("bitrate"),
            }
        )

    thumbs = data.get("videoThumbnails") or []
    return {
        "videoDetails": {
            "videoId": video_id,
            "title": data.get("title") or "",
            "shortDescription": data.get("description") or "",
            "author": data.get("author") or "",
            "channelId": data.get("authorId") or "",
            "viewCount": str(data.get("viewCount") or 0),
            "lengthSeconds": str(int(data.get("lengthSeconds") or 0)),
            "thumbnail": {
                "thumbnails": [
                    {"url": t["url"]}
                    for t in thumbs
                    if isinstance(t, dict) and t.get("url")
                ]
            },
        },
        "streamingData": {
            "formats": progressive,
            "adaptiveFormats": adaptive,
        },
        "playabilityStatus": {"status": "OK"},
        "_client": "invidious",
    }


async def fetch_via_mirrors(
    session: aiohttp.ClientSession,
    video_id: str,
) -> dict[str, Any] | None:
    timeout = aiohttp.ClientTimeout(total=25, connect=10)
    headers = {
        "User-Agent": "ContentExplorer/1.0",
        "Accept": "application/json",
    }

    for base in PIPED_API_BASES:
        url = f"{base.rstrip('/')}/streams/{video_id}"
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    continue
                player = _piped_to_player(data, video_id=video_id)
                if player.get("streamingData", {}).get("formats") or player.get(
                    "streamingData", {}
                ).get("adaptiveFormats"):
                    logger.info("youtube mirror: piped %s", base)
                    return player
        except Exception as exc:
            logger.debug("piped %s: %s", base, exc)

    for base in INVIDIOUS_API_BASES:
        url = f"{base.rstrip('/')}/api/v1/videos/{video_id}"
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    continue
                player = _invidious_to_player(data, video_id=video_id)
                if player.get("streamingData", {}).get("formats") or player.get(
                    "streamingData", {}
                ).get("adaptiveFormats"):
                    logger.info("youtube mirror: invidious %s", base)
                    return player
        except Exception as exc:
            logger.debug("invidious %s: %s", base, exc)

    return None