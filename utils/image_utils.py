"""
Обработка изображений для Telegram (квадрат 1:1).
"""

from __future__ import annotations

import io

from PIL import Image


def to_square_jpeg(
    data: bytes,
    *,
    max_side: int = 1080,
    quality: int = 92,
) -> tuple[bytes, int]:
    """
    Центрированная обрезка до 1:1 и сохранение в JPEG.
    Возвращает (bytes, сторона в пикселях).
    """
    with Image.open(io.BytesIO(data)) as img:
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode == "P":
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        img = img.crop((left, top, left + side, top + side))

        if side > max_side:
            img = img.resize((max_side, max_side), Image.Resampling.LANCZOS)
            side = max_side

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue(), side