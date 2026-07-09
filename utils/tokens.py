"""Нормализация Instagram cookie-токенов."""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# Частые опечатки при копировании cookies YouTube из браузера.
_COOKIE_KEY_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'__Secure-"1PAPISID', re.I), "__Secure-1PAPISID"),
    (re.compile(r'__Secure-"1PSID', re.I), "__Secure-1PSID"),
    (re.compile(r'__Secure-"3PAPISID', re.I), "__Secure-3PAPISID"),
    (re.compile(r'__Secure-"3PSID', re.I), "__Secure-3PSID"),
)

# Railway/копипаст без дефиса в имени cookie.
_COOKIE_KEY_ALIASES: dict[str, str] = {
    "__Secure1PSID": "__Secure-1PSID",
    "__Secure1PAPISID": "__Secure-1PAPISID",
    "__Secure3PSID": "__Secure-3PSID",
    "__Secure3PAPISID": "__Secure-3PAPISID",
    "Secure1PSID": "__Secure-1PSID",
    "Secure1PAPISID": "__Secure-1PAPISID",
}


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


def _strip_cookie_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _normalize_cookie_key(key: str) -> str:
    """Убирает кавычки внутри имени cookie и типичные опечатки."""
    key = _strip_cookie_quotes(key.strip())
    key = key.replace('"', "").replace("'", "")
    alias = _COOKIE_KEY_ALIASES.get(key)
    if alias:
        logger.warning("cookie key fixed: %s → %s", key, alias)
        return alias
    for pattern, replacement in _COOKIE_KEY_FIXES:
        if pattern.search(key):
            fixed = pattern.sub(replacement, key)
            if fixed != key:
                logger.warning(
                    "cookie key fixed: %s → %s", key, fixed
                )
            key = fixed
    return key.strip()


def _is_valid_cookie_key(key: str) -> bool:
    if not key:
        return False
    if re.search(r'[\s;,"\'\x00-\x1f]', key):
        return False
    return True


def parse_cookie_string(raw: str) -> dict[str, str]:
    """Парсит строку cookies: 'SID=abc; SAPISID=xyz' или document.cookie."""
    cookies: dict[str, str] = {}
    text = raw.strip()
    if not text:
        return cookies
    lower = text.lower()
    if lower.startswith("youtube_session_token="):
        text = text.split("=", 1)[1].strip()
    if not text:
        return cookies
    for part in re.split(r"[;\r\n]+", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        raw_key = key.strip()
        key = _normalize_cookie_key(raw_key)
        value = _strip_cookie_quotes(value.strip())
        if not _is_valid_cookie_key(key):
            logger.warning("cookie key skipped (invalid): %r", raw_key)
            continue
        cookies[key] = unquote(value)
    return cookies


_YOUTUBE_ENV_KEYS: tuple[tuple[str, str], ...] = (
    ("SID", "YOUTUBE_SID"),
    ("HSID", "YOUTUBE_HSID"),
    ("SSID", "YOUTUBE_SSID"),
    ("APISID", "YOUTUBE_APISID"),
    ("SAPISID", "YOUTUBE_SAPISID"),
    ("__Secure-1PSID", "YOUTUBE_SECURE_1PSID"),
    ("__Secure-1PAPISID", "YOUTUBE_SECURE_1PAPISID"),
    ("__Secure-1PSID", "YOUTUBE_SECURE1PSID"),
    ("__Secure-1PAPISID", "YOUTUBE_SECURE1PAPISID"),
    ("__Secure-3PSID", "YOUTUBE_SECURE_3PSID"),
    ("__Secure-3PAPISID", "YOUTUBE_SECURE_3PAPISID"),
    ("LOGIN_INFO", "YOUTUBE_LOGIN_INFO"),
)


def assemble_youtube_session_token() -> str:
    """
    Собирает cookies из YOUTUBE_SESSION_TOKEN и/или отдельных переменных.
    """
    import os

    chunks: list[str] = []
    for env_name in ("YOUTUBE_SESSION_TOKEN", "YOUTUBE_COOKIES"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            chunks.append(raw)
    for cookie_name, env_name in _YOUTUBE_ENV_KEYS:
        value = os.getenv(env_name, "").strip()
        if value:
            chunks.append(f"{cookie_name}={value}")
    return "\n".join(chunks)


def extract_ds_user_id(session_id: str) -> str | None:
    """Первая часть sessionid до ':' — ds_user_id для cookies."""
    decoded = unquote(session_id.strip())
    if ":" in decoded:
        return decoded.split(":", 1)[0]
    if "%3A" in session_id.lower():
        return unquote(session_id).split(":", 1)[0]
    return None