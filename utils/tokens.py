"""Нормализация Instagram cookie-токенов."""

from __future__ import annotations

from urllib.parse import unquote


def normalize_session_token(raw: str) -> str:
    token = raw.strip().strip('"').strip("'")
    if token.lower().startswith("sessionid="):
        token = token.split("=", 1)[1].split(";")[0].strip()
    return unquote(token)


def normalize_csrf_token(raw: str) -> str:
    token = raw.strip().strip('"').strip("'")
    lower = token.lower()
    for prefix in ("csrftoken=", "tt_csrf_token="):
        if lower.startswith(prefix):
            token = token.split("=", 1)[1].split(";")[0].strip()
            break
    return unquote(token)


def parse_cookie_string(raw: str) -> dict[str, str]:
    """Парсит строку cookies: 'SID=abc; SAPISID=xyz' или document.cookie."""
    cookies: dict[str, str] = {}
    text = raw.strip().strip('"').strip("'")
    if not text:
        return cookies
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = unquote(value)
    return cookies


def extract_ds_user_id(session_id: str) -> str | None:
    """Первая часть sessionid до ':' — ds_user_id для cookies."""
    decoded = unquote(session_id.strip())
    if ":" in decoded:
        return decoded.split(":", 1)[0]
    if "%3A" in session_id.lower():
        return unquote(session_id).split(":", 1)[0]
    return None