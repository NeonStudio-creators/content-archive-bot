"""
SessionAuthManager — управление авторизацией через cookie session_token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from config import Settings
from utils.tokens import (
    extract_ds_user_id,
    normalize_csrf_token,
    normalize_session_token,
)

MOBILE_APP_ID = "567067343352427"
WEB_APP_ID = "936619743392459"
WEB_ASBD_ID = "129477"

logger = logging.getLogger(__name__)

INSTAGRAM_REFRESHABLE_KEYS = frozenset({
    "csrftoken",
    "mid",
    "ig_did",
    "ig_nrcb",
    "lsd",
    "rur",
    "wd",
    "dpr",
})

MOBILE_USER_AGENT = (
    "Instagram 385.0.0.47.74 Android (34/14; 480dpi; 1344x2992; "
    "Google/google; Pixel 8 Pro; husky; husky; en_US; 378906843)"
)


@dataclass
class SessionAuthManager:
    """
    Формирует заголовки и cookies для внутренних GraphQL-запросов.
    session_token маппится на cookie sessionid платформы.
    """

    settings: Settings
    _runtime_cookies: dict[str, str] = field(default_factory=dict)
    _persist_callback: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def session_id(self) -> str:
        return normalize_session_token(self.settings.session_token)

    def set_persist_callback(self, callback: Callable[[], None] | None) -> None:
        self._persist_callback = callback

    def apply_cached_cookies(self, cookies: dict[str, str]) -> None:
        filtered = {k: v for k, v in cookies.items() if v and k != "sessionid"}
        if filtered:
            self._runtime_cookies.update(filtered)

    def update_runtime_cookies(self, cookies: dict[str, str]) -> None:
        before_csrf = self._runtime_cookies.get("csrftoken")
        filtered = {k: v for k, v in cookies.items() if v and k != "sessionid"}
        if not filtered:
            return
        self._runtime_cookies.update(filtered)
        after_csrf = self._runtime_cookies.get("csrftoken")
        if after_csrf and after_csrf != before_csrf:
            logger.info("instagram csrftoken auto-refreshed")
        if self._persist_callback:
            self._persist_callback()

    def get_csrf_token(self) -> str:
        runtime = self._runtime_cookies.get("csrftoken", "")
        if runtime:
            return normalize_csrf_token(runtime)
        if self.settings.csrf_token:
            return normalize_csrf_token(self.settings.csrf_token)
        return ""

    def csrf_source_label(self) -> str:
        if self._runtime_cookies.get("csrftoken"):
            return "auto-refresh (instagram.com)"
        if self.settings.csrf_token:
            return "Railway (начальный CSRF_TOKEN)"
        return "нет"

    def export_refreshable_cookies(self) -> dict[str, str]:
        return {
            k: v
            for k, v in self._runtime_cookies.items()
            if k in INSTAGRAM_REFRESHABLE_KEYS and v
        }

    def build_cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {"sessionid": self.session_id}
        csrf = self.get_csrf_token()
        if csrf:
            cookies["csrftoken"] = csrf
        ds_uid = extract_ds_user_id(self.session_id)
        if ds_uid:
            cookies["ds_user_id"] = ds_uid
        cookies.update(self._runtime_cookies)
        cookies["sessionid"] = self.session_id
        return cookies

    def build_headers(
        self,
        referer: str | None = None,
        *,
        api_type: str = "web",
        for_graphql: bool = False,
    ) -> dict[str, str]:
        csrf = self.get_csrf_token()
        if api_type == "mobile":
            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US",
                "X-IG-App-ID": MOBILE_APP_ID,
                "X-IG-Capabilities": "3brTvx0=",
                "X-IG-Connection-Type": "WIFI",
                "X-IG-Connection-Speed": "3700kbps",
                "X-IG-Bandwidth-Speed-KBPS": "0",
                "X-IG-Bandwidth-TotalBytes-B": "0",
                "X-IG-Bandwidth-TotalTime-MS": "0",
            }
        else:
            headers = {
                "User-Agent": self.settings.user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "X-IG-App-ID": WEB_APP_ID,
                "X-ASBD-ID": WEB_ASBD_ID,
                "X-IG-WWW-Claim": "0",
                "Origin": self.settings.platform_base_url,
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            }

        if csrf:
            headers["X-CSRFToken"] = csrf

        if for_graphql:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["X-FB-Friendly-Name"] = "PolarisAPI"

        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = f"{self.settings.platform_base_url}/"
        return headers

    def build_web_api_headers(self, referer: str) -> dict[str, str]:
        """Заголовки для /api/v1/users/web_profile_info/ (как в instagrapi)."""
        csrf = self.get_csrf_token()
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": WEB_APP_ID,
            "X-ASBD-ID": WEB_ASBD_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Referer": referer,
        }
        if csrf:
            headers["X-CSRFToken"] = csrf
        return headers

    def is_configured(self) -> bool:
        return bool(self.session_id)