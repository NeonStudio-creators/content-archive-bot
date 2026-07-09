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


def _unique_http_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url and url.startswith("http") and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def sort_download_urls(urls: list[str]) -> list[str]:
    """Только приоритет CDN (legacy). Для HQ используйте order_download_urls."""
    return sorted(_unique_http_urls(urls), key=download_url_rank, reverse=True)


def order_download_urls(
    urls: list[str],
    *,
    entries: list[dict] | None = None,
) -> list[str]:
    """
    Сначала исходное качество (source/hdplay), затем CDN-доступность.
    """
    from core.source_quality import entry_quality_score

    unique = _unique_http_urls(urls)
    if not unique:
        return []

    by_url: dict[str, dict] = {}
    if entries:
        for entry in entries:
            url = entry.get("url")
            if isinstance(url, str) and url.startswith("http"):
                by_url[url] = entry

    return sorted(
        unique,
        key=lambda url: (
            entry_quality_score(by_url[url]) if url in by_url else (0, 0, 0),
            download_url_rank(url),
        ),
        reverse=True,
    )