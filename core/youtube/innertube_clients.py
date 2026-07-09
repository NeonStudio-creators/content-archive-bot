"""
Конфигурации InnerTube-клиентов YouTube (обход bot-check на сервере).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InnertubeClient:
    name: str
    version: str
    user_agent: str
    client_extra: dict[str, Any] = field(default_factory=dict)
    needs_cookies: bool = False
    embed_url: str | None = None


# Порядок: сначала клиенты без cookies, затем WEB с сессией.
INNERTUBE_CLIENTS: tuple[InnertubeClient, ...] = (
    InnertubeClient(
        name="ANDROID",
        version="19.29.37",
        user_agent=(
            "com.google.android.youtube/19.29.37 "
            "(Linux; U; Android 11) gzip"
        ),
        client_extra={"androidSdkVersion": 30, "osName": "Android", "osVersion": "11"},
    ),
    InnertubeClient(
        name="ANDROID_EMBEDDED",
        version="19.29.37",
        user_agent=(
            "com.google.android.youtube/19.29.37 "
            "(Linux; U; Android 11) gzip"
        ),
        client_extra={"androidSdkVersion": 30, "osName": "Android", "osVersion": "11"},
        embed_url="https://www.youtube.com/embed/{video_id}",
    ),
    InnertubeClient(
        name="IOS",
        version="19.29.3",
        user_agent=(
            "com.google.ios.youtube/19.29.3 "
            "(iPhone14,3; U; CPU iOS 16_6 like Mac OS X)"
        ),
        client_extra={
            "deviceModel": "iPhone14,3",
            "osName": "iPhone",
            "osVersion": "16_6.0",
        },
    ),
    InnertubeClient(
        name="TVHTML5",
        version="7.20240724.00.00",
        user_agent=(
            "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version"
        ),
        client_extra={"clientFormFactor": "UNKNOWN_FORM_FACTOR"},
    ),
    InnertubeClient(
        name="WEB_EMBEDDED_PLAYER",
        version="2.20240401.00.00",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        client_extra={"clientScreen": "EMBED"},
        embed_url="https://www.youtube.com/embed/{video_id}",
    ),
    InnertubeClient(
        name="MWEB",
        version="2.20240701.00.00",
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/16.6 Mobile/15E148 Safari/604.36"
        ),
        needs_cookies=True,
    ),
    InnertubeClient(
        name="WEB",
        version="2.20240710.00.00",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        needs_cookies=True,
    ),
)


def build_client_context(
    client: InnertubeClient,
    *,
    visitor_id: str = "",
    web_client_version: str | None = None,
    video_id: str = "",
) -> dict[str, Any]:
    version = web_client_version if client.name == "WEB" and web_client_version else client.version
    ctx: dict[str, Any] = {
        "clientName": client.name,
        "clientVersion": version,
        "hl": "en",
        "gl": "US",
        "userAgent": client.user_agent,
        **client.client_extra,
    }
    if visitor_id:
        ctx["visitorData"] = visitor_id
    context: dict[str, Any] = {"client": ctx}
    if client.embed_url:
        embed = client.embed_url
        if "{video_id}" in embed and video_id:
            embed = embed.format(video_id=video_id)
        context["thirdParty"] = {"embedUrl": embed}
    return context