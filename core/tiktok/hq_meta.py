"""
Ссылки максимального качества для TikTok.
"""

from __future__ import annotations

import re
from typing import Any

from core.source_quality import pick_source_best, source_type_rank
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


def _bitrate_play_url(variant: dict[str, Any]) -> str | None:
    play_addr = safe_dict(variant).get("PlayAddr") or safe_dict(variant).get(
        "play_addr"
    )
    if isinstance(play_addr, str):
        return play_addr
    if isinstance(play_addr, dict):
        url_list = play_addr.get("UrlList") or play_addr.get("url_list") or []
        if url_list:
            return url_list[0]
        uri = play_addr.get("Uri") or play_addr.get("uri")
        if isinstance(uri, str) and uri.startswith("http"):
            return uri
    return None


def build_hq_downloads(item: dict[str, Any]) -> dict[str, Any]:
    """Собирает play / hdplay / wmplay в единый список HQ-вариантов."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    video = safe_dict(item.get("video"))
    width = video.get("width") or item.get("width")
    height = video.get("height") or item.get("height")

    bitrate_variants = sorted(
        item.get("bitrateInfo") or video.get("bitrateInfo") or [],
        key=lambda v: int(
            safe_dict(v).get("Bitrate")
            or safe_dict(v).get("bitrate")
            or safe_dict(v).get("DataSize")
            or safe_dict(v).get("data_size")
            or 0
        ),
        reverse=True,
    )
    for idx, variant in enumerate(bitrate_variants):
        v = safe_dict(variant)
        url = _bitrate_play_url(v)
        tag = "source" if idx == 0 else "bitrate"
        entry = _entry(
            url,
            source=tag,
            width=v.get("Width") or v.get("width") or width,
            height=v.get("Height") or v.get("height") or height,
            size_bytes=v.get("DataSize") or v.get("data_size"),
        )
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            entries.append(entry)

    candidates = [
        ("hdplay", item.get("hdplay"), item.get("hd_size"), "source"),
        ("downloadAddr", video.get("downloadAddr"), None, "download"),
        ("playAddr", video.get("playAddr"), None, "play_addr"),
        ("play", item.get("play"), item.get("size"), "compressed"),
        ("wmplay", item.get("wmplay"), item.get("wm_size"), "watermark"),
    ]
    for _name, url, size, tag in candidates:
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

    entries.sort(
        key=lambda e: (
            source_type_rank(e.get("source")),
            download_url_rank(e.get("url") or ""),
            (e.get("width") or 0) * (e.get("height") or 0),
            e.get("size_bytes") or 0,
        ),
        reverse=True,
    )

    best = pick_source_best(entries) or (entries[0] if entries else None)
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