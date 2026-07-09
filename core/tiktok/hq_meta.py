"""
Ссылки максимального качества для TikTok.
"""

from __future__ import annotations

import re
from typing import Any

from core.tiktok.cdn_urls import download_url_rank
from utils.dict_utils import safe_dict


def _entry(
    url: str | None,
    *,
    source: str,
    width: int | None = None,
    height: int | None = None,
    size_bytes: int | None = None,
    format_name: str = "mp4",
) -> dict[str, Any] | None:
    if not url or not str(url).startswith("http"):
        return None
    label = f"{width}×{height}" if width and height else source
    return {
        "url": url,
        "width": width,
        "height": height,
        "source": source,
        "format": format_name,
        "size_bytes": size_bytes,
        "label": label,
    }


def build_hq_downloads(item: dict[str, Any]) -> dict[str, Any]:
    """Собирает play / hdplay / wmplay в единый список HQ-вариантов."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    video = safe_dict(item.get("video"))
    width = video.get("width") or item.get("width")
    height = video.get("height") or item.get("height")

    candidates = [
        ("hdplay", item.get("hdplay"), item.get("hd_size"), "hd"),
        ("play", item.get("play"), item.get("size"), "play"),
        ("wmplay", item.get("wmplay"), item.get("wm_size"), "watermark"),
        ("downloadAddr", video.get("downloadAddr"), None, "download"),
        ("playAddr", video.get("playAddr"), None, "play_addr"),
    ]
    for source, url, size, tag in candidates:
        entry = _entry(
            url,
            source=tag,
            width=width,
            height=height,
            size_bytes=size,
        )
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            entries.append(entry)

    for variant in item.get("bitrateInfo") or video.get("bitrateInfo") or []:
        play_addr = safe_dict(variant).get("PlayAddr") or safe_dict(variant).get(
            "play_addr"
        )
        url = None
        if isinstance(play_addr, str):
            url = play_addr
        elif isinstance(play_addr, dict):
            url_list = play_addr.get("UrlList") or play_addr.get("url_list") or []
            url = url_list[0] if url_list else play_addr.get("Uri")
        entry = _entry(
            url,
            source="bitrate",
            width=variant.get("Width") or variant.get("width"),
            height=variant.get("Height") or variant.get("height"),
            size_bytes=variant.get("DataSize") or variant.get("data_size"),
        )
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            entries.append(entry)

    entries.sort(
        key=lambda e: (
            download_url_rank(e.get("url") or ""),
            (e.get("width") or 0) * (e.get("height") or 0),
            e.get("size_bytes") or 0,
        ),
        reverse=True,
    )

    best = entries[0] if entries else None
    result: dict[str, Any] = {"hq_downloads": entries}
    if best:
        result["hq_best"] = best
        result["hq_best_url"] = best["url"]
        result["video_url_best"] = best["url"]
        result["hq_best_source"] = best.get("source")
        result["hq_format"] = best.get("format", "mp4")
        w, h = best.get("width"), best.get("height")
        if w and h:
            result["resolution"] = f"{w}x{h}"
            result["width"] = w
            result["height"] = h
    return {k: v for k, v in result.items() if v not in (None, [], {})}


def hq_filename(base: str, entry: dict[str, Any], *, index: int = 1) -> str:
    fmt = entry.get("format") or "mp4"
    w, h = entry.get("width"), entry.get("height")
    safe = re.sub(r"[^\w\-.]+", "_", base).strip("_")[:32] or "media"
    if w and h:
        return f"{safe}_{index}_{w}x{h}.{fmt}"
    return f"{safe}_{index}_hq.{fmt}"