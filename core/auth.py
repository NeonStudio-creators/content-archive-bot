"""
SessionAuthManager — управление авторизацией через cookie session_token.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings


@dataclass
class SessionAuthManager:
    """
    Формирует заголовки и cookies для внутренних GraphQL-запросов.
    session_token маппится на cookie sessionid платформы.
    """

    settings: Settings

    def build_cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {"sessionid": self.settings.session_token}
        if self.settings.csrf_token:
            cookies["csrftoken"] = self.settings.csrf_token
        return cookies

    def build_headers(self, referer: str | None = None) -> dict[str, str]:
        csrf = self.settings.csrf_token or self.settings.session_token[:32]
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-CSRFToken": csrf,
            "X-IG-App-ID": "936619743392459",
            "X-ASBD-ID": "129477",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = f"{self.settings.platform_base_url}/"
        return headers

    def is_configured(self) -> bool:
        return bool(self.settings.session_token)