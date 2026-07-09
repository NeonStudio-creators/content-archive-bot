"""
TikTokSessionAuthManager — авторизация через cookie sessionid.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from config import Settings
from utils.tokens import normalize_csrf_token, normalize_session_token

logger = logging.getLogger(__name__)

TIKTOK_REFRESHABLE_KEYS = frozenset({
    "tt_csrf_token",
    "csrf_token",
    "ttwid",
    "msToken",
    "odin_tt",
    "s_v_web_id",
})


@dataclass
class TikTokSessionAuthManager:
    """
    Формирует заголовки и cookies для запросов к tiktok.com.
    tiktok_session_token → cookie sessionid.
    """

    settings: Settings
    _runtime_cookies: dict[str, str] = field(default_factory=dict)
    _persist_callback: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def session_id(self) -> str:
        return normalize_session_token(self.settings.tiktok_session_token)

    def set_persist_callback(self, callback: Callable[[], None] | None) -> None:
        self._persist_callback = callback

    def apply_cached_cookies(self, cookies: dict[str, str]) -> None:
        filtered = {k: v for k, v in cookies.items() if v}
        filtered.pop("sessionid", None)
        if filtered:
            self._runtime_cookies.update(filtered)

    def update_runtime_cookies(self, cookies: dict[str, str]) -> None:
        before = self.get_csrf_token()
        filtered = {k: v for k, v in cookies.items() if v}
        filtered.pop("sessionid", None)
        if not filtered:
            return
        self._runtime_cookies.update(filtered)
        after = self.get_csrf_token()
        if after and after != before:
            logger.info("tiktok csrf auto-refreshed")
        if self._persist_callback:
            self._persist_callback()

    def get_csrf_token(self) -> str:
        runtime = (
            self._runtime_cookies.get("tt_csrf_token")
            or self._runtime_cookies.get("csrf_token")
            or ""
        )
        if runtime:
            return normalize_csrf_token(runtime)
        if self.settings.tiktok_csrf_token:
            return normalize_csrf_token(self.settings.tiktok_csrf_token)
        return ""

    def csrf_source_label(self) -> str:
        if (
            self._runtime_cookies.get("tt_csrf_token")
            or self._runtime_cookies.get("csrf_token")
        ):
            return "auto-refresh (tiktok.com)"
        if self.settings.tiktok_csrf_token:
            return "Railway (начальный TIKTOK_CSRF_TOKEN)"
        return "нет"

    def export_refreshable_cookies(self) -> dict[str, str]:
        return {
            k: v
            for k, v in self._runtime_cookies.items()
            if k in TIKTOK_REFRESHABLE_KEYS and v
        }

    def build_cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = dict(self._runtime_cookies)
        if self.session_id:
            cookies["sessionid"] = self.session_id
        csrf = self.get_csrf_token()
        if csrf:
            cookies["tt_csrf_token"] = csrf
        return cookies

    def build_headers(
        self,
        referer: str | None = None,
        *,
        accept: str | None = None,
        for_api: bool = False,
    ) -> dict[str, str]:
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": accept or (
                "application/json, text/plain, */*"
                if for_api
                else "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
            ),
            "Origin": self.settings.tiktok_base_url,
            "Sec-Fetch-Site": "same-origin" if for_api else "none",
            "Sec-Fetch-Mode": "cors" if for_api else "navigate",
            "Sec-Fetch-Dest": "empty" if for_api else "document",
        }
        if for_api:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = f"{self.settings.tiktok_base_url}/"
        return headers

    def is_configured(self) -> bool:
        return bool(self.session_id)