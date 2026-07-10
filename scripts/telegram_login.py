#!/usr/bin/env python3
"""
Вход в Telegram (MTProto) → TELEGRAM_SESSION.

my.telegram.org в РФ не нужен: по умолчанию ключи Telegram Desktop.
Скрипт ходит напрямую в Telegram — VPN обычно не требуется.

  python scripts/telegram_login.py

Опционально свои ключи (если создали приложение через VPN/VPS):
  TELEGRAM_API_ID=...
  TELEGRAM_API_HASH=...
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from core.telegram.credentials import resolve_telegram_api_credentials

    api_id, api_hash, source = resolve_telegram_api_credentials()
    if source == "desktop":
        print("Ключи Telegram Desktop (my.telegram.org не нужен)")
    else:
        print("Свои TELEGRAM_API_ID / TELEGRAM_API_HASH")

    client = TelegramClient(StringSession(), api_id, api_hash)
    print("Введите номер и код из приложения Telegram на телефоне…")
    await client.start()
    session_string = client.session.save()
    await client.disconnect()

    print("")
    print("=" * 60)
    print("Скопируйте в .env / Railway Variables:")
    print("")
    print(f"TELEGRAM_SESSION={session_string}")
    print("")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())