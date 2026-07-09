"""
Обработчики /start и /help.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.orchestrator import ArchiveOrchestrator

router = Router(name="commands")

START_TEXT = """
<b>ContentExplorer</b>
Архиватор контента Instagram и TikTok

<b>Возможности</b>
───────────────
Ссылка · мгновенный анализ публикации или профиля
Превью · видео или фото в начале сообщения
Кнопки · Профиль, Звук, Видео полностью, HD загрузка
Статистика · просмотры, лайки, комментарии
Архив · полный JSON вторым сообщением

<b>Instagram</b>
───────────────
Профиль · <code>instagram.com/username</code>
Пост · <code>instagram.com/p/…</code>
Reels · <code>instagram.com/reel/…</code>

<b>TikTok</b>
───────────────
Видео · <code>tiktok.com/@user/video/…</code>
Профиль · <code>tiktok.com/@user</code>
Короткие · <code>vm.tiktok.com/…</code>

Просто отправьте ссылку — команды не нужны.
"""

HELP_TEXT = """
<b>ContentExplorer</b> · Справка

<b>Отчёт</b>
───────────────
Автор · ник, имя, подписчики
<blockquote>био автора — в цитате</blockquote>

Публикация · ID, дата, локация
<blockquote>описание, музыка, комментарии — в цитатах</blockquote>

Статистика · точные числа просмотров и лайков
Видео · технические параметры файла
Медиа · прямые ссылки на скачивание

<b>Формат вывода</b>
───────────────
Сообщение 1 · превью + отчёт
Сообщение 2 · JSON-архив

<b>Настройка (Railway)</b>
───────────────
<code>TELEGRAM_BOT_TOKEN</code>
<code>SESSION_TOKEN</code>
<code>CSRF_TOKEN</code> — обязателен (cookie csrftoken)
<code>TIKTOK_COOKIE</code> — опционально (cookies tiktok.com)
"""


def setup_commands(orchestrator: ArchiveOrchestrator) -> Router:
    @router.message(Command("session"))
    async def cmd_session(message: Message) -> None:
        await orchestrator.fetcher.ensure_session()
        csrf = orchestrator.auth.get_csrf_token()
        lines = [
            "<b>ContentExplorer</b> · Проверка сессии",
            "",
            f"SESSION_TOKEN · {'OK' if orchestrator.auth.session_id else 'нет'}",
            f"CSRF_TOKEN · {'OK' if csrf else 'нет — добавьте в Railway'}",
        ]
        try:
            data = await orchestrator.fetcher._fetch_profile_via_web_api(
                "instagram",
                orchestrator.fetcher._profile_referer("instagram"),
            )
            if data:
                user = data.get("data", {}).get("user", {})
                lines.append(
                    f"Тест API · OK (@{user.get('username', 'instagram')})"
                )
            else:
                lines.append(
                    "Тест API · web_profile_info не ответил — "
                    "проверьте SESSION_TOKEN и CSRF_TOKEN"
                )
        except Exception as exc:
            lines.append(f"Тест API · ошибка: {exc}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    return router


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML", disable_web_page_preview=True)