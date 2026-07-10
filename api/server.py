"""
HTTP API для сторонних сервисов — подписчики и просмотры.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from api.auth import api_auth_middleware
from api.middleware import API_VERSION, cors_middleware
from api.models import StatsResponse
from api.openapi import build_openapi_spec
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

_BATCH_LIMIT = 20


def _json_response(
    data: StatsResponse | dict,
    *,
    status: int = 200,
) -> web.Response:
    payload = data.to_dict() if isinstance(data, StatsResponse) else data
    return web.json_response(
        payload,
        status=status,
        dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2),
    )


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


async def _read_batch_urls(request: web.Request) -> list[str]:
    if not request.can_read_body:
        return []
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return []
    if not isinstance(body, dict):
        return []
    raw = body.get("urls")
    if not isinstance(raw, list):
        return []
    return [str(u).strip() for u in raw if u]


def create_app(
    orchestrator: ArchiveOrchestrator,
    settings: Settings,
) -> web.Application:
    stats = StatsService(orchestrator)
    public_url = settings.api_public_url

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "service": "content-explorer-api",
            "api_version": API_VERSION,
        })

    async def api_info(_request: web.Request) -> web.Response:
        return _json_response({
            "ok": True,
            "api_version": API_VERSION,
            "name": "ContentExplorer Stats API",
            "platforms": ["instagram", "tiktok", "youtube"],
            "auth": "Authorization: Bearer <STATS_API_TOKEN>",
            "endpoints": {
                "stats": f"{public_url}/api/v1/stats",
                "batch": f"{public_url}/api/v1/stats/batch",
                "instagram": f"{public_url}/api/v1/instagram/stats",
                "tiktok": f"{public_url}/api/v1/tiktok/stats",
                "youtube": f"{public_url}/api/v1/youtube/stats",
                "openapi": f"{public_url}/api/v1/openapi.json",
            },
        })

    async def openapi(_request: web.Request) -> web.Response:
        return web.json_response(build_openapi_spec(base_url=public_url))

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

    async def stats_batch(request: web.Request) -> web.Response:
        urls = await _read_batch_urls(request)
        if not urls:
            return _json_response(
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": "JSON body: {\"urls\": [\"https://...\", ...]}",
                },
                status=400,
            )
        if len(urls) > _BATCH_LIMIT:
            return _json_response(
                {
                    "ok": False,
                    "api_version": API_VERSION,
                    "error": f"Максимум {_BATCH_LIMIT} ссылок за запрос",
                },
                status=400,
            )
        payload = await stats.fetch_batch(urls, max_items=_BATCH_LIMIT)
        status = 200 if payload.get("ok") else 207
        return _json_response(payload, status=status)

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

    app = web.Application(middlewares=[cors_middleware, api_auth_middleware])
    app["api_tokens"] = settings.stats_api_tokens
    app["api_cors_origins"] = settings.api_cors_origins

    app.router.add_get("/health", health)
    app.router.add_get("/api/v1", api_info)
    app.router.add_get("/api/v1/openapi.json", openapi)
    app.router.add_get("/api/v1/stats", stats_any)
    app.router.add_post("/api/v1/stats", stats_any)
    app.router.add_post("/api/v1/stats/batch", stats_batch)
    app.router.add_get("/api/v1/{platform}/stats", stats_platform)
    app.router.add_post("/api/v1/{platform}/stats", stats_platform)
    async def _options(_request: web.Request) -> web.Response:
        return web.Response(status=204)

    app.router.add_route("OPTIONS", "/api/v1", _options)
    app.router.add_route("OPTIONS", "/api/v1/stats", _options)
    app.router.add_route("OPTIONS", "/api/v1/stats/batch", _options)
    app.router.add_route("OPTIONS", "/api/v1/{platform}/stats", _options)

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
    base = settings.api_public_url or f"http://{host}:{port}"
    logger.info("ContentExplorer API: %s", base)
    logger.info("Документация: %s/api/v1/openapi.json", base.rstrip("/"))
    if settings.stats_api_tokens:
        logger.info("Авторизация: %s токен(ов) STATS_API_TOKENS", len(settings.stats_api_tokens))
    else:
        logger.warning("STATS_API_TOKENS не задан — API открыт без авторизации")
    return runner