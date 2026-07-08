"""
Извлечение оригинального аудиофайла из метаданных публикации.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from utils.dict_utils import safe_dict

AUDIO_URL_KEYS = (
    "progressive_download_url",
    "fast_start_progressive_download_url",
    "reactive_audio_download_url",
)


def _normalize_url(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    url = html.unescape(value.strip())
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http"):
        return url
    return None


def url_from_audio_block(block: dict[str, Any]) -> str | None:
    """Прямая ссылка на аудио из блока music_asset_info / original_sound_info."""
    for key in AUDIO_URL_KEYS:
        url = _normalize_url(block.get(key))
        if url:
            return url
    dash = block.get("dash_manifest")
    if dash:
        tracks = parse_audio_dash_manifest(dash)
        if tracks:
            return tracks[0].get("url")
    return _normalize_url(block.get("uri"))


def parse_audio_dash_manifest(manifest: str) -> list[dict[str, Any]]:
    """Парсит MPD и возвращает аудио-дорожки (лучшие первыми)."""
    if not manifest or not manifest.strip():
        return []

    manifest = html.unescape(manifest)
    tracks: list[dict[str, Any]] = []

    try:
        root = ET.fromstring(manifest)
        ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}
        sets = (
            root.findall(".//mpd:AdaptationSet", ns)
            or root.findall(".//AdaptationSet")
        )
        for adap in sets:
            ctype = (adap.get("contentType") or "").lower()
            mime = (adap.get("mimeType") or "").lower()
            if ctype != "audio" and "audio" not in mime:
                continue
            reps = (
                adap.findall("mpd:Representation", ns)
                or adap.findall("Representation")
            )
            for rep in reps:
                rep_mime = (rep.get("mimeType") or mime or "").lower()
                base = rep.find("mpd:BaseURL", ns) or rep.find("BaseURL")
                url = _normalize_url(
                    base.text if base is not None and base.text else None
                )
                if url:
                    tracks.append({
                        "url": url,
                        "codec": rep.get("codecs"),
                        "bandwidth_bps": int(rep.get("bandwidth") or 0) or None,
                        "mime_type": rep_mime,
                    })
    except ET.ParseError:
        for match in re.finditer(
            r'contentType="audio"[^>]*>.*?<BaseURL>([^<]+)</BaseURL>',
            manifest,
            re.DOTALL,
        ):
            url = _normalize_url(match.group(1))
            if url:
                tracks.append({"url": url})

    tracks.sort(key=lambda t: t.get("bandwidth_bps") or 0, reverse=True)
    return tracks


def _guess_format(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".mp3"):
        return "mp3"
    if path.endswith(".mp4"):
        return "mp4"
    return "m4a"


def extract_audio_sources(node: dict[str, Any]) -> dict[str, Any]:
    """
    Собирает все источники аудио и выбирает лучший оригинальный файл.
    Приоритет: original_sound → music → DASH-аудио из видео.
    """
    clips = safe_dict(node.get("clips_metadata"))
    candidates: list[dict[str, Any]] = []

    blocks: list[tuple[str, dict, int]] = [
        ("original_sound", safe_dict(clips.get("original_sound_info")), 1),
        (
            "music",
            safe_dict(safe_dict(clips.get("music_info")).get("music_asset_info")),
            2,
        ),
        ("music_attribution", safe_dict(node.get("clips_music_attribution_info")), 3),
    ]
    top_music = safe_dict(node.get("music_info"))
    if top_music:
        blocks.append(("music_info", top_music, 2))

    for source_type, block, priority in blocks:
        if not block:
            continue
        url = url_from_audio_block(block)
        candidates.append({
            "type": source_type,
            "priority": priority,
            "url": url,
            "title": (
                block.get("title")
                or block.get("song_name")
                or block.get("original_audio_title")
            ),
            "artist": block.get("display_artist") or block.get("artist_name"),
            "duration_ms": block.get("duration_in_ms"),
            "audio_asset_id": block.get("audio_asset_id") or block.get("id"),
            "audio_cluster_id": block.get("audio_cluster_id"),
            "music_canonical_id": (
                clips.get("music_canonical_id") or block.get("music_canonical_id")
            ),
        })

    video_dash = node.get("video_dash_manifest") or node.get("dash_manifest")
    if video_dash:
        for idx, track in enumerate(parse_audio_dash_manifest(video_dash)):
            candidates.append({
                "type": "video_dash_audio",
                "priority": 10 + idx,
                "url": track.get("url"),
                "codec": track.get("codec"),
                "bandwidth_bps": track.get("bandwidth_bps"),
            })

    candidates = [c for c in candidates if c.get("url")]
    candidates.sort(key=lambda c: c.get("priority", 99))

    best = candidates[0] if candidates else None
    result: dict[str, Any] = {
        "audio_sources": [
            {k: v for k, v in c.items() if v is not None}
            for c in candidates
        ],
        "music_canonical_id": clips.get("music_canonical_id"),
    }

    if best:
        result["audio_url"] = best["url"]
        result["audio_source"] = best["type"]
        result["audio_format"] = _guess_format(best["url"])
        if best.get("audio_asset_id"):
            result["audio_asset_id"] = best["audio_asset_id"]
        if best.get("audio_cluster_id"):
            result["audio_cluster_id"] = best["audio_cluster_id"]
        if best.get("music_canonical_id"):
            result["music_canonical_id"] = best["music_canonical_id"]
        music: dict[str, Any] = {}
        if best.get("title"):
            music["title"] = best["title"]
        if best.get("artist"):
            music["artist"] = best["artist"]
        if best.get("duration_ms"):
            music["duration_ms"] = best["duration_ms"]
        if music:
            result["music"] = music

    return {k: v for k, v in result.items() if v is not None and v != []}


def build_audio_technical(node: dict[str, Any]) -> dict[str, Any]:
    """Аудио-метаданные для включения в technical профиль видео."""
    return extract_audio_sources(node)