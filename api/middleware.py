"""Middleware для внешних клиентов (CORS, метаданные ответа)."""

from __future__ import annotations

from aiohttp import web

API_VERSION = "v1"


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    origins: frozenset[str] = request.app.get("api_cors_origins") or frozenset()
    origin = request.headers.get("Origin", "")

    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)

    if not origins:
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
    elif "*" in origins:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in origins:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"

    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, X-API-Token"
    )
    resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["X-API-Version"] = API_VERSION
    return resp