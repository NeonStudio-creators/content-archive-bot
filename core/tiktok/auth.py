"""
TikTokSessionAuthManager — авторизация через cookie sessionid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import Settings
from utils.tokens import normalize_csrf_token, normalize_session_token


@dataclass
class TikTokSessionAuthManager:
    """
    Формирует заголовки и cookies для запросов к tiktok.com.
    tiktok_session_token → cookie sessionid.
    """

    settings: Settings
    _runtime_cookies: dict[str, str] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return normalize_session_token(self.settings.tiktok_session_token)

    def update_runtime_cookies(self, cookies: dict[str, str]) -> None:
        filtered = {k: v for k, v in cookies.items() if v}
        # sessionid всегда из настроек, не перезаписываем bootstrap-ом
        filtered.pop("sessionid", None)
        self._runtime_cookies.update(filtered)

    def get_csrf_token(self) -> str:
        if self.settings.tiktok_csrf_token:
            return normalize_csrf_token(self.settings.tiktok_csrf_token)
        return (
            self._runtime_cookies.get("tt_csrf_token")
            or self._runtime_cookies.get("csrf_token")
            or ""
        )

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