"""Проверка YouTube-сессии для /session."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class YouTubeSessionVerify:
    configured: bool
    cookie_count: int
    session_ok: bool
    visitor_ok: bool
    test_streams: int = 0
    client: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.session_ok and self.test_streams > 0