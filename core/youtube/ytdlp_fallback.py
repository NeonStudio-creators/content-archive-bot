"""
Fallback через yt-dlp (обход bot-check / poToken на Railway).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_YTDLP_TIMEOUT_SEC = 120

_CLIENT_PRESETS: tuple[str, ...] = (
    "youtube:player_client=android_vr,web_safari,tv,ios",
    "youtube:player_client=mweb,web_embedded,android",
    "youtube:player_client=tv_embedded,web",
)


def _ytdlp_base() -> list[str]:
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


def _write_netscape_cookies(cookies: dict[str, str]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".txt", prefix="yt_cookies_")
    os.close(fd)
    path = Path(name)
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "",
    ]
    for name, value in cookies.items():
        if not name or not value:
            continue
        lines.append(
            f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _build_cmd(
    page_url: str,
    *,
    extractor_args: str,
    cookies_path: Path | None,
) -> list[str]:
    extras: list[str] = []
    if shutil.which("node"):
        extras.extend(["--js-runtimes", "node"])
    if cookies_path:
        extras.extend(["--cookies", str(cookies_path)])
    return [
        *_ytdlp_base(),
        *extras,
        "-J",
        "--no-warnings",
        "--no-playlist",
        "--socket-timeout",
        "45",
        "--retries",
        "3",
        "--remote-components",
        "ejs:github",
        "--extractor-args",
        extractor_args,
        page_url,
    ]


async def _run_ytdlp(cmd: list[str]) -> tuple[dict[str, Any] | None, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, "yt-dlp module not found (pip install yt-dlp)"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=_YTDLP_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return None, "yt-dlp timeout"

    err = (stderr or b"").decode(errors="replace").strip()
    if proc.returncode != 0:
        tail = err.splitlines()[-1] if err else f"exit {proc.returncode}"
        return None, tail[:400]

    try:
        return json.loads(stdout.decode(errors="replace")), ""
    except json.JSONDecodeError as exc:
        return None, f"json error: {exc}"


async def fetch_via_ytdlp(
    video_id: str,
    page_url: str,
    *,
    cookies: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    last_err = ""
    cookie_attempts: list[dict[str, str] | None] = [None]
    if cookies:
        cookie_attempts.append(cookies)

    for cookie_set in cookie_attempts:
        cookies_path: Path | None = None
        try:
            if cookie_set:
                cookies_path = _write_netscape_cookies(cookie_set)
            for preset in _CLIENT_PRESETS:
                cmd = _build_cmd(
                    page_url,
                    extractor_args=preset,
                    cookies_path=cookies_path,
                )
                info, err = await _run_ytdlp(cmd)
                if err:
                    last_err = err
                    logger.warning("yt-dlp (%s): %s", preset, err)
                    continue
                if not info:
                    continue
                player = ytdlp_info_to_player(info, video_id=video_id)
                streams = player.get("streamingData") or {}
                if streams.get("formats") or streams.get("adaptiveFormats"):
                    logger.info("yt-dlp OK preset=%s cookies=%s", preset, bool(cookie_set))
                    return player
                last_err = "no stream URLs in yt-dlp response"
        finally:
            if cookies_path and cookies_path.exists():
                cookies_path.unlink(missing_ok=True)

    if last_err:
        logger.warning("yt-dlp failed: %s", last_err)
    return None


async def probe_ytdlp() -> tuple[bool, str]:
    """Проверка доступности yt-dlp на сервере."""
    cmd = [*_ytdlp_base(), "--version"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except FileNotFoundError:
        return False, "модуль yt_dlp не установлен"
    except asyncio.TimeoutError:
        return False, "timeout"
    if proc.returncode != 0:
        return False, (stderr or b"").decode(errors="replace")[:200]
    ver = (stdout or b"").decode().strip()
    node = "node OK" if shutil.which("node") else "node MISSING"
    return True, f"{ver} ({node})"