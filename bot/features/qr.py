from __future__ import annotations

import io

import segno


class QrError(Exception):
    pass


def make_qr_png(text: str) -> bytes:
    if not text.strip():
        raise QrError("пустой текст")
    if len(text) > 1000:
        raise QrError("слишком длинный текст для QR-кода")
    qr = segno.make(text, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=6, border=2)
    return buffer.getvalue()
