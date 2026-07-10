"""
aiohttp-сервер Stats API.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from api.auth import api_auth_middleware
from api.models import StatsResponse
from api.stats_service import StatsService
from config import Settings
from core.platforms import Platform

if TYPE_CHECKING:
    from core.orchestrator import ArchiveOrchestrator

logger = logging.getLogger(__name__)

_PLATFORM_ROUTES = {
    "instagram": Platform.INSTAGRAM,
    "tiktok": Platform.TIKTOK,
    "youtube": Platform.YOUTUBE,
}


def _json_response(
    data: StatsResponse | dict,
    *,
    status: int = 200,
) -> web.Response:
    payload = data.to_dict() if isinstance(data, StatsResponse) else data
    return web.json_response(payload, status=status, dumps=lambda o: json.dumps(
        o, ensure_ascii=False, indent=2
    ))


async def _read_url(request: web.Request) -> str | None:
    url = request.rel_url.query.get("url", "").strip()
    if url:
        return url
    if request.can_read_body and request.content_type.startswith("application/json"):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return None
        if isinstance(body, dict):
            raw = body.get("url")
            return str(raw).strip() if raw else None
    return None


def create_app(
    orchestrator: ArchiveOrchestrator,
    settings: Settings,
) -> web.Application:
    stats = StatsService(orchestrator)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "content-explorer-stats"})

    async def stats_any(request: web.Request) -> web.Response:
        url = await _read_url(request)
        if not url:
            return _json_response(
                StatsResponse(
                    ok=False,
                    platform="unknown",
                    entity_type="unknown",
                    url="",
                    error="Параметр url обязателен (query или JSON body)",
                ),
                status=400,
            )
        result = await stats.fetch(url)
        return _json_response(result, status=200 if result.ok else 502)

    async def stats_platform(request: web.Request) -> web.Response:
        name = request.match_info.get("platform", "").lower()
        platform = _PLATFORM_ROUTES.get(name)
        if not platform:
            return _json_response(
                StatsResponse(
                    ok=False,
                    platform=name or "unknown",
                    entity_type="unknown",
                    url="",
                    error="Платформа: instagram, tiktok, youtube",
                ),
                status=404,
            )
        url = await _read_url(request)
        if not url:
            return _json_response(
                StatsResponse(
                    ok=False,
                    platform=name,
                    entity_type="unknown",
                    url="",
                    error="Параметр url обязателен",
                ),
                status=400,
            )
        result = await stats.fetch(url, platform=platform)
        return _json_response(result, status=200 if result.ok else 502)

    app = web.Application(middlewares=[api_auth_middleware])
    app["api_tokens"] = settings.stats_api_tokens
    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/stats", stats_any)
    app.router.add_post("/api/v1/stats", stats_any)
    app.router.add_get("/api/v1/{platform}/stats", stats_platform)
    app.router.add_post("/api/v1/{platform}/stats", stats_platform)
    app["orchestrator"] = orchestrator
    return app


async def start_api_server(
    orchestrator: ArchiveOrchestrator,
    settings: Settings,
    *,
    host: str,
    port: int,
) -> web.AppRunner:
    app = create_app(orchestrator, settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Stats API слушает http://%s:%s", host, port)
    if settings.stats_api_tokens:
        logger.info(
            "Stats API: авторизация включена (%s токенов)",
            len(settings.stats_api_tokens),
        )
    else:
        logger.warning(
            "Stats API: STATS_API_TOKENS не задан — доступ без авторизации"
        )
    logger.info(
        "Эндпоинты: GET /api/v1/stats?url=… | /api/v1/instagram/stats | tiktok | youtube"
    )
    return runner