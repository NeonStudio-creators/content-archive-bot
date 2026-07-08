"""
Безопасная работа с вложенными dict из API (где значения часто null).
"""

from __future__ import annotations

from typing import Any


def safe_dict(value: Any) -> dict[str, Any]:
    """dict или пустой dict — никогда None."""
    return value if isinstance(value, dict) else {}


def dig(data: Any, *keys: str, default: Any = None) -> Any:
    """Безопасный доступ к вложенным ключам."""
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur