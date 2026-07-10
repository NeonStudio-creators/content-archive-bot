"""OpenAPI 3.0 — спецификация для сторонних сервисов."""

from __future__ import annotations

from typing import Any


def build_openapi_spec(*, base_url: str) -> dict[str, Any]:
    server = base_url.rstrip("/") or "http://localhost:8080"
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "ContentExplorer Stats API",
            "description": (
                "Подписчики и просмотры Instagram, TikTok, YouTube. "
                "Авторизация: Bearer-токен из STATS_API_TOKENS."
            ),
            "version": "1.0.0",
        },
        "servers": [{"url": server}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "STATS_API_TOKEN (cex_...)",
                },
                "headerAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Token",
                },
            },
            "schemas": {
                "StatsRequest": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {
                            "type": "string",
                            "example": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        }
                    },
                },
                "BatchStatsRequest": {
                    "type": "object",
                    "required": ["urls"],
                    "properties": {
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 20,
                        }
                    },
                },
                "StatsResponse": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "api_version": {"type": "string"},
                        "platform": {
                            "type": "string",
                            "enum": ["instagram", "tiktok", "youtube"],
                        },
                        "entity_type": {
                            "type": "string",
                            "enum": ["profile", "publication"],
                        },
                        "url": {"type": "string"},
                        "username": {"type": "string"},
                        "display_name": {"type": "string"},
                        "stats": {
                            "type": "object",
                            "properties": {
                                "followers": {"type": "integer"},
                                "following": {"type": "integer"},
                                "views": {"type": "integer"},
                                "likes": {"type": "integer"},
                                "comments": {"type": "integer"},
                                "publications": {"type": "integer"},
                                "aggregate_views": {"type": "integer"},
                            },
                        },
                        "error": {"type": "string"},
                    },
                },
            },
        },
        "security": [{"bearerAuth": []}, {"headerAuth": []}],
        "paths": {
            "/api/v1/stats": {
                "get": {
                    "summary": "Статистика по ссылке (авто-платформа)",
                    "parameters": [
                        {
                            "name": "url",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StatsResponse"}
                                }
                            },
                        }
                    },
                },
                "post": {
                    "summary": "Статистика по ссылке (JSON body)",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/StatsRequest"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/api/v1/stats/batch": {
                "post": {
                    "summary": "Пакет до 20 ссылок",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/BatchStatsRequest"
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/{platform}/stats": {
                "get": {
                    "summary": "Статистика для конкретной платформы",
                    "parameters": [
                        {
                            "name": "platform",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "enum": ["instagram", "tiktok", "youtube"],
                            },
                        },
                        {
                            "name": "url",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }