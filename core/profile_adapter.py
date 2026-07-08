"""
Нормализация ответов profile API в единый формат data.user.
"""

from __future__ import annotations

from typing import Any

from utils.dict_utils import dig, safe_dict


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