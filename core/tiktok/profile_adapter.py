"""
Извлечение аватара и полей профиля TikTok.
"""

from __future__ import annotations

from typing import Any

from utils.dict_utils import dig, safe_dict


def extract_avatar_url(user: dict[str, Any] | None) -> str | None:
    if not user:
        return None
    for key in (
        "avatarLarger",
        "avatarMedium",
        "avatarThumb",
        "avatar",
    ):
        url = user.get(key)
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


def extract_avatar_from_scope(scope: dict[str, Any]) -> str | None:
    user = dig(scope, "webapp.user-detail", "userInfo", "user")
    if isinstance(user, dict):
        return extract_avatar_url(user)
    return None


def extract_user_from_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return safe_dict(dig(scope, "webapp.user-detail", "userInfo", "user"))


def extract_stats_from_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return safe_dict(dig(scope, "webapp.user-detail", "userInfo", "stats"))