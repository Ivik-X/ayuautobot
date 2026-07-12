from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, Message

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).resolve().parent.parent / "data" / "media"
DB_MEDIA_DIR = Path(__file__).resolve().parent.parent / "data" / "db_media"


@dataclass(slots=True)
class MediaRef:
    kind: str
    file_id: str
    local_path: Path | None = None
    mime_type: str | None = None
    file_name: str | None = None
    is_animated: bool = False
    is_video: bool = False


def extract_media(message: Message) -> MediaRef | None:
    if message.photo:
        photo = message.photo[-1]
        return MediaRef(kind="photo", file_id=photo.file_id, mime_type="image/jpeg")

    if message.video:
        return MediaRef(
            kind="video",
            file_id=message.video.file_id,
            mime_type=message.video.mime_type,
            file_name=message.video.file_name,
        )

    if message.video_note:
        return MediaRef(kind="video_note", file_id=message.video_note.file_id, mime_type="video/mp4")

    if message.voice:
        return MediaRef(kind="voice", file_id=message.voice.file_id, mime_type=message.voice.mime_type)

    if message.audio:
        return MediaRef(
            kind="audio",
            file_id=message.audio.file_id,
            mime_type=message.audio.mime_type,
            file_name=message.audio.file_name or message.audio.title,
        )

    if message.document:
        return MediaRef(
            kind="document",
            file_id=message.document.file_id,
            mime_type=message.document.mime_type,
            file_name=message.document.file_name,
        )

    if message.animation:
        return MediaRef(
            kind="animation",
            file_id=message.animation.file_id,
            mime_type=message.animation.mime_type,
            file_name=message.animation.file_name,
        )

    if message.sticker:
        return MediaRef(
            kind="sticker",
            file_id=message.sticker.file_id,
            mime_type="image/webp",
            file_name=message.sticker.emoji,
            is_animated=message.sticker.is_animated,
            is_video=message.sticker.is_video,
        )

    return None


def media_flags(message: Message) -> list[str]:
    flags: list[str] = []
    if message.has_protected_content:
        flags.append("🔒 одноразовое/защищённое")
    if message.has_media_spoiler:
        flags.append("🙈 спойлер")
    return flags


def _owner_dir(base: Path, connection_id: str) -> Path:
    # разложено по connection_id, чтобы не смешивать медиа разных владельцев
    safe = "".join(c for c in connection_id if c.isalnum())[:40] or "common"
    return base / safe


async def download_media(bot: Bot, media: MediaRef, message_id: int, connection_id: str) -> MediaRef:
    directory = _owner_dir(MEDIA_DIR, connection_id)
    directory.mkdir(parents=True, exist_ok=True)
    ext = _guess_extension(media)
    destination = directory / f"{message_id}_{media.kind}{ext}"

    for attempt in range(2):
        try:
            tg_file = await bot.get_file(media.file_id)
            if not tg_file.file_path:
                # Telegram ответил без ошибки, но без file_path — типично для
                # одноразовых/самоуничтожающихся медиа, которые сервер не отдаёт
                # ботам. Раньше это молча считалось "успехом" без скачивания —
                # теперь считаем это ошибкой, логируем и пробуем ещё раз.
                raise RuntimeError(f"get_file вернул пустой file_path для {media.kind} (message_id={message_id})")
            await bot.download_file(tg_file.file_path, destination=destination)
            media.local_path = destination
            return media
        except Exception as exc:
            if attempt == 0:
                # Иногда файл отдаётся не с первой попытки — пробуем ещё раз почти
                # сразу, пока он не "протух" на серверах Telegram.
                await asyncio.sleep(0.3)
                continue
            logger.warning(
                "Не удалось скачать медиа %s (message_id=%s, connection=%s): %s: %s",
                media.kind, message_id, connection_id, type(exc).__name__, exc,
            )

    return media


async def download_bytes(bot: Bot, file_id: str) -> bytes | None:
    """Скачивает файл в память (без записи на диск) — для антиспойлера/анонимных стикеров."""
    try:
        tg_file = await bot.get_file(file_id)
        if not tg_file.file_path:
            return None
        buffer = await bot.download_file(tg_file.file_path)
        return buffer.read() if buffer else None
    except Exception:
        logger.exception("Не удалось скачать файл %s в память", file_id)
        return None


