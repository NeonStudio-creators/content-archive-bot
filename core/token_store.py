"""
Локальный кэш обновлённых cookies (csrftoken, mid, …) между перезапусками.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLATFORMS = ("instagram", "tiktok", "youtube")


class TokenStore:
    """JSON-файл с runtime-cookies, которые бот обновляет сам."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load_all(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("token cache read failed: %s", exc)
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for platform in PLATFORMS:
            block = raw.get(platform)
            if isinstance(block, dict):
                result[platform] = {
                    str(k): str(v)
                    for k, v in block.items()
                    if k and v
                }
        return result

    def save_all(self, data: dict[str, dict[str, str]]) -> None:
        payload: dict[str, Any] = {
            platform: data.get(platform, {})
            for platform in PLATFORMS
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("token cache saved: %s", self.path)
        except OSError as exc:
            logger.warning("token cache write failed: %s", exc)

    def update_platform(self, platform: str, cookies: dict[str, str]) -> None:
        if not cookies:
            return
        all_data = self.load_all()
        merged = {**all_data.get(platform, {}), **cookies}
        all_data[platform] = merged
        self.save_all(all_data)