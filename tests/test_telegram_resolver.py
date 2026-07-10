"""Тесты разбора ссылок Telegram."""

from core.telegram.resolver import TelegramLinkResolver
from core.models import EntityType
from core.platforms import Platform


def test_channel_profile():
    r = TelegramLinkResolver.resolve("https://t.me/durov")
    assert r is not None
    assert r.platform == Platform.TELEGRAM
    assert r.entity_type == EntityType.PROFILE
    assert r.identifiers["username"] == "durov"


def test_channel_post():
    r = TelegramLinkResolver.resolve("https://t.me/durov/1")
    assert r is not None
    assert r.entity_type == EntityType.PUBLICATION
    assert r.identifiers["message_id"] == "1"


def test_private_post():
    r = TelegramLinkResolver.resolve("https://t.me/c/1234567890/42")
    assert r is not None
    assert r.identifiers["channel_id"] == "1234567890"
    assert r.identifiers["message_id"] == "42"


def test_at_username():
    r = TelegramLinkResolver.resolve("@durov")
    assert r is not None
    assert r.entity_type == EntityType.PROFILE