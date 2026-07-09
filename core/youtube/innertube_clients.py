"""
Конфигурации InnerTube-клиентов YouTube (обход bot-check на сервере).
Версии синхронизированы с yt-dlp (2026-01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InnertubeClient:
    name: str
    version: str
    user_agent: str
    client_id: int
    client_extra: dict[str, Any] = field(default_factory=dict)
    needs_cookies: bool = False
    prefer_cookies: bool = False
    embed_url: str | None = None


# Порядок: сначала клиенты без обязательной авторизации.
INNERTUBE_CLIENTS: tuple[InnertubeClient, ...] = (
    InnertubeClient(
        name="ANDROID_VR",
        version="1.65.10",
        client_id=28,
        user_agent=(
            "com.google.android.apps.youtube.vr.oculus/1.65.10 "
            "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"
        ),
        client_extra={
            "deviceMake": "Oculus",
            "deviceModel": "Quest 3",
            "androidSdkVersion": 32,
            "osName": "Android",
            "osVersion": "12L",
        },
    ),
    InnertubeClient(
        name="WEB_EMBEDDED_PLAYER",
        version="1.20260115.01.00",
        client_id=56,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        client_extra={"clientScreen": "EMBED"},
        embed_url="https://www.youtube.com/embed/{video_id}",
    ),
    InnertubeClient(
        name="TVHTML5",
        version="7.20260114.12.00",
        client_id=7,
        user_agent=(
            "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/25.lts.30.1034943-gold "
            "(unlike Gecko), Unknown_TV_Unknown_0/Unknown (Unknown, Unknown)"
        ),
        client_extra={"clientFormFactor": "UNKNOWN_FORM_FACTOR"},
        prefer_cookies=True,
    ),
    InnertubeClient(
        name="MWEB",
        version="2.20260115.01.00",
        client_id=2,
        user_agent=(
            "Mozilla/5.0 (iPad; CPU OS 16_7_10 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/16.6 Mobile/15E148 Safari/604.1,gzip(gfe)"
        ),
        prefer_cookies=True,
    ),
    InnertubeClient(
        name="ANDROID",
        version="21.02.35",
        client_id=3,
        user_agent=(
            "com.google.android.youtube/21.02.35 "
            "(Linux; U; Android 11) gzip"
        ),
        client_extra={"androidSdkVersion": 30, "osName": "Android", "osVersion": "11"},
    ),
    InnertubeClient(
        name="IOS",
        version="21.02.3",
        client_id=5,
        user_agent=(
            "com.google.ios.youtube/21.02.3 "
            "(iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)"
        ),
        client_extra={
            "deviceMake": "Apple",
            "deviceModel": "iPhone16,2",
            "osName": "iPhone",
            "osVersion": "18.3.2.22D82",
        },
    ),
    InnertubeClient(
        name="WEB",
        version="2.20260114.08.00",
        client_id=1,
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
    version = (
        web_client_version
        if client.name == "WEB" and web_client_version
        else client.version
    )
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