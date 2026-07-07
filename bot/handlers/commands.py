"""
Обработчики команд /start и /help.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="commands")

START_TEXT = """
👋 <b>ContentExplorer</b> — ваш помощник по архивации цифрового контента.

Отправьте ссылку на объект визуальной экосистемы:
• Профиль — <code>instagram.com/username</code>
• Публикация — <code>instagram.com/p/…</code> или <code>/reel/…</code>
• История — <code>instagram.com/stories/username/id</code>
• Хайлайт — <code>instagram.com/stories/highlights/id</code>
• Коллекция — <code>instagram.com/user/saved/id</code>

Бот извлечёт метаданные, медиа, связи и активность через внутренние GraphQL-запросы.

<b>Команды:</b>
/help — справка
/start — это сообщение
"""

HELP_TEXT = """
📚 <b>Справка ContentExplorer</b>

<b>Как использовать:</b>
Просто отправьте ссылку в любом сообщении — бот автоматически её обработает.

<b>Что собирается:</b>
• Полные метаданные сущности
• Медиа-файлы с прямыми URL
• Связи (теги, связанные профили, ко-авторы)
• Активность (комментарии и др.)
• JSON-дамп полного архива

<b>Режим работы:</b>
«Тихий» — минимум запросов, задержки между вызовами, автоматические ретраи при лимитах.

<b>Настройка (для администратора):</b>
<code>SESSION_TOKEN</code> — cookie sessionid
<code>TELEGRAM_BOT_TOKEN</code> — токен бота
<code>CSRF_TOKEN</code> — опционально, csrftoken

<b>Ограничения:</b>
• Приватный контент доступен только при авторизованной сессии
• Telegram может не показать превью некоторых медиа — ссылки всегда в ответе
"""


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML", disable_web_page_preview=True)