"""
Приоритет CDN-URL TikTok для скачивания.

playAddr с HTML/API (webapp-prime) требуют браузерную сессию и дают 403
с сервера Railway — для загрузки нужны mirror CDN (tiktokcdn-*).
"""

from __future__ import annotations

RESTRICTED_MARKERS = (
    "webapp-prime",
    ".tiktokv.com",
    "/video/tos/",
)

MIRROR_CDN_MARKERS = (
    "tiktokcdn",
)


def is_mirror_cdn_url(url: str) -> bool:
    lower = (url or "").lower()
    return any(marker in lower for marker in MIRROR_CDN_MARKERS)


def is_restricted_download_url(url: str) -> bool:
    if is_mirror_cdn_url(url):
        return False
    lower = (url or "").lower()
    return any(marker in lower for marker in RESTRICTED_MARKERS)


def download_url_rank(url: str) -> int:
    """Чем выше — тем раньше пробуем скачать."""
    if not url or not url.startswith("http"):
        return -1
    if is_mirror_cdn_url(url):
        return 100
    if is_restricted_download_url(url):
        return 0
    return 50


def sort_download_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url and url.startswith("http") and url not in seen:
            seen.add(url)
            unique.append(url)
    return sorted(unique, key=download_url_rank, reverse=True)