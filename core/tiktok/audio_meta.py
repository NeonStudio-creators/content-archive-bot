"""
Извлечение аудио из данных TikTok.
"""

from __future__ import annotations

from typing import Any


def extract_audio_sources(item: dict[str, Any]) -> dict[str, Any]:
    music_info = item.get("music_info") or item.get("musicInfo") or {}
    music = item.get("music") or {}

    audio_url = (
        item.get("music")
        if isinstance(item.get("music"), str) and str(item.get("music")).startswith("http")
        else None
    )
    if not audio_url:
        audio_url = music_info.get("play") or music.get("playUrl")

    title = music_info.get("title") or music.get("title")
    artist = music_info.get("author") or music.get("authorName")
    duration_ms = None
    if music_info.get("duration"):
        duration_ms = int(music_info["duration"]) * 1000
    elif music.get("duration"):
        duration_ms = int(music["duration"]) * 1000

    result: dict[str, Any] = {
        "has_audio": bool(audio_url),
        "audio_url": audio_url,
        "audio_format": "mp3",
        "audio_source": "music_info" if music_info else "music",
    }
    if title or artist:
        result["music"] = {
            "title": title,
            "artist": artist,
            "duration_ms": duration_ms,
            "id": music_info.get("id") or music.get("id"),
            "original": music_info.get("original") or music.get("original"),
        }
    return {k: v for k, v in result.items() if v is not None}