async def send_media_copy(
    bot: Bot,
    owner_chat_id: int,
    media: MediaRef,
    caption: str | None = None,
    *,
    disable_notification: bool = False,
) -> None:
    source = FSInputFile(media.local_path) if media.local_path and media.local_path.exists() else media.file_id

    try:
        if media.kind == "photo":
            await bot.send_photo(owner_chat_id, source, caption=caption, disable_notification=disable_notification)
            return
        if media.kind == "video":
            await bot.send_video(owner_chat_id, source, caption=caption, disable_notification=disable_notification)
            return
        if media.kind == "video_note":
            await bot.send_video_note(owner_chat_id, source, disable_notification=disable_notification)
            if caption:
                await bot.send_message(owner_chat_id, caption, disable_notification=disable_notification)
            return
        if media.kind == "voice":
            await bot.send_voice(owner_chat_id, source, caption=caption, disable_notification=disable_notification)
            return
        if media.kind == "audio":
            await bot.send_audio(owner_chat_id, source, caption=caption, disable_notification=disable_notification)
            return
        if media.kind == "animation":
            await bot.send_animation(owner_chat_id, source, caption=caption, disable_notification=disable_notification)
            return
        if media.kind == "sticker":
            await bot.send_sticker(owner_chat_id, source, disable_notification=disable_notification)
            if caption:
                await bot.send_message(owner_chat_id, caption, disable_notification=disable_notification)
            return
        await bot.send_document(owner_chat_id, source, caption=caption, disable_notification=disable_notification)
    except Exception:
        logger.exception("Не удалось переслать медиа владельцу")
        if caption:
            await bot.send_message(owner_chat_id, caption, disable_notification=disable_notification)


def _guess_extension(media: MediaRef) -> str:
    if media.file_name and "." in media.file_name:
        return "." + media.file_name.rsplit(".", 1)[-1].lower()

    mapping = {
        "photo": ".jpg",
        "video": ".mp4",
        "video_note": ".mp4",
        "voice": ".ogg",
        "audio": ".mp3",
        "animation": ".mp4",
        "sticker": ".webp",
    }
    return mapping.get(media.kind, ".bin")


def unlink_media(media: MediaRef | None) -> None:
    if media is None or media.local_path is None:
        return
    try:
        if DB_MEDIA_DIR in media.local_path.parents:
            return
        if media.local_path.exists():
            media.local_path.unlink()
    except OSError:
        logger.debug("Не удалось удалить файл %s", media.local_path, exc_info=True)


def directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def enforce_media_quota(base_dir: Path, max_total_mb: int) -> int:
    """Удаляет самые старые файлы, пока суммарный размер base_dir не впишется в лимит.

    Возвращает количество удалённых файлов. Защищает 10 GB NVMe от переполнения.
    """
    if max_total_mb <= 0 or not base_dir.exists():
        return 0

    max_bytes = max_total_mb * 1024 * 1024
    entries: list[tuple[float, Path, int]] = []
    total = 0
    for root, _dirs, files in os.walk(base_dir):
        for name in files:
            if name == ".gitkeep":
                continue
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, fp, st.st_size))
            total += st.st_size

    if total <= max_bytes:
        return 0

    entries.sort(key=lambda item: item[0])  # старые первыми
    removed = 0
    for _mtime, fp, size in entries:
        if total <= max_bytes:
            break
        try:
            fp.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


async def send_media_to_chat(
    bot: Bot,
    connection_id: str,
    chat_id: int,
    media: "MediaRef",
    caption: str | None = None,
) -> None:
    """Отправляет медиа в чат от имени бизнес-подключения (для .spam с реплаем на медиа/стикер)."""
    source = FSInputFile(media.local_path) if media.local_path and media.local_path.exists() else media.file_id
    kwargs = {"business_connection_id": connection_id}

    if media.kind == "photo":
        await bot.send_photo(chat_id, source, caption=caption, **kwargs)
    elif media.kind == "video":
        await bot.send_video(chat_id, source, caption=caption, **kwargs)
    elif media.kind == "video_note":
        await bot.send_video_note(chat_id, source, **kwargs)
    elif media.kind == "voice":
        await bot.send_voice(chat_id, source, caption=caption, **kwargs)
    elif media.kind == "audio":
        await bot.send_audio(chat_id, source, caption=caption, **kwargs)
    elif media.kind == "animation":
        await bot.send_animation(chat_id, source, caption=caption, **kwargs)
    elif media.kind == "sticker":
        await bot.send_sticker(chat_id, source, **kwargs)
    else:
        await bot.send_document(chat_id, source, caption=caption, **kwargs)
