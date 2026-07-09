"""
Аудиодорожка YouTube из adaptiveFormats.
"""

from __future__ import annotations

from typing import Any

from core.youtube.hq_meta import _format_url


def extract_audio_sources(player: dict[str, Any]) -> dict[str, Any]:
    streaming = player.get("streamingData") or {}
    candidates: list[dict[str, Any]] = []
    for fmt in streaming.get("adaptiveFormats") or []:
        mime = (fmt.get("mimeType") or "").lower()
        if "audio" not in mime:
            continue
        url = _format_url(fmt)
        if url:
            candidates.append({**fmt, "_url": url})

    if not candidates:
        return {"has_audio": False}

    candidates.sort(key=lambda f: int(f.get("bitrate") or 0), reverse=True)
    best = candidates[0]
    url = best["_url"]
    fmt = (best.get("mimeType") or "audio/mp4").split("/")[-1].split(";")[0]
    details = player.get("videoDetails") or {}
    result: dict[str, Any] = {
        "has_audio": True,
        "audio_url": url,
        "audio_format": "m4a" if "mp4" in fmt else fmt,
        "audio_source": "adaptiveFormats",
        "music": {
            "title": details.get("title"),
            "artist": details.get("author"),
        },
    }
    if details.get("lengthSeconds"):
        result["music"]["duration_ms"] = int(details["lengthSeconds"]) * 1000
    return result