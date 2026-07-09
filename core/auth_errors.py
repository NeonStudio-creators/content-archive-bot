"""
Детекция ошибок авторизации в ответах Instagram / TikTok / YouTube.
"""

from __future__ import annotations

import json
from typing import Any

_INSTAGRAM_AUTH_MARKERS = (
    "login_required",
    "user_logged_out",
    "checkpoint_required",
    "challenge_required",
    "csrf",
    "please wait",
    "consent_required",
)

_TIKTOK_AUTH_MARKERS = (
    "login",
    "session expired",
    "not login",
    "verify",
)


def _parse_json(body: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def is_instagram_auth_error(
    status: int,
    body: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    if status in (401, 403):
        return True
    data = payload if payload is not None else _parse_json(body)
    if not data:
        return False
    if data.get("status") == "fail":
        msg = str(data.get("message", "")).lower()
        return any(marker in msg for marker in _INSTAGRAM_AUTH_MARKERS)
    return False


def is_tiktok_auth_error(status: int, body: str) -> bool:
    if status in (401, 403):
        return True
    low = body[:500].lower()
    return any(marker in low for marker in _TIKTOK_AUTH_MARKERS)


def is_youtube_auth_error(status: int, body: str) -> bool:
    if status not in (401, 403):
        return False
    low = body[:500].lower()
    return "sign in" in low or "login" in low or "unauthorized" in low