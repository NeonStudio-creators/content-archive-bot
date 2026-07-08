"""
Обработчики /start и /help — стиль @reTikTok_bot.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="commands")

START_TEXT = """
⚡ <b>re:Instagram</b> Checker

<blockquote><b>❤️ Main features</b>
🧲 Send an Instagram link — instant analysis
🧲 Video preview at the top of the message
🧲 Author &amp; Video buttons below the media
🧲 Exact statistics: views, likes, comments
🧲 Quality data: resolution, FPS, bitrate, codec
🧲 Full JSON dump as a second message</blockquote>

<blockquote><b>❤️ Supported links</b>
🧲 Profile — <code>instagram.com/username</code>
👻🧲 Post — <code>instagram.com/p/…</code>
👻🧲 Reel — <code>instagram.com/reel/…</code>
👻🧲 Story — <code>instagram.com/stories/user/id</code>
👻🧲 Highlight — <code>instagram.com/stories/highlights/id</code></blockquote>

🧲 Just send a link — no commands needed
"""

HELP_TEXT = """
⚡ <b>re:Instagram</b> Checker · Help

<blockquote><b>❤️ Checker</b>
🧲 <b>Author</b> — username, name, followers
🧲 <b>Video</b> — ID, description, date
🧲 <b>Statistics</b> — exact view/like/comment counts
🧲 <b>Quality</b> — resolution, FPS, bitrate, codec
🧲 <b>Activity</b> — comments and likers
🧲 <b>Media</b> — direct download links</blockquote>

<blockquote><b>❤️ Output</b>
🧲 Message 1 — video/photo + checker report
🧲 Message 2 — full JSON archive</blockquote>

<blockquote><b>❤️ Admin setup</b>
🧲 <code>TELEGRAM_BOT_TOKEN</code>
🧲 <code>SESSION_TOKEN</code> (cookie sessionid)
🧲 <code>CSRF_TOKEN</code> (optional)</blockquote>

⚡ 🧲 <b>re:Instagram</b>
"""


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML", disable_web_page_preview=True)