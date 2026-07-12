from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class SttError(Exception):
    pass


_model = None
_model_lock = asyncio.Lock()
_loading_size: str | None = None


async def _get_model(model_size: str, models_dir: Path):
    global _model, _loading_size

    if _model is not None and _loading_size == model_size:
        return _model

    async with _model_lock:
        if _model is not None and _loading_size == model_size:
            return _model

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SttError(
                "faster-whisper не установлен в окружении (см. requirements.txt)"
            ) from exc

        models_dir.mkdir(parents=True, exist_ok=True)

        def _load():
            # int8 на CPU — компромисс скорость/память для слабого сервера;
            # модель весит существенно меньше, чем float32/float16.
            return WhisperModel(model_size, device="cpu", compute_type="int8", download_root=str(models_dir))

        logger.info("Загружаю локальную модель распознавания речи (%s)…", model_size)
        _model = await asyncio.to_thread(_load)
        _loading_size = model_size
        logger.info("Модель распознавания речи загружена")
        return _model


async def transcribe_local(
    file_bytes: bytes,
    *,
    model_size: str,
    models_dir: Path,
    language: str | None = None,
) -> str:
    """Распознаёт речь локально, без внешних API и без затрат.

    Первый вызов может занять заметное время (загрузка/скачивание модели),
    дальнейшие — быстрее, модель остаётся в памяти процесса.
    """
    model = await _get_model(model_size, models_dir)

    def _run() -> str:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            segments, _info = model.transcribe(tmp.name, language=language, beam_size=1, vad_filter=True)
            return " ".join(segment.text.strip() for segment in segments).strip()

    try:
        text = await asyncio.to_thread(_run)
    except Exception as exc:
        raise SttError(f"ошибка распознавания: {exc}") from exc

    if not text:
        raise SttError("не удалось распознать речь (возможно, тишина или слишком короткая запись)")
    return text
