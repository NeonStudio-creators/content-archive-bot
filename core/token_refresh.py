"""
Автообновление cookies: bootstrap при старте, периодически и после ошибок auth.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config import Settings
from core.token_store import TokenStore

if TYPE_CHECKING:
    from core.auth import SessionAuthManager
    from core.fetcher import GraphQLFetcher
    from core.tiktok.auth import TikTokSessionAuthManager
    from core.tiktok.fetcher import TikTokFetcher
    from core.youtube.auth import YouTubeSessionAuthManager
    from core.youtube.fetcher import YouTubeFetcher

logger = logging.getLogger(__name__)


@dataclass
class TokenRefresher:
    settings: Settings
    store: TokenStore
    instagram_auth: SessionAuthManager
    ig_fetcher: GraphQLFetcher
    tiktok_auth: TikTokSessionAuthManager
    tiktok_fetcher: TikTokFetcher
    youtube_auth: YouTubeSessionAuthManager
    youtube_fetcher: YouTubeFetcher
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    def wire_persist_callbacks(self) -> None:
        self.instagram_auth.set_persist_callback(
            lambda: self.persist_platform("instagram")
        )
        self.tiktok_auth.set_persist_callback(
            lambda: self.persist_platform("tiktok")
        )
        self.youtube_auth.set_persist_callback(
            lambda: self.persist_platform("youtube")
        )

    def load_cached_cookies(self) -> None:
        cached = self.store.load_all()
        if ig := cached.get("instagram"):
            self.instagram_auth.apply_cached_cookies(ig)
            logger.info("instagram cache loaded: %s", list(ig.keys()))
        if tt := cached.get("tiktok"):
            self.tiktok_auth.apply_cached_cookies(tt)
            logger.info("tiktok cache loaded: %s", list(tt.keys()))
        if yt := cached.get("youtube"):
            self.youtube_auth.apply_cached_cookies(yt)
            logger.info("youtube cache loaded: %s", list(yt.keys()))

    def persist_platform(self, platform: str) -> None:
        exporters = {
            "instagram": self.instagram_auth.export_refreshable_cookies,
            "tiktok": self.tiktok_auth.export_refreshable_cookies,
            "youtube": self.youtube_auth.export_refreshable_cookies,
        }
        export = exporters.get(platform)
        if not export:
            return
        cookies = export()
        if cookies:
            self.store.update_platform(platform, cookies)

    async def bootstrap_all(self, *, force: bool = False) -> None:
        tasks = [
            self.ig_fetcher.ensure_session(force=force),
            self.tiktok_fetcher.ensure_session(force=force),
            self.youtube_fetcher.ensure_session(force=force),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        self.persist_all()

    def persist_all(self) -> None:
        data = {
            "instagram": self.instagram_auth.export_refreshable_cookies(),
            "tiktok": self.tiktok_auth.export_refreshable_cookies(),
            "youtube": self.youtube_auth.export_refreshable_cookies(),
        }
        self.store.save_all(data)

    async def startup(self) -> None:
        self.load_cached_cookies()
        self.wire_persist_callbacks()
        await self.bootstrap_all(force=True)
        logger.info("token auto-refresh: startup bootstrap complete")

    async def refresh_on_auth_failure(self, platform: str) -> None:
        logger.info("token auto-refresh: auth failure on %s", platform)
        if platform == "instagram":
            await self.ig_fetcher.ensure_session(force=True)
            self.persist_platform("instagram")
        elif platform == "tiktok":
            await self.tiktok_fetcher.ensure_session(force=True)
            self.persist_platform("tiktok")
        elif platform == "youtube":
            await self.youtube_fetcher.bootstrap_auth_session(force=True)
            self.persist_platform("youtube")

    async def periodic_loop(self) -> None:
        interval = self.settings.token_refresh_interval_sec
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await self.bootstrap_all(force=True)
                logger.info(
                    "token auto-refresh: periodic bootstrap (every %.0fs)",
                    interval,
                )
            except Exception as exc:
                logger.warning("token auto-refresh periodic failed: %s", exc)

    def start_background(self) -> None:
        if self.settings.token_refresh_interval_sec <= 0:
            logger.info("token auto-refresh: periodic disabled")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self.periodic_loop(),
            name="token-refresh",
        )

    async def stop_background(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.persist_all()