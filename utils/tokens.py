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
    if token.lower().startswith("csrftoken="):
        token = token.split("=", 1)[1].split(";")[0].strip()
    return unquote(token)