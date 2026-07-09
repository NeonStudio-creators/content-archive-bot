"""
Извлечение ссылок на видео/фото в максимальном качестве.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from core.source_quality import pick_source_best, source_type_rank
from core.video_meta import extract_video_versions, pick_best_version
from utils.dict_utils import safe_dict

_TG_MAX_BYTES = 48 * 1024 * 1024


def _normalize_url(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    url = html.unescape(value.strip())
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http"):
        return url
    return None


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


def _guess_video_format(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".mp4"):
        return "mp4"
    if path.endswith(".mov"):
        return "mov"
    if path.endswith(".webm"):
        return "webm"
    return "mp4"


def _pixels(item: dict[str, Any]) -> int:
    w = int(item.get("width") or 0)
    h = int(item.get("height") or 0)
    return w * h


def parse_video_dash_manifest(manifest: str) -> list[dict[str, Any]]:
    """Видео-дорожки из MPD с прямыми BaseURL."""
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
            if ctype == "audio" or "audio" in mime:
                continue
            if ctype != "video" and "video" not in mime and not adap.findall(
                ".//mpd:Representation", ns
            ):
                continue
            reps = (
                adap.findall("mpd:Representation", ns)
                or adap.findall("Representation")
            )
            for rep in reps:
                rep_mime = (rep.get("mimeType") or mime or "").lower()
                if "audio" in rep_mime and "video" not in rep_mime:
                    continue
                w = int(rep.get("width") or 0) or None
                h = int(rep.get("height") or 0) or None
                if not w and not h and "video" not in rep_mime:
                    continue
                base = rep.find("mpd:BaseURL", ns) or rep.find("BaseURL")
                url = _normalize_url(
                    base.text if base is not None and base.text else None
                )
                if not url:
                    continue
                tracks.append({
                    "url": url,
                    "width": w,
                    "height": h,
                    "fps": _parse_fps(
                        rep.get("frameRate") or rep.get("framerate")
                    ),
                    "codec": rep.get("codecs") or rep.get("mimeType"),
                    "bandwidth_bps": int(rep.get("bandwidth") or 0) or None,
                    "source": "source",
                    "id": rep.get("id"),
                })
    except ET.ParseError:
        for match in re.finditer(
            r'width="(\d+)"[^>]*height="(\d+)"[^>]*>.*?<BaseURL>([^<]+)</BaseURL>',
            manifest,
            re.DOTALL,
        ):
            url = _normalize_url(match.group(3))
            if url:
                tracks.append({
                    "url": url,
                    "width": int(match.group(1)),
                    "height": int(match.group(2)),
                    "source": "source",
                })

    tracks.sort(key=lambda t: _pixels(t), reverse=True)
    return tracks


def _entry_from_version(v: dict[str, Any], source: str) -> dict[str, Any] | None:
    url = _normalize_url(v.get("url"))
    if not url:
        return None
    w = v.get("width")
    h = v.get("height")
    return {
        "url": url,
        "width": w,
        "height": h,
        "bandwidth_bps": v.get("bandwidth") or v.get("bandwidth_bps"),
        "source": source,
        "format": _guess_video_format(url),
        "label": f"{w}×{h}" if w and h else source,
    }


def build_hq_downloads(node: dict[str, Any]) -> dict[str, Any]:
    """
    Все варианты загрузки + лучший файл (progressive + DASH).
    """
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for v in extract_video_versions(node):
        item = _entry_from_version(v, "source")
        if item and item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            entries.append(item)

    for url_key in ("video_url",):
        url = _normalize_url(node.get(url_key))
        if url and url not in seen_urls:
            seen_urls.add(url)
            entries.append({
                "url": url,
                "width": node.get("original_width"),
                "height": node.get("original_height"),
                "source": "direct",
                "format": _guess_video_format(url),
                "label": "direct",
            })

    manifest = node.get("video_dash_manifest") or node.get("dash_manifest")
    for dash in parse_video_dash_manifest(manifest or ""):
        url = dash.get("url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        w, h = dash.get("width"), dash.get("height")
        entries.append({
            "url": url,
            "width": w,
            "height": h,
            "fps": dash.get("fps"),
            "codec": dash.get("codec"),
            "bandwidth_bps": dash.get("bandwidth_bps"),
            "source": "source",
            "format": _guess_video_format(url),
            "label": f"{w}×{h} DASH" if w and h else "DASH",
        })

    entries.sort(
        key=lambda e: (
            source_type_rank(e.get("source")),
            _pixels(e),
            e.get("bandwidth_bps") or 0,
        ),
        reverse=True,
    )

    best_progressive = pick_best_version(extract_video_versions(node))
    best_entry = pick_source_best(entries) or (entries[0] if entries else None)

    if best_progressive and best_entry:
        prog_px = (best_progressive.get("width") or 0) * (
            best_progressive.get("height") or 0
        )
        if _pixels(best_entry) < prog_px:
            url = _normalize_url(best_progressive.get("url"))
            if url:
                best_entry = _entry_from_version(best_progressive, "progressive")

    dims = safe_dict(node.get("dimensions"))
    result: dict[str, Any] = {
        "hq_downloads": entries,
    }

    if best_entry:
        result["hq_best"] = best_entry
        result["hq_best_url"] = best_entry["url"]
        result["video_url_best"] = best_entry["url"]
        w, h = best_entry.get("width"), best_entry.get("height")
        if w and h:
            result["resolution"] = f"{w}x{h}"
            result["width"] = w
            result["height"] = h
        result["hq_best_source"] = best_entry.get("source")
        result["hq_format"] = best_entry.get("format", "mp4")

    elif node.get("display_url") or node.get("thumbnail_src"):
        img = _normalize_url(
            node.get("display_url") or node.get("thumbnail_src")
        )
        if img:
            result["hq_best"] = {
                "url": img,
                "source": "image",
                "format": "jpg",
                "label": "фото",
            }
            result["hq_best_url"] = img

    return {k: v for k, v in result.items() if v is not None and v != []}


def iter_video_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Все видео-узлы публикации (включая карусель)."""
    children = safe_dict(node.get("edge_sidecar_to_children")).get("edges", [])
    if children:
        nodes: list[dict[str, Any]] = []
        for edge in children:
            child = safe_dict(edge.get("node"))
            nodes.extend(iter_video_nodes(child))
        return nodes

    is_video = bool(
        node.get("is_video")
        or node.get("media_type") == 2
        or node.get("video_url")
        or node.get("video_versions")
    )
    if is_video:
        return [node]

    if node.get("display_url") and not node.get("video_versions"):
        return [node]
    return []


def hq_filename(
    base: str,
    entry: dict[str, Any],
    *,
    index: int = 1,
) -> str:
    fmt = entry.get("format") or "mp4"
    w, h = entry.get("width"), entry.get("height")
    safe = re.sub(r"[^\w\-.]+", "_", base).strip("_")[:32] or "media"
    if w and h:
        return f"{safe}_{index}_{w}x{h}.{fmt}"
    return f"{safe}_{index}_hq.{fmt}"


def within_telegram_limit(size_bytes: int) -> bool:
    return 0 < size_bytes <= _TG_MAX_BYTES