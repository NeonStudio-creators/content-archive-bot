"""
Нормализация ответов REST/GraphQL API в единый формат shortcode_media.
"""

from __future__ import annotations

from typing import Any


def _best_image_from_item(item: dict[str, Any]) -> str | None:
    candidates = item.get("image_versions2", {}).get("candidates", [])
    if candidates:
        best = max(candidates, key=lambda c: c.get("width", 0) * c.get("height", 0))
        return best.get("url")
    return item.get("display_url") or item.get("thumbnail_src")


def _rest_item_to_node(item: dict[str, Any], shortcode: str | None = None) -> dict[str, Any]:
    """REST /media/info/ item → узел shortcode_media."""
    caption = item.get("caption") or {}
    user = item.get("user") or {}
    code = shortcode or item.get("code") or ""

    node: dict[str, Any] = {
        "id": str(item.get("pk", item.get("id", ""))),
        "shortcode": code,
        "taken_at_timestamp": item.get("taken_at"),
        "is_video": item.get("media_type") == 2,
        "media_type": "GraphVideo" if item.get("media_type") == 2 else "GraphImage",
        "video_url": (item.get("video_versions") or [{}])[0].get("url"),
        "video_duration": item.get("video_duration"),
        "video_view_count": item.get("view_count") or item.get("play_count"),
        "display_url": _best_image_from_item(item),
        "thumbnail_src": _best_image_from_item(item),
        "edge_media_to_caption": {
            "edges": [{"node": {"text": caption.get("text", "")}}]
        }
        if caption.get("text")
        else {"edges": []},
        "edge_liked_by": {"count": item.get("like_count", 0)},
        "edge_media_preview_like": {"count": item.get("like_count", 0)},
        "edge_media_to_comment": {"count": item.get("comment_count", 0)},
        "owner": {
            "id": str(user.get("pk", user.get("id", ""))),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
        },
        "product_type": item.get("product_type"),
        "location": item.get("location"),
        "video_versions": item.get("video_versions"),
        "video_dash_manifest": item.get("video_dash_manifest"),
        "has_audio": item.get("has_audio"),
        "video_codec": item.get("video_codec"),
        "audio_codec": item.get("audio_codec"),
        "is_dash_eligible": item.get("is_dash_eligible"),
        "number_of_qualities": item.get("number_of_qualities"),
        "clips_metadata": item.get("clips_metadata"),
        "video_subtitles_uri": item.get("video_subtitles_uri"),
        "accessibility_caption": item.get("accessibility_caption"),
        "original_width": item.get("original_width"),
        "original_height": item.get("original_height"),
    }

    dimensions = {}
    if item.get("original_width"):
        dimensions["width"] = item["original_width"]
    if item.get("original_height"):
        dimensions["height"] = item["original_height"]
    if dimensions:
        node["dimensions"] = dimensions

    carousel = item.get("carousel_media")
    if carousel:
        node["edge_sidecar_to_children"] = {
            "edges": [
                {"node": _rest_item_to_node(child)}
                for child in carousel
            ]
        }

    return node


def wrap_shortcode_media(node: dict[str, Any]) -> dict[str, Any]:
    """Оборачивает узел в стандартный GraphQL-ответ."""
    return {"data": {"shortcode_media": node}}


def from_rest_media_info(payload: dict[str, Any], shortcode: str) -> dict[str, Any] | None:
    items = payload.get("items") or []
    if not items:
        return None
    return wrap_shortcode_media(_rest_item_to_node(items[0], shortcode))


def from_graphql_polaris(payload: dict[str, Any], shortcode: str) -> dict[str, Any] | None:
    """Парсит xig_polaris_media из нового GraphQL."""
    media = payload.get("data", {}).get("xig_polaris_media") or {}
    product = (
        media.get("if_not_gated_logged_out")
        or media.get("xdt_shortcode_media")
        or media
    )
    if not product:
        return None

    # Уже в GraphQL-формате
    if product.get("shortcode") or product.get("__typename"):
        if not product.get("shortcode"):
            product["shortcode"] = shortcode
        return wrap_shortcode_media(product)

    return from_rest_media_info({"items": [product]}, shortcode)


def from_embedded_json(data: dict[str, Any], shortcode: str) -> dict[str, Any] | None:
    """Извлекает media из вложенного JSON страницы."""
    candidates = [
        data.get("shortcode_media"),
        data.get("xdt_shortcode_media"),
        (data.get("data") or {}).get("shortcode_media"),
        (data.get("data") or {}).get("xdt_shortcode_media"),
        ((data.get("graphql") or {}).get("shortcode_media")),
    ]
    for node in candidates:
        if isinstance(node, dict) and node.get("id"):
            if not node.get("shortcode"):
                node["shortcode"] = shortcode
            return wrap_shortcode_media(node)

    # REST-подобный item внутри JSON
    if data.get("pk"):
        return wrap_shortcode_media(_rest_item_to_node(data, shortcode))

    return None