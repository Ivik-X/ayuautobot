from __future__ import annotations

import logging
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
        )

    return None


def media_flags(message: Message) -> list[str]:
    flags: list[str] = []
    if message.has_protected_content:
        flags.append("🔒 одноразовое/защищённое")
    if message.has_media_spoiler:
        flags.append("🙈 спойлер")
    return flags


async def download_media(bot: Bot, media: MediaRef, message_id: int) -> MediaRef:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = _guess_extension(media)
    destination = MEDIA_DIR / f"{message_id}_{media.kind}{ext}"

    try:
        tg_file = await bot.get_file(media.file_id)
        if tg_file.file_path:
            await bot.download_file(tg_file.file_path, destination=destination)
            media.local_path = destination
    except Exception:
        logger.exception("Не удалось скачать медиа %s", media.kind)

    return media


async def send_media_copy(
    bot: Bot,
    owner_chat_id: int,
    media: MediaRef,
    caption: str | None = None,
) -> None:
    source = FSInputFile(media.local_path) if media.local_path and media.local_path.exists() else media.file_id

    try:
        if media.kind == "photo":
            await bot.send_photo(owner_chat_id, source, caption=caption)
            return
        if media.kind == "video":
            await bot.send_video(owner_chat_id, source, caption=caption)
            return
        if media.kind == "video_note":
            await bot.send_video_note(owner_chat_id, source)
            if caption:
                await bot.send_message(owner_chat_id, caption)
            return
        if media.kind == "voice":
            await bot.send_voice(owner_chat_id, source, caption=caption)
            return
        if media.kind == "audio":
            await bot.send_audio(owner_chat_id, source, caption=caption)
            return
        if media.kind == "animation":
            await bot.send_animation(owner_chat_id, source, caption=caption)
            return
        if media.kind == "sticker":
            await bot.send_sticker(owner_chat_id, source)
            if caption:
                await bot.send_message(owner_chat_id, caption)
            return
        await bot.send_document(owner_chat_id, source, caption=caption)
    except Exception:
        logger.exception("Не удалось переслать медиа владельцу")
        if caption:
            await bot.send_message(owner_chat_id, caption)


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
