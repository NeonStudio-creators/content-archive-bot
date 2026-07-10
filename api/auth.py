"""Проверка API-токенов для Stats API."""

from __future__ import annotations

import hmac
import secrets
from typing import Iterable

from aiohttp import web


def generate_api_token() -> str:
    return f"cex_{secrets.token_urlsafe(32)}"


def parse_api_tokens(raw: str) -> frozenset[str]:
    tokens: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        token = part.strip().strip('"').strip("'")
        if token:
            tokens.add(token)
    return frozenset(tokens)


def extract_request_token(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("X-API-Token", "").strip()
    if header:
        return header
    query = request.rel_url.query.get("api_token", "").strip()
    return query or None


def token_is_valid(provided: str | None, allowed: Iterable[str]) -> bool:
    if not provided:
        return False
    allowed_set = set(allowed)
    if not allowed_set:
        return True
    for expected in allowed_set:
        if hmac.compare_digest(provided, expected):
            return True
    return False


@web.middleware
async def api_auth_middleware(
    request: web.Request,
    handler,
) -> web.StreamResponse:
    public_paths = {"/health", "/api/v1", "/api/v1/openapi.json"}
    if request.method == "OPTIONS" or request.path in public_paths:
        return await handler(request)

    allowed: frozenset[str] = request.app.get("api_tokens") or frozenset()
    if not allowed:
        return await handler(request)

    provided = extract_request_token(request)
    if token_is_valid(provided, allowed):
        return await handler(request)

    return web.json_response(
        {
            "ok": False,
            "error": "Неверный или отсутствующий API-токен",
            "hint": "Authorization: Bearer <token> | X-API-Token | ?api_token=",
        },
        status=401,
    )