"""
Извлечение вложенного JSON из HTML YouTube (ytInitialPlayerResponse).
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_balanced_json(html: str, start: int) -> str | None:
    """Возвращает JSON-объект, начиная с `{` в позиции start."""
    if start >= len(html) or html[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(html)):
        ch = html[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : idx + 1]
    return None


_PLAYER_MARKERS = (
    "ytInitialPlayerResponse",
    "var ytInitialPlayerResponse",
)


def extract_player_response(html: str) -> dict[str, Any] | None:
    """Парсит ytInitialPlayerResponse из HTML watch/m.youtube/embed."""
    for marker in _PLAYER_MARKERS:
        search_from = 0
        while True:
            pos = html.find(marker, search_from)
            if pos < 0:
                break
            search_from = pos + len(marker)
            brace = html.find("{", pos)
            if brace < 0:
                continue
            raw = extract_balanced_json(html, brace)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and (
                data.get("videoDetails")
                or data.get("streamingData")
                or data.get("playabilityStatus")
            ):
                return data
    match = re.search(r'"playerResponse"\s*:\s*\{', html)
    if match:
        brace = match.end() - 1
        raw = extract_balanced_json(html, brace)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None