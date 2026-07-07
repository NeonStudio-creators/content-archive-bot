"""
Конвертация shortcode ↔ numeric media_id (алгоритм Instagram).
"""

from __future__ import annotations

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def shortcode_to_media_id(shortcode: str) -> str:
    """Преобразует shortcode (DacaunvRdTv) в числовой media_id для API."""
    code = shortcode.strip()
    if len(code) > 28:
        code = code[:-28]

    num = 0
    for char in code:
        num = num * 64 + _ALPHABET.index(char)
    return str(num)