"""
YouTubeSessionAuthManager — авторизация через cookies youtube.com.

YOUTUBE_SESSION_TOKEN — строка cookies из браузера (F12 → Application → Cookies),
например: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from config import Settings
from utils.tokens import parse_cookie_string

logger = logging.getLogger(__name__)

YOUTUBE_REQUIRED_KEYS = (
    ("SID", "__Secure-1PSID"),  # хотя бы один — сессия
    ("SAPISID", "__Secure-1PAPISID"),  # хотя бы один — авторизация API
)

YOUTUBE_REFRESHABLE_KEYS = frozenset({
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "LOGIN_INFO",
    "VISITOR_INFO1_LIVE",
    "YSC",
    "PREF",
    "GPS",
    "__Secure-YEC",
    "ST-tladl",
})


@dataclass
class YouTubeSessionAuthManager:
    settings: Settings
    _runtime_cookies: dict[str, str] = field(default_factory=dict)
    _persist_callback: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )

    def set_persist_callback(self, callback: Callable[[], None] | None) -> None:
        self._persist_callback = callback

    def _configured_cookies(self) -> dict[str, str]:
        raw = self.settings.youtube_session_token.strip()
        if not raw:
            return {}
        return parse_cookie_string(raw)

    def apply_cached_cookies(self, cookies: dict[str, str]) -> None:
        filtered = {k: v for k, v in cookies.items() if v}
        if filtered:
            self._runtime_cookies.update(filtered)

    def update_runtime_cookies(self, cookies: dict[str, str]) -> None:
        before = set(self._runtime_cookies.keys())
        filtered = {k: v for k, v in cookies.items() if v}
        if not filtered:
            return
        self._runtime_cookies.update(filtered)
        rotated = set(self._runtime_cookies.keys()) - before
        session_keys = rotated & {
            "SID",
            "__Secure-1PSID",
            "SAPISID",
            "__Secure-1PAPISID",
            "VISITOR_INFO1_LIVE",
        }
        if session_keys:
            logger.info("youtube cookies auto-refreshed: %s", sorted(session_keys))
        if self._persist_callback:
            self._persist_callback()

    def export_refreshable_cookies(self) -> dict[str, str]:
        exported = {
            k: v
            for k, v in self._runtime_cookies.items()
            if k in YOUTUBE_REFRESHABLE_KEYS and v
        }
        cookies = self.build_cookies()
        for key in YOUTUBE_REFRESHABLE_KEYS:
            if key in cookies and cookies[key] and key not in exported:
                exported[key] = cookies[key]
        return exported

    def session_source_label(self) -> str:
        runtime_session = bool(
            self._runtime_cookies.get("SID")
            or self._runtime_cookies.get("__Secure-1PSID")
        )
        if runtime_session:
            return "auto-refresh (youtube.com)"
        if self._configured_cookies():
            return "Railway (начальный YOUTUBE_SESSION_TOKEN)"
        return "нет"

    def build_cookies(self) -> dict[str, str]:
        from utils.tokens import _is_valid_cookie_key, _normalize_cookie_key

        cookies: dict[str, str] = {}
        for key, value in {
            **self._configured_cookies(),
            **self._runtime_cookies,
        }.items():
            norm = _normalize_cookie_key(key)
            if _is_valid_cookie_key(norm) and value:
                cookies[norm] = value
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

    def env_cookie_keys(self) -> list[str]:
        return sorted(self._configured_cookies().keys())

    def env_raw_length(self) -> int:
        return len(self.settings.youtube_session_token.strip())

    def cookie_diagnostic(self) -> dict[str, object]:
        configured = self._configured_cookies()
        built = self.build_cookies()
        missing: list[str] = []
        has_session = bool(built.get("SID") or built.get("__Secure-1PSID"))
        has_auth = bool(
            built.get("SAPISID") or built.get("__Secure-1PAPISID")
        )
        if not has_session:
            missing.append("SID или __Secure-1PSID")
        if not has_auth:
            missing.append("SAPISID или __Secure-1PAPISID")
        return {
            "env_len": self.env_raw_length(),
            "env_keys": self.env_cookie_keys(),
            "runtime_keys": sorted(self._runtime_cookies.keys()),
            "all_keys": sorted(built.keys()),
            "missing": missing,
            "ok": has_session and has_auth,
        }

    def is_configured(self) -> bool:
        diag = self.cookie_diagnostic()
        return bool(diag["ok"])