"""
Безопасная HTML-разметка для Telegram (parse_mode=HTML).
"""

from __future__ import annotations

import html
import re

# Telegram parse_mode=HTML поддерживает ограниченный набор тегов
_ALLOWED_TAGS = frozenset({"b", "i", "code", "a", "blockquote"})

_TAG_OPEN_RE = re.compile(
    r"<(blockquote|[bi]|code|a)\b([^>]*)>",
    re.IGNORECASE,
)
_TAG_CLOSE_RE = re.compile(r"</(blockquote|[bi]|code|a)>", re.IGNORECASE)


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def href(url: str | None) -> str:
    """Экранирует URL для атрибута href."""
    if not url:
        return ""
    return html.escape(url, quote=True)


def truncate_html(text: str, max_len: int, *, note: str = "") -> str:
    """
    Обрезает HTML без «рваных» тегов.
    Закрывает все незакрытые теги в конце.
    """
    suffix = note or "<i>…в JSON ↓</i>"
    if len(text) <= max_len:
        return text

    budget = max_len - len(suffix)
    if budget < 64:
        return esc(text[: max_len - 1]) + "…"

    # Ищем безопасную точку обрезки (после закрытого тега или перевода строки)
    cut = budget
    safe_marks = []
    for m in _TAG_CLOSE_RE.finditer(text):
        if m.end() <= budget:
            safe_marks.append(m.end())
    for i, ch in enumerate(text[:budget]):
        if ch == "\n":
            safe_marks.append(i + 1)

    if safe_marks:
        cut = max(safe_marks)
    else:
        cut = budget

    chunk = text[:cut].rstrip()

    # Стек открытых тегов
    stack: list[str] = []
    pos = 0
    while pos < len(chunk):
        close_m = _TAG_CLOSE_RE.match(chunk, pos)
        if close_m:
            tag = close_m.group(1).lower()
            if stack and stack[-1] == tag:
                stack.pop()
            pos = close_m.end()
            continue

        open_m = _TAG_OPEN_RE.match(chunk, pos)
        if open_m:
            tag = open_m.group(1).lower()
            if tag in _ALLOWED_TAGS:
                stack.append(tag)
            pos = open_m.end()
            continue

        pos += 1

    closing = "".join(f"</{t}>" for t in reversed(stack))
    result = chunk + closing + suffix

    if len(result) > max_len:
        # Жёсткий fallback — plain text без тегов
        plain = re.sub(r"<[^>]*>", "", text)
        plain = html.unescape(plain)
        if len(plain) > max_len:
            plain = plain[: max_len - len(suffix) - 1] + "…"
        return esc(plain) + suffix

    return result


def strip_to_plain(text: str) -> str:
    """Убирает HTML-теги для fallback-сообщения."""
    plain = re.sub(r"<[^>]*>", "", text)
    return html.unescape(plain)