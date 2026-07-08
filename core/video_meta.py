"""
Извлечение технических метаданных видео: разрешение, FPS, кодек, битрейт.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


def _parse_fps(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            d = float(den)
            return round(float(num) / d, 2) if d else None
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_dash_manifest(manifest: str | None) -> dict[str, Any]:
    """Парсит video_dash_manifest (MPD) — FPS, кодеки, варианты качества."""
    if not manifest or not manifest.strip():
        return {}

    result: dict[str, Any] = {
        "representations": [],
        "fps": None,
        "max_width": None,
        "max_height": None,
        "max_bandwidth": None,
        "codecs": [],
    }

    try:
        root = ET.fromstring(manifest)
    except ET.ParseError:
        # Fallback regex
        for match in re.finditer(
            r'frameRate="([^"]+)"[^>]*width="(\d+)"[^>]*height="(\d+)"'
            r'(?:[^>]*bandwidth="(\d+)")?(?:[^>]*codecs="([^"]+)")?',
            manifest,
        ):
            fps = _parse_fps(match.group(1))
            w, h = int(match.group(2)), int(match.group(3))
            bw = int(match.group(4)) if match.group(4) else None
            codec = match.group(5)
            result["representations"].append({
                "width": w, "height": h, "fps": fps,
                "bandwidth_bps": bw, "codec": codec,
            })
        if result["representations"]:
            best = max(result["representations"], key=lambda r: r["width"] * r["height"])
            result["fps"] = result["fps"] or best.get("fps")
            result["max_width"] = best["width"]
            result["max_height"] = best["height"]
        return result

    ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}
    reps = root.findall(".//mpd:Representation", ns) or root.findall(".//Representation")

    for rep in reps:
        fps = _parse_fps(rep.get("frameRate") or rep.get("framerate"))
        w = int(rep.get("width") or 0)
        h = int(rep.get("height") or 0)
        bw = int(rep.get("bandwidth") or 0) or None
        codec = rep.get("codecs") or rep.get("mimeType")
        entry = {
            "width": w or None,
            "height": h or None,
            "fps": fps,
            "bandwidth_bps": bw,
            "codec": codec,
            "id": rep.get("id"),
        }
        result["representations"].append(entry)
        if fps and not result["fps"]:
            result["fps"] = fps
        if codec and codec not in result["codecs"]:
            result["codecs"].append(codec)
        if w and h:
            if not result["max_width"] or w * h > (result["max_width"] or 0) * (result["max_height"] or 0):
                result["max_width"] = w
                result["max_height"] = h
        if bw and (not result["max_bandwidth"] or bw > result["max_bandwidth"]):
            result["max_bandwidth"] = bw

    return result


def extract_video_versions(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Все варианты video_versions / display_resources для видео."""
    versions: list[dict[str, Any]] = []

    for vv in node.get("video_versions") or []:
        versions.append({
            "url": vv.get("url"),
            "width": vv.get("width"),
            "height": vv.get("height"),
            "type": vv.get("type"),
            "id": vv.get("id"),
            "bandwidth": vv.get("bandwidth"),
        })

    if not versions:
        for res in node.get("display_resources") or []:
            if res.get("src") or res.get("url"):
                versions.append({
                    "url": res.get("src") or res.get("url"),
                    "width": res.get("config_width") or res.get("width"),
                    "height": res.get("config_height") or res.get("height"),
                })

    return versions


def pick_best_version(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not versions:
        return None
    return max(
        versions,
        key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
    )


def build_video_technical(node: dict[str, Any]) -> dict[str, Any]:
    """
    Полный технический профиль видео из узла API.
    """
    versions = extract_video_versions(node)
    best = pick_best_version(versions)

    dims = node.get("dimensions") or {}
    width = (
        (best or {}).get("width")
        or node.get("original_width")
        or dims.get("width")
    )
    height = (
        (best or {}).get("height")
        or node.get("original_height")
        or dims.get("height")
    )

    dash = parse_dash_manifest(
        node.get("video_dash_manifest") or node.get("dash_manifest")
    )

    if dash.get("max_width") and dash.get("max_height"):
        dw = dash["max_width"] * dash["max_height"]
        cw = (width or 0) * (height or 0)
        if dw >= cw:
            width = dash["max_width"]
            height = dash["max_height"]

    fps = dash.get("fps")
    if not fps:
        fps = node.get("video_fps") or node.get("frame_rate")

    clips = node.get("clips_metadata") or {}
    audio = node.get("has_audio")
    if audio is None:
        audio = clips.get("audio_type") != "muted" if clips else None

    tech: dict[str, Any] = {
        "resolution": f"{width}x{height}" if width and height else None,
        "width": width,
        "height": height,
        "fps": fps,
        "duration_sec": node.get("video_duration") or node.get("duration"),
        "video_codec": node.get("video_codec"),
        "audio_codec": node.get("audio_codec"),
        "has_audio": audio,
        "is_dash_eligible": node.get("is_dash_eligible"),
        "number_of_qualities": node.get("number_of_qualities") or len(versions),
        "view_count": node.get("video_view_count") or node.get("view_count") or node.get("play_count"),
        "product_type": node.get("product_type"),
        "video_url_best": (best or {}).get("url") or node.get("video_url"),
        "bandwidth_bps": dash.get("max_bandwidth") or (best or {}).get("bandwidth"),
        "codecs": dash.get("codecs") or (
            [node.get("video_codec")] if node.get("video_codec") else []
        ),
        "quality_variants": versions,
        "dash_representations": dash.get("representations", []),
        "aspect_ratio": round(width / height, 3) if width and height else None,
        "accessibility_caption": node.get("accessibility_caption"),
        "video_subtitles_uri": node.get("video_subtitles_uri"),
    }

    # Музыка / Reels
    music = (
        clips.get("music_info", {}).get("music_asset_info")
        or clips.get("original_sound_info")
        or node.get("clips_music_attribution_info")
    )
    if music:
        tech["music"] = {
            "title": music.get("title") or music.get("song_name"),
            "artist": music.get("display_artist") or music.get("artist_name"),
            "duration_ms": music.get("duration_in_ms"),
        }

    return {k: v for k, v in tech.items() if v is not None and v != [] and v != {}}