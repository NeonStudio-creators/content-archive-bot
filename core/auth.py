"""
SessionAuthManager — управление авторизацией через cookie session_token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from config import Settings
from utils.tokens import normalize_csrf_token, normalize_session_token

MOBILE_APP_ID = "567067343352427"
WEB_APP_ID = "936619743392459"

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

    @property
    def session_id(self) -> str:
        return normalize_session_token(self.settings.session_token)

    def update_runtime_cookies(self, cookies: dict[str, str]) -> None:
        self._runtime_cookies.update({k: v for k, v in cookies.items() if v})

    def get_csrf_token(self) -> str:
        if self.settings.csrf_token:
            return normalize_csrf_token(self.settings.csrf_token)
        return self._runtime_cookies.get("csrftoken", "")

    def build_cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {"sessionid": self.session_id}
        csrf = self.get_csrf_token()
        if csrf:
            cookies["csrftoken"] = csrf
        cookies.update(self._runtime_cookies)
        cookies["sessionid"] = self.session_id
        return cookies

    def build_headers(
        self,
        referer: str | None = None,
        *,
        api_type: str = "web",
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
                "X-ASBD-ID": "359341",
                "X-IG-WWW-Claim": "0",
                "Origin": self.settings.platform_base_url,
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }

        if csrf:
            headers["X-CSRFToken"] = csrf

        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = f"{self.settings.platform_base_url}/"
        return headers

    def is_configured(self) -> bool:
        return bool(self.session_id)