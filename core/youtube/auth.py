"""
YouTubeSessionAuthManager — авторизация через cookies youtube.com.

YOUTUBE_SESSION_TOKEN — строка cookies из браузера (F12 → Application → Cookies),
например: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from config import Settings
from utils.tokens import parse_cookie_string


@dataclass
class YouTubeSessionAuthManager:
    settings: Settings
    _runtime_cookies: dict[str, str] = field(default_factory=dict)

    def _configured_cookies(self) -> dict[str, str]:
        raw = self.settings.youtube_session_token.strip()
        if not raw:
            return {}
        return parse_cookie_string(raw)

    def update_runtime_cookies(self, cookies: dict[str, str]) -> None:
        self._runtime_cookies.update({k: v for k, v in cookies.items() if v})

    def build_cookies(self) -> dict[str, str]:
        cookies = dict(self._configured_cookies())
        cookies.update(self._runtime_cookies)
        return cookies

    def _sapisid(self, cookies: dict[str, str]) -> str:
        for key in (
            "SAPISID",
            "__Secure-1PAPISID",
            "__Secure-3PAPISID",
            "APISID",
        ):
            if cookies.get(key):
                return cookies[key]
        return ""

    def build_authorization(self) -> str:
        cookies = self.build_cookies()
        sapisid = self._sapisid(cookies)
        if not sapisid:
            return ""
        origin = self.settings.youtube_base_url
        ts = str(int(time.time()))
        digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
        return f"SAPISIDHASH {ts}_{digest}"

    def build_headers(
        self,
        *,
        referer: str | None = None,
        accept: str | None = None,
        for_api: bool = False,
    ) -> dict[str, str]:
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": accept or (
                "application/json"
                if for_api
                else "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
            ),
            "Origin": self.settings.youtube_base_url,
            "X-Origin": self.settings.youtube_base_url,
        }
        if for_api:
            headers["Content-Type"] = "application/json"
            auth = self.build_authorization()
            if auth:
                headers["Authorization"] = auth
            headers["X-Youtube-Client-Name"] = "1"
            headers["X-Youtube-Client-Version"] = self.settings.youtube_client_version
        ref = referer or f"{self.settings.youtube_base_url}/"
        headers["Referer"] = ref
        return headers

    def is_configured(self) -> bool:
        cookies = self.build_cookies()
        return bool(
            cookies.get("SID")
            or cookies.get("__Secure-1PSID")
            or cookies.get("SAPISID")
            or cookies.get("__Secure-1PAPISID")
            or len(cookies) >= 2
        )