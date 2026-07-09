from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_solid_png(width: int = 512, height: int = 512, color: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """Генерирует однотонный PNG без внешних зависимостей (без Pillow).

    Используется в команде .view — после истечения таймера картинка
    подменяется на чёрный квадрат перед удалением, чтобы у собеседника
    (например, в AyuGram, который сохраняет последнюю версию удалённых
    медиа) в сохранённой копии осталась просто заливка, а не оригинал.
    """
    r, g, b = color
    raw_row = bytes([0, *([r, g, b] * width)])  # filter type 0 + RGB pixels
    raw = raw_row * height
    compressed = zlib.compress(raw, level=6)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        signature
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
    return png
