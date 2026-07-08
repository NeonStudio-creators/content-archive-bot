"""
Нормализация ответов profile API в единый формат data.user.
"""

from __future__ import annotations

import html as html_module
from typing import Any

from utils.dict_utils import dig, safe_dict


def _is_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = html_module.unescape(value.strip())
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http"):
        return url
    return None


def _best_square_version_url(versions: list) -> str | None:
    """Выбирает максимальную квадратную 1:1 версию аватарки."""
    square: list[tuple[int, dict]] = []
    other: list[tuple[int, dict]] = []

    for raw in versions:
        v = safe_dict(raw)
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
        if not _is_http_url(v.get("url")):
            continue
        area = w * h
        if w and h and w == h:
            square.append((area, v))
        elif area:
            other.append((area, v))

    pool = square or other
    if not pool:
        return None
    best = max(pool, key=lambda item: item[0])[1]
    return _is_http_url(best.get("url"))


def extract_avatar_url(user: dict[str, Any]) -> str | None:
    """Достаёт URL аватарки из любого формата ответа API."""
    if not user:
        return None

    for key in (
        "profile_pic_url_hd",
        "profile_pic_url",
        "hd_profile_pic_url",
        "profilePicUrl",
        "profile_pic_url_hd_2025",
    ):
        url = _is_http_url(user.get(key))
        if url:
            return url

    for key in ("hd_profile_pic_url_info", "profile_pic_url_info"):
        info = safe_dict(user.get(key))
        url = _is_http_url(info.get("url"))
        if url:
            return url

    for key in ("hd_profile_pic_versions", "profile_pic_versions"):
        versions = user.get(key)
        if not isinstance(versions, list) or not versions:
            continue
        url = _best_square_version_url(versions)
        if url:
            return url

    return _find_avatar_url_deep(user)


def _find_avatar_url_deep(
    obj: Any,
    *,
    depth: int = 0,
    username: str | None = None,
) -> str | None:
    """Рекурсивный поиск profile_pic / og:image URL в сыром JSON."""
    if depth > 12:
        return None

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if key_l in {
                "profile_pic_url_hd",
                "profile_pic_url",
                "hd_profile_pic_url",
                "profilepicurl",
            }:
                url = _is_http_url(value)
                if url:
                    return url
            if key_l in {"hd_profile_pic_url_info", "profile_pic_url_info"}:
                url = _is_http_url(safe_dict(value).get("url"))
                if url:
                    return url
            if "profile_pic" in key_l or key_l.endswith("_pic_url"):
                url = _is_http_url(value)
                if url:
                    return url

        for value in obj.values():
            found = _find_avatar_url_deep(
                value, depth=depth + 1, username=username
            )
            if found:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_avatar_url_deep(
                item, depth=depth + 1, username=username
            )
            if found:
                return found

    return None


def extract_avatar_from_profile_payload(
    profile_data: dict[str, Any],
) -> str | None:
    """Ищет аватар в полном ответе profile API / GraphQL."""
    user = safe_dict(dig(profile_data, "data", "user"))
    url = extract_avatar_url(user)
    if url:
        return url
    return _find_avatar_url_deep(profile_data)


def normalize_user_node(user: dict[str, Any]) -> dict[str, Any]:
    """Приводит REST/mobile/GraphQL user к ожидаемому parser-ом виду."""
    u = dict(user)

    if not u.get("id"):
        u["id"] = str(u.get("pk", ""))
    else:
        u["id"] = str(u["id"])

    if "edge_followed_by" not in u and u.get("follower_count") is not None:
        u["edge_followed_by"] = {"count": u["follower_count"]}
    if "edge_follow" not in u and u.get("following_count") is not None:
        u["edge_follow"] = {"count": u["following_count"]}
    if "edge_owner_to_timeline_media" not in u and u.get("media_count") is not None:
        u["edge_owner_to_timeline_media"] = {"count": u["media_count"]}

    avatar = extract_avatar_url(u)
    if avatar:
        u["profile_pic_url_hd"] = avatar
        if not u.get("profile_pic_url"):
            u["profile_pic_url"] = avatar

    return u


