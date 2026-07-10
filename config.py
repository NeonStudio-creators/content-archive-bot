"""
Конфигурация бота ContentExplorer.
Все секреты загружаются из переменных окружения или .env файла.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from utils.tokens import (
    assemble_youtube_session_token,
    normalize_csrf_token,
    normalize_session_token,
    parse_cookie_string,
)

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
    youtube_base_url: str = "https://www.youtube.com"
    youtube_session_token: str = ""
    youtube_client_version: str = "2.20260114.08.00"

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

    # Автообновление cookies (csrftoken и др.)
    token_cache_path: str = ""
    token_refresh_interval_sec: float = 1800.0

    # HTTP Stats API (подписчики, просмотры)
    api_enabled: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        session = os.getenv("SESSION_TOKEN", "").strip()

        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан в окружении")
        if not session:
            raise ValueError("SESSION_TOKEN не задан в окружении")

        cache_path = os.getenv("TOKEN_CACHE_PATH", "").strip()
        if not cache_path:
            cache_path = str(_PROJECT_ROOT / ".token_cache.json")

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
            youtube_base_url=os.getenv(
                "YOUTUBE_BASE_URL", "https://www.youtube.com"
            ).rstrip("/"),
            youtube_session_token=assemble_youtube_session_token(),
            youtube_client_version=os.getenv(
                "YOUTUBE_CLIENT_VERSION", "2.20260114.08.00"
            ).strip(),
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
            token_cache_path=cache_path,
            token_refresh_interval_sec=float(
                os.getenv("TOKEN_REFRESH_INTERVAL_SEC", "1800")
            ),
            api_enabled=os.getenv("API_ENABLED", "true").strip().lower()
            in ("1", "true", "yes", "on"),
            api_host=os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0",
            api_port=int(
                os.getenv("API_PORT") or os.getenv("PORT") or "8080"
            ),
        )


settings = Settings.from_env


def is_cloud_deploy() -> bool:
    """Railway / другой облачный хостинг (IP датацентра)."""
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RENDER")
        or os.getenv("FLY_APP_NAME")
    )


def deploy_label() -> str:
    if is_cloud_deploy():
        return "облако (Railway)"
    return "локальный ПК/VPS"


def secrets_hint() -> str:
    if is_cloud_deploy():
        return "Variables в панели Railway"
    return "файл .env в папке проекта"


def log_config_status() -> None:
    """Логирует наличие переменных (без значений) — для отладки деплоя."""
    import logging

    log = logging.getLogger("content-explorer")
    log.info("Runtime: %s", deploy_label())
    required = ("TELEGRAM_BOT_TOKEN", "SESSION_TOKEN")
    optional = (
        "CSRF_TOKEN",
        "TIKTOK_SESSION_TOKEN",
        "TIKTOK_CSRF_TOKEN",
        "YOUTUBE_SESSION_TOKEN",
        "TOKEN_CACHE_PATH",
        "TOKEN_REFRESH_INTERVAL_SEC",
        "REQUEST_DELAY_SEC",
        "MAX_RETRIES",
        "API_ENABLED",
        "API_PORT",
    )

    for name in required:
        status = "OK" if os.getenv(name, "").strip() else "MISSING"
        log.info("Env %s: %s", name, status)

    for name in optional:
        if os.getenv(name, "").strip():
            log.info("Env %s: OK", name)

    yt_raw = assemble_youtube_session_token()
    yt_parts = [
        name
        for name in (
            "YOUTUBE_SID",
            "YOUTUBE_SAPISID",
            "YOUTUBE_SECURE_1PSID",
            "YOUTUBE_SECURE1PSID",
            "YOUTUBE_SECURE_1PAPISID",
            "YOUTUBE_SECURE1PAPISID",
        )
        if os.getenv(name, "").strip()
    ]
    if yt_raw.strip():
        log.info(
            "YouTube cookies env: %s chars, keys=%s",
            len(yt_raw.strip()),
            sorted(parse_cookie_string(yt_raw).keys()),
        )
    elif yt_parts:
        log.info("YouTube cookies env: separate vars %s", yt_parts)
    else:
        log.warning("YouTube cookies env: MISSING")