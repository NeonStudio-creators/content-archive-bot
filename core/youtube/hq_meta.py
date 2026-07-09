"""
Ссылки максимального качества YouTube (streamingData).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, unquote

from core.source_quality import pick_muxed_best, pick_source_best, source_type_rank


def _format_url(fmt: dict[str, Any]) -> str | None:
    if fmt.get("url"):
        return fmt["url"]
    cipher = fmt.get("signatureCipher") or fmt.get("cipher")
    if not cipher or not isinstance(cipher, str):
        return None
    params = dict(parse_qsl(cipher))
    url = params.get("url")
    if not url:
        return None
    url = unquote(url)
    sig = params.get("sig")
    if sig:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sig={sig}"
    return url


def _entry(
    fmt: dict[str, Any],
    *,
    source: str,
    is_muxed: bool = False,
) -> dict[str, Any] | None:
    url = _format_url(fmt)
    if not url or not url.startswith("http"):
        return None
    width = fmt.get("width")
    height = fmt.get("height")
    label = f"{width}×{height}" if width and height else source
    mime = (fmt.get("mimeType") or "video/mp4").lower()
    fmt_ext = mime.split("/")[-1].split(";")[0]
    return {
        "url": url,
        "width": width,
        "height": height,
        "source": source,
        "format": fmt_ext,
        "size_bytes": fmt.get("contentLength"),
        "bitrate": fmt.get("bitrate"),
        "fps": fmt.get("fps"),
        "label": label,
        "itag": fmt.get("itag"),
        "is_muxed": is_muxed,
        "has_audio": is_muxed,
    }


def build_hq_downloads(player: dict[str, Any]) -> dict[str, Any]:
    streaming = player.get("streamingData") or {}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for fmt in streaming.get("adaptiveFormats") or []:
        mime = (fmt.get("mimeType") or "").lower()
        if "audio" in mime and "video" not in mime:
            entry = _entry(fmt, source="audio", is_muxed=False)
            if entry and entry["url"] not in seen:
                seen.add(entry["url"])
                entries.append(entry)
            continue
        if "video" not in mime:
            continue
        entry = _entry(fmt, source="adaptive", is_muxed=False)
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            entries.append(entry)

    for fmt in streaming.get("formats") or []:
        entry = _entry(fmt, source="progressive", is_muxed=True)
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            entries.append(entry)

    video_entries = [e for e in entries if e.get("source") != "audio"]
    video_entries.sort(
        key=lambda e: (
            1 if e.get("is_muxed") else 0,
            source_type_rank(e.get("source")),
            (e.get("width") or 0) * (e.get("height") or 0),
            int(e.get("bitrate") or 0),
            1 if (e.get("format") or "").lower() == "mp4" else 0,
        ),
        reverse=True,
    )
    entries = video_entries + [e for e in entries if e.get("source") == "audio"]

    playback = pick_muxed_best(video_entries)
    adaptive_best = pick_source_best(
        [e for e in video_entries if not e.get("is_muxed")]
    )
    best = playback or adaptive_best or (video_entries[0] if video_entries else None)

    result: dict[str, Any] = {"hq_downloads": entries}
    if playback:
        result["playback_best"] = playback
        result["playback_best_url"] = playback["url"]
    if adaptive_best:
        result["adaptive_best"] = adaptive_best
        result["adaptive_best_url"] = adaptive_best["url"]
    if best:
        result["hq_best"] = best
        result["hq_best_url"] = best["url"]
        result["video_url_best"] = best["url"]
        result["hq_best_source"] = best.get("source")
        w, h = best.get("width"), best.get("height")
        if w and h:
            result["resolution"] = f"{w}x{h}"
            result["width"] = w
            result["height"] = h
        result["has_audio"] = bool(best.get("is_muxed"))
    return {k: v for k, v in result.items() if v not in (None, [], {})}


def hq_filename(base: str, entry: dict[str, Any], *, index: int = 1) -> str:
    fmt = entry.get("format") or "mp4"
    if fmt not in ("mp4", "webm", "m4a"):
        fmt = "mp4"
    w, h = entry.get("width"), entry.get("height")
    safe = re.sub(r"[^\w\-.]+", "_", base).strip("_")[:32] or "youtube"
    if w and h:
        return f"{safe}_{index}_{w}x{h}.{fmt}"
    return f"{safe}_{index}_hq.{fmt}"