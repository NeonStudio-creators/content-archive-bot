"""
API credentials для MTProto.

my.telegram.org в РФ часто недоступен — по умолчанию используются
публичные ключи Telegram Desktop (работают без регистрации приложения).
"""

from __future__ import annotations

import os

# Официальные ключи Telegram Desktop (публичные, без my.telegram.org)
TELEGRAM_DESKTOP_API_ID = 2040
TELEGRAM_DESKTOP_API_HASH = "b18441a1ff607e10a989891a5462e627"


def resolve_telegram_api_credentials() -> tuple[int, str, str]:
    """
    Возвращает (api_id, api_hash, source_label).
    source_label: custom | desktop
    """
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    if api_id_raw.isdigit() and api_hash:
        return int(api_id_raw), api_hash, "custom"

    return (
        TELEGRAM_DESKTOP_API_ID,
        TELEGRAM_DESKTOP_API_HASH,
        "desktop",
    )