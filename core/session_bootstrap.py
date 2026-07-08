"""
Прогрев Instagram-сессии: csrftoken, mid, ig_did, lsd.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_CSRF_RE = re.compile(r'"csrf_token":"([^"]+)"')
_LSD_RE = re.compile(
    r'(?:\["LSD",\[\],\{"token":"([^"]+)"\}|"lsd":"([^"]+)")'
)


def parse_set_cookies(headers: Any) -> dict[str, str]:
    """Извлекает cookies из заголовков Set-Cookie."""
    cookies: dict[str, str] = {}
    raw_headers = headers.getall("Set-Cookie", []) if hasattr(headers, "getall") else []
    if not raw_headers and headers.get("Set-Cookie"):
        raw_headers = [headers.get("Set-Cookie")]

    for header in raw_headers:
        if not header:
            continue
        part = header.split(";", 1)[0].strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def parse_tokens_from_html(html: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    csrf = _CSRF_RE.search(html)
    if csrf:
        tokens["csrftoken"] = csrf.group(1)
    lsd = _LSD_RE.search(html)
    if lsd:
        tokens["lsd"] = lsd.group(1) or lsd.group(2)
    return tokens


def merge_cookies(*maps: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for m in maps:
        merged.update({k: v for k, v in m.items() if v})
    return merged


def profile_signals_in_html(html: str, username: str) -> bool:
    """Есть ли признаки реального профиля в HTML (og:title, canonical)."""
    low = html.lower()
    user = username.lower()
    markers = (
        f"@{user}",
        f"/{user}/",
        f"&#064;{user}",
        f"content=\"{user}",
    )
    return any(m in low for m in markers) and (
        "og:title" in low or "profilepage" in low or "polarisprofile" in low
    )


def is_profile_not_found_html(html: str, username: str) -> bool:
    """
    Строгая проверка 404 — только если нет признаков профиля.
    Instagram вшивает httpErrorPage в JS даже на живых страницах.
    """
    if profile_signals_in_html(html, username):
        return False

    low = html.lower()
    user = username.lower()
    if "show_lox_redesigned_404_page" in low:
        return True
    if f"/{user}/" in low and "page_type\":\"httpErrorPage\"" in low:
        return True
    return False