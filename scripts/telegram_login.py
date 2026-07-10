#!/usr/bin/env python3
"""
Однократный вход в Telegram (MTProto) → строка TELEGRAM_SESSION.

1. Получите api_id и api_hash на https://my.telegram.org
2. Добавьте в .env:
     TELEGRAM_API_ID=12345678
     TELEGRAM_API_HASH=abcdef...
3. Запустите:
     python scripts/telegram_login.py
4. Скопируйте TELEGRAM_SESSION в .env / Railway Variables
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

    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id_raw.isdigit() or not api_hash:
        print("Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")
        print("Получить: https://my.telegram.org → API development tools")
        sys.exit(1)

    api_id = int(api_id_raw)
    client = TelegramClient(StringSession(), api_id, api_hash)
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