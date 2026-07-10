"""
Обработчики /start и /help.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import deploy_label, secrets_hint
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

<b>Настройка</b>
───────────────
Среда · {deploy}
Секреты · {secrets}
<code>TELEGRAM_BOT_TOKEN</code>
<code>SESSION_TOKEN</code>
<code>CSRF_TOKEN</code> — опционально (csrftoken обновляется автоматически)
<code>TIKTOK_SESSION_TOKEN</code> — sessionid с tiktok.com
<code>TIKTOK_CSRF_TOKEN</code> — tt_csrf_token (опционально)
<code>YOUTUBE_SESSION_TOKEN</code> — cookies с youtube.com (обновляются автоматически)
Локально · <code>scripts/local/install.ps1</code> / <code>run.ps1</code>

<b>Stats API</b>
───────────────
<code>GET /api/v1/stats?url=…</code>
<code>GET /api/v1/instagram/stats?url=…</code>
<code>GET /api/v1/tiktok/stats?url=…</code>
<code>GET /api/v1/youtube/stats?url=…</code>
<code>GET /api/v1/telegram/stats?url=…</code>
Порт · <code>{api_port}</code> (переменная API_PORT)
Авторизация · <code>Authorization: Bearer &lt;STATS_API_TOKEN&gt;</code>
"""

def _help_text(settings_api_port: int = 8080) -> str:
    return HELP_TEXT.format(
        deploy=deploy_label(),
        secrets=secrets_hint(),
        api_port=settings_api_port,
    )


def setup_commands(orchestrator: ArchiveOrchestrator) -> Router:
    @router.message(Command("session"))
    async def cmd_session(message: Message) -> None:
        tt_csrf = orchestrator.tiktok_auth.get_csrf_token()
        yt_cookies = orchestrator.youtube_auth.build_cookies()
        lines = [
            "<b>ContentExplorer</b> · Проверка сессии",
            f"Среда · {deploy_label()}",
            "",
            "<b>Instagram</b>",
        ]
        try:
            ig = await orchestrator.fetcher.verify_instagram_session()
            interval = orchestrator.settings.token_refresh_interval_sec
            refresh_note = (
                f"каждые {int(interval)} с"
                if interval > 0
                else "фон отключён"
            )
            lines.extend([
                f"SESSION_TOKEN · {'OK' if ig.session_id_ok else 'нет'}",
                f"CSRF · {'OK' if ig.csrf_ok else 'нет — ждём bootstrap'}",
                f"Источник CSRF · {ig.csrf_source}",
                f"Автообновление · {refresh_note}",
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
        ])
        yt_diag = orchestrator.youtube_auth.cookie_diagnostic()
        lines.extend([
            f"YOUTUBE_SESSION_TOKEN · {'OK' if yt_diag['ok'] else 'нет'}",
            f"Источник cookies · {orchestrator.youtube_auth.session_source_label()}",
            f"Env · {yt_diag['env_len']} симв., ключи: {', '.join(yt_diag['env_keys']) or '—'}",
            f"Всего cookies · {len(yt_cookies)} ({', '.join(yt_diag['all_keys'])})",
        ])
        if yt_diag["missing"]:
            lines.append(f"Не хватает · {', '.join(yt_diag['missing'])}")
            if yt_diag["env_len"] == 0:
                lines.append(
                    f"  · переменная пуста — проверьте {secrets_hint()}"
                )
            elif yt_diag["env_len"] < 80 and not yt_diag["env_keys"]:
                lines.append(
                    "  · многострочное значение могло обрезаться — "
                    "используйте одну строку через «;» или YOUTUBE_SID, YOUTUBE_SAPISID…"
                )
        try:
            yt = await orchestrator.youtube_fetcher.verify_session()
            interval = orchestrator.settings.token_refresh_interval_sec
            refresh_note = (
                f"каждые {int(interval)} с"
                if interval > 0
                else "фон отключён"
            )
            lines.append(f"Автообновление · {refresh_note}")
            if yt.ok:
                lines.append(
                    f"Тест API · OK ({yt.test_streams} потоков, {yt.client})"
                )
            else:
                lines.append("Тест API · не удалось")
                if not yt.configured:
                    lines.append(
                        "  · задайте YOUTUBE_SESSION_TOKEN (SID, SAPISID, "
                        "__Secure-1PSID, __Secure-1PAPISID)"
                    )
                elif not yt.visitor_ok:
                    lines.append("  · нет visitor_id — ждём bootstrap")
                for err in yt.errors[:4]:
                    lines.append(f"  · {err}")
        except Exception as exc:
            lines.append(f"Тест API · ошибка: {exc}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    return router


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    from config import Settings

    port = Settings.from_env().api_port
    await message.answer(
        _help_text(port), parse_mode="HTML", disable_web_page_preview=True
    )