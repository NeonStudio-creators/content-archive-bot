"""
Поддерживаемые платформы контента.
"""

from __future__ import annotations

from enum import Enum


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"