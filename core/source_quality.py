"""
Приоритет «исходного» видео (до сжатия CDN/превью соцсетей).
"""

from __future__ import annotations

from typing import Any

# Чем выше rank — тем ближе к оригиналу загрузки автором.
SOURCE_TYPE_RANK: dict[str, int] = {
    "source": 100,
    "original": 100,
    "hd": 95,
    "bitrate": 90,
    "download": 88,
    "dash": 85,
    "adaptive": 80,
    "direct": 75,
    "progressive": 70,
    "play_addr": 65,
    "innertube": 60,
}

COMPRESSED_TYPE_RANK: dict[str, int] = {
    "play": 20,
    "watermark": 15,
    "wmplay": 15,
    "compressed": 10,
    "preview": 5,
}


def source_type_rank(source: str | None) -> int:
    if not source:
        return 50
    key = source.lower()
    if key in SOURCE_TYPE_RANK:
        return SOURCE_TYPE_RANK[key]
    if key in COMPRESSED_TYPE_RANK:
        return COMPRESSED_TYPE_RANK[key]
    return 50


def is_compressed_source(source: str | None) -> bool:
    return source_type_rank(source) <= 30


def entry_quality_score(entry: dict[str, Any]) -> tuple[int, int, int]:
    """(тип источника, пиксели, байты/битрейт) — для сортировки."""
    src = entry.get("source")
    w = int(entry.get("width") or 0)
    h = int(entry.get("height") or 0)
    pixels = w * h
    size = int(entry.get("size_bytes") or entry.get("bitrate") or 0)
    return (source_type_rank(src), pixels, size)


def sort_source_first(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=entry_quality_score, reverse=True)


def pick_source_best(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    non_compressed = [e for e in entries if not is_compressed_source(e.get("source"))]
    pool = non_compressed or entries
    return sort_source_first(pool)[0]


def filter_download_candidates(
    entries: list[dict[str, Any]],
    *,
    source_only: bool = True,
) -> list[dict[str, Any]]:
    if not entries:
        return []
    if source_only:
        filtered = [e for e in entries if not is_compressed_source(e.get("source"))]
        if filtered:
            return sort_source_first(filtered)
    return sort_source_first(entries)