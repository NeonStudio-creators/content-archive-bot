"""
Конфигурация бота ContentExplorer.
Все секреты загружаются из переменных окружения или .env файла.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from utils.tokens import normalize_csrf_token, normalize_session_token

# .env — для локального запуска; на деплое используются Variables платформы
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    """Централизованные настройки приложения."""

    # Telegram
    telegram_bot_token: str

    # Авторизация платформы (cookie sessionid)
    session_token: str
    csrf_token: str = ""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    # Базовый URL визуальной экосистемы
    platform_base_url: str = "https://www.instagram.com"
    graphql_endpoint: str = "https://www.instagram.com/api/graphql"
    tiktok_base_url: str = "https://www.tiktok.com"
    tiktok_session_token: str = ""
    tiktok_csrf_token: str = ""

    # «Тихий» режим — задержки, потоки и лимиты
    request_delay_sec: float = 0.4
    max_concurrent_requests: int = 8
    max_retries: int = 2
    retry_backoff_sec: float = 1.5
    pagination_page_size: int = 24
    max_pagination_pages: int = 100
    comments_page_size: int = 50
    max_comment_pages: int = 20
    profile_max_pages: int = 2
    profile_max_tagged_pages: int = 1
    profile_enrich_top_posts: int = 2
    profile_max_highlights_fetch: int = 0

    # Telegram-лимиты
    max_media_per_message: int = 10
    json_dump_threshold_kb: int = 48

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        session = os.getenv("SESSION_TOKEN", "").strip()

        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан в окружении")
        if not session:
            raise ValueError("SESSION_TOKEN не задан в окружении")

        return cls(
            telegram_bot_token=token,
            session_token=normalize_session_token(session),
            csrf_token=normalize_csrf_token(
                os.getenv("CSRF_TOKEN", "").strip()
            ),
            user_agent=os.getenv(
                "USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36",
            ),
            platform_base_url=os.getenv(
                "PLATFORM_BASE_URL", "https://www.instagram.com"
            ).rstrip("/"),
            tiktok_base_url=os.getenv(
                "TIKTOK_BASE_URL", "https://www.tiktok.com"
            ).rstrip("/"),
            tiktok_session_token=normalize_session_token(
                os.getenv("TIKTOK_SESSION_TOKEN", "").strip()
                or os.getenv("TIKTOK_SESSIONID", "").strip()
            ),
            tiktok_csrf_token=normalize_csrf_token(
                os.getenv("TIKTOK_CSRF_TOKEN", "").strip()
                or os.getenv("TIKTOK_CSRF", "").strip()
            ),
            request_delay_sec=float(os.getenv("REQUEST_DELAY_SEC", "0.8")),
            max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "6")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_backoff_sec=float(os.getenv("RETRY_BACKOFF_SEC", "2.0")),
            pagination_page_size=int(os.getenv("PAGINATION_PAGE_SIZE", "24")),
            max_pagination_pages=int(os.getenv("MAX_PAGINATION_PAGES", "100")),
            comments_page_size=int(os.getenv("COMMENTS_PAGE_SIZE", "50")),
            max_comment_pages=int(os.getenv("MAX_COMMENT_PAGES", "20")),
            profile_max_pages=int(os.getenv("PROFILE_MAX_PAGES", "2")),
            profile_max_tagged_pages=int(
                os.getenv("PROFILE_MAX_TAGGED_PAGES", "1")
            ),
            profile_enrich_top_posts=int(
                os.getenv("PROFILE_ENRICH_TOP_POSTS", "2")
            ),
            profile_max_highlights_fetch=int(
                os.getenv("PROFILE_MAX_HIGHLIGHTS_FETCH", "0")
            ),
        )


settings = Settings.from_env


def log_config_status() -> None:
    """Логирует наличие переменных (без значений) — для отладки деплоя."""
    import logging

    log = logging.getLogger("content-explorer")
    required = ("TELEGRAM_BOT_TOKEN", "SESSION_TOKEN")
    optional = (
        "CSRF_TOKEN",
        "TIKTOK_SESSION_TOKEN",
        "TIKTOK_CSRF_TOKEN",
        "REQUEST_DELAY_SEC",
        "MAX_RETRIES",
    )

    for name in required:
        status = "OK" if os.getenv(name, "").strip() else "MISSING"
        log.info("Env %s: %s", name, status)

    for name in optional:
        if os.getenv(name, "").strip():
            log.info("Env %s: OK", name)