def wrap_profile(user: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"user": normalize_user_node(user)}}


def from_web_profile_info(payload: dict[str, Any]) -> dict[str, Any] | None:
    user = safe_dict(payload.get("data", {})).get("user")
    if not user or not (user.get("id") or user.get("pk")):
        return None
    return wrap_profile(user)


def from_usernameinfo(payload: dict[str, Any]) -> dict[str, Any] | None:
    user = safe_dict(payload.get("user"))
    if not user or not (user.get("id") or user.get("pk")):
        return None
    return wrap_profile(user)


def from_gql_profile(payload: dict[str, Any]) -> dict[str, Any] | None:
    user = dig(payload, "data", "user")
    if not user or not isinstance(user, dict):
        return None
    if not (user.get("id") or user.get("pk")):
        return None
    return wrap_profile(user)


def find_user_id_in_search(
    payload: dict[str, Any], username: str
) -> str | None:
    users = (
        dig(
            payload,
            "data",
            "xdt_api__v1__fbsearch__non_profiled_serp",
            "users",
        )
        or []
    )
    target = username.lower()
    for user in users:
        if (user.get("username") or "").lower() == target:
            return str(user.get("pk") or user.get("id") or "")
    return None


def _find_user_recursive(
    obj: Any,
    username: str,
    *,
    depth: int = 0,
) -> dict[str, Any] | None:
    if depth > 14:
        return None
    target = username.lower()

    if isinstance(obj, dict):
        uname = (obj.get("username") or "").lower()
        uid = obj.get("id") or obj.get("pk")
        if uname == target and uid:
            return obj
        for value in obj.values():
            found = _find_user_recursive(value, username, depth=depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_user_recursive(item, username, depth=depth + 1)
            if found:
                return found
    return None


def from_embedded_profile_json(
    data: dict[str, Any], username: str
) -> dict[str, Any] | None:
    user = _find_user_recursive(data, username)
    if not user:
        return None
    return wrap_profile(user)


def from_html_meta(html: str, username: str) -> dict[str, Any] | None:
    """Минимальный профиль из meta/og тегов (fallback без API)."""
    import re

    title_m = re.search(
        r'<meta\s+property="og:title"\s+content="([^"]*)"',
        html,
        re.I,
    )
    desc_m = re.search(
        r'<meta\s+property="og:description"\s+content="([^"]*)"',
        html,
        re.I,
    )
    image_m = re.search(
        r'<meta\s+property="og:image"\s+content="([^"]*)"',
        html,
        re.I,
    )
    if not title_m and not desc_m:
        return None

    title = html_module.unescape(title_m.group(1)) if title_m else ""
    display_name = title.split("(")[0].strip() if title else username
    if display_name.startswith("@"):
        display_name = display_name[1:]

    bio = html_module.unescape(desc_m.group(1)) if desc_m else ""
    followers = following = posts = None
    stats_m = re.search(
        r"([\d,.]+[KMB]?)\s+Followers,\s*([\d,.]+[KMB]?)\s+Following,\s*([\d,.]+[KMB]?)\s+Posts",
        bio,
        re.I,
    )
    if stats_m:
        followers, following, posts = stats_m.groups()

    def _parse_count(val: str | None) -> int | None:
        if not val:
            return None
        val = val.replace(",", "").strip().upper()
        mult = 1
        if val.endswith("K"):
            mult = 1_000
            val = val[:-1]
        elif val.endswith("M"):
            mult = 1_000_000
            val = val[:-1]
        elif val.endswith("B"):
            mult = 1_000_000_000
            val = val[:-1]
        try:
            return int(float(val) * mult)
        except ValueError:
            return None

    user: dict[str, Any] = {
        "username": username,
        "full_name": display_name or username,
        "biography": bio,
        "profile_pic_url_hd": image_m.group(1) if image_m else None,
        "edge_followed_by": {"count": _parse_count(followers)},
        "edge_follow": {"count": _parse_count(following)},
        "edge_owner_to_timeline_media": {"count": _parse_count(posts)},
        "source": "html_meta",
    }
    return wrap_profile(user)