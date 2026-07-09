"""
Fallback через yt-dlp (обход bot-check / poToken на Railway).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from typing import Any

logger = logging.getLogger(__name__)

_YTDLP_TIMEOUT_SEC = 90


def _ytdlp_executable() -> list[str]:
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def _mime_for_format(fmt: dict[str, Any]) -> str:
    ext = (fmt.get("ext") or "mp4").lower()
    vcodec = fmt.get("vcodec") or ""
    acodec = fmt.get("acodec") or ""
    if ext == "webm":
        vc = "vp9" if "vp9" in str(vcodec) else "vp8"
        if acodec and acodec != "none":
            return f"video/webm; codecs=\"{vc}, opus\""
        return f"video/webm; codecs=\"{vc}\""
    if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
        return "audio/mp4; codecs=\"mp4a.40.2\""
    return "video/mp4; codecs=\"avc1.4D401E, mp4a.40.2\""


def ytdlp_info_to_player(info: dict[str, Any], *, video_id: str) -> dict[str, Any]:
    progressive: list[dict[str, Any]] = []
    adaptive: list[dict[str, Any]] = []

    for fmt in info.get("formats") or []:
        url = fmt.get("url") or ""
        if not url.startswith("http"):
            continue
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        has_video = vcodec and vcodec != "none"
        has_audio = acodec and acodec != "none"
        if not has_video and not has_audio:
            continue

        entry: dict[str, Any] = {
            "url": url,
            "mimeType": _mime_for_format(fmt),
            "width": fmt.get("width"),
            "height": fmt.get("height"),
            "fps": fmt.get("fps"),
            "bitrate": int((fmt.get("tbr") or 0) * 1000) or None,
            "contentLength": fmt.get("filesize") or fmt.get("filesize_approx"),
            "itag": fmt.get("format_id"),
        }
        if has_video and has_audio:
            progressive.append(entry)
        else:
            adaptive.append(entry)

    thumbs = []
    thumb = info.get("thumbnail")
    if thumb:
        thumbs.append({"url": thumb})
    for t in info.get("thumbnails") or []:
        if isinstance(t, dict) and t.get("url"):
            thumbs.append({"url": t["url"]})

    return {
        "videoDetails": {
            "videoId": video_id,
            "title": info.get("title") or "",
            "shortDescription": info.get("description") or "",
            "author": info.get("channel") or info.get("uploader") or "",
            "channelId": info.get("channel_id") or "",
            "viewCount": str(info.get("view_count") or 0),
            "lengthSeconds": str(int(info.get("duration") or 0)),
            "thumbnail": {"thumbnails": thumbs},
        },
        "microformat": {
            "playerMicroformatRenderer": {
                "publishDate": info.get("upload_date"),
                "category": (info.get("categories") or [None])[0],
            }
        },
        "streamingData": {
            "formats": progressive,
            "adaptiveFormats": adaptive,
        },
        "playabilityStatus": {"status": "OK"},
        "_client": "ytdlp",
    }


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k and v)


async def fetch_via_ytdlp(
    video_id: str,
    page_url: str,
    *,
    cookies: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    cmd = [
        *_ytdlp_executable(),
        "-J",
        "--no-warnings",
        "--no-playlist",
        "--socket-timeout",
        "30",
        "--extractor-args",
        "youtube:player_client=android_vr,web,ios,tv_embedded",
        page_url,
    ]
    if cookies:
        header = _cookie_header(cookies)
        if header:
            cmd[1:1] = ["--add-header", f"Cookie:{header}"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("yt-dlp not found")
        return None

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=_YTDLP_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("yt-dlp timeout")
        return None

    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace")[:300]
        logger.warning("yt-dlp exit %s: %s", proc.returncode, err)
        return None

    try:
        info = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError as exc:
        logger.warning("yt-dlp json: %s", exc)
        return None

    player = ytdlp_info_to_player(info, video_id=video_id)
    if not (player.get("streamingData", {}).get("formats") or player.get(
        "streamingData", {}
    ).get("adaptiveFormats")):
        return None
    return player