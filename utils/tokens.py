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
    for part in text.split(";"):
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


def extract_ds_user_id(session_id: str) -> str | None:
    """Первая часть sessionid до ':' — ds_user_id для cookies."""
    decoded = unquote(session_id.strip())
    if ":" in decoded:
        return decoded.split(":", 1)[0]
    if "%3A" in session_id.lower():
        return unquote(session_id).split(":", 1)[0]
    return None