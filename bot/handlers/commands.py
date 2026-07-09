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
Архиватор контента Instagram, TikTok и YouTube

<b>Возможности</b>
───────────────
Ссылка · мгновенный анализ публикации или профиля
Превью · видео или фото в начале сообщения
Кнопки · Профиль, Звук, Видео полностью, Исходное видео
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

<b>YouTube</b>
───────────────
Видео · <code>youtube.com/watch?v=…</code>
Shorts · <code>youtube.com/shorts/…</code>
Канал · <code>youtube.com/@channel</code>

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
<code>TIKTOK_SESSION_TOKEN</code> — sessionid с tiktok.com
<code>TIKTOK_CSRF_TOKEN</code> — tt_csrf_token (опционально)
<code>YOUTUBE_SESSION_TOKEN</code> — cookies с youtube.com (SID, SAPISID, …)
"""


def setup_commands(orchestrator: ArchiveOrchestrator) -> Router:
    @router.message(Command("session"))
    async def cmd_session(message: Message) -> None:
        tt_csrf = orchestrator.tiktok_auth.get_csrf_token()
        yt_cookies = orchestrator.youtube_auth.build_cookies()
        lines = [
            "<b>ContentExplorer</b> · Проверка сессии",
            "",
            "<b>Instagram</b>",
        ]
        try:
            ig = await orchestrator.fetcher.verify_instagram_session()
            lines.extend([
                f"SESSION_TOKEN · {'OK' if ig.session_id_ok else 'нет'}",
                f"CSRF_TOKEN · {'OK' if ig.csrf_ok else 'нет — добавьте в Railway'}",
                f"Bootstrap · csrftoken "
                f"{'OK' if ig.csrf_ok else 'MISSING'} ({ig.csrf_source})",
            ])
            if ig.ok:
                lines.append(
                    f"Тест API · OK (@{ig.profile_username}) · {ig.strategy}"
                )
            else:
                lines.append("Тест API · не удалось")
                if not ig.csrf_ok:
                    lines.append(
                        "  · обновите SESSION_TOKEN и CSRF_TOKEN "
                        "из одного браузера (F12 → Cookies → instagram.com)"
                    )
                for err in ig.errors[:4]:
                    lines.append(f"  · {err}")
                if len(ig.errors) > 4:
                    lines.append(f"  · … ещё {len(ig.errors) - 4} ошибок")
        except Exception as exc:
            lines.extend([
                f"SESSION_TOKEN · {'OK' if orchestrator.auth.session_id else 'нет'}",
                f"Тест API · ошибка: {exc}",
            ])
        lines.extend([
            "",
            "<b>TikTok</b>",
            f"TIKTOK_SESSION_TOKEN · {'OK' if orchestrator.tiktok_auth.session_id else 'нет'}",
            f"TIKTOK_CSRF_TOKEN · {'OK' if tt_csrf else 'опционально'}",
            "",
            "<b>YouTube</b>",
            f"YOUTUBE_SESSION_TOKEN · {'OK' if orchestrator.youtube_auth.is_configured() else 'нет'}",
            f"Cookies · {len(yt_cookies)} шт.",
        ])
        await message.answer("\n".join(lines), parse_mode="HTML")

    return router


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML", disable_web_page_preview=True)