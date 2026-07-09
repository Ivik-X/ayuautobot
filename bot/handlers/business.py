from __future__ import annotations

import asyncio
import html
import logging

import aiohttp
from aiogram import Bot, Router
from aiogram.enums import ChatAction
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    BusinessMessagesDeleted,
    InputMediaPhoto,
    Message,
)

from bot.commands import (
    MuteCommand,
    QrCommand,
    SayCommand,
    ShortCommand,
    SimpleCommand,
    SpamCommand,
    TextTransformCommand,
    TranslateCommand,
    TypingCommand,
    UnmuteCommand,
    ViewCommand,
    parse_command,
)
from bot.features.antisearch import antisearch_transform
from bot.features.blackout import make_solid_png
from bot.features.qr import QrError, make_qr_png
from bot.features.shorten import ShortenError, shorten
from bot.features.translate import TranslateError, translate
from bot.fun import mock_text, reverse_text
from bot.media import (
    MediaRef,
    download_bytes,
    download_media,
    extract_media,
    send_media_copy,
    send_media_to_chat,
)
from bot.settings import COMMAND_FLAG
from bot.storage import Storage, describe_message

logger = logging.getLogger(__name__)

router = Router(name="business")


@router.business_connection()
async def on_business_connection(connection: BusinessConnection, storage: Storage) -> None:
    storage.set_connection(connection)
    status = "подключён" if connection.is_enabled else "отключён"
    logger.info("Business connection %s: %s (owner=%s)", connection.id, status, connection.user.id)


@router.business_message()
async def on_business_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    http_session: aiohttp.ClientSession,
) -> None:
    connection_id = message.business_connection_id
    if not connection_id:
        return

    connection = await _ensure_connection(bot, storage, connection_id)
    if connection is None or not connection.is_enabled:
        return

    if storage.is_bot_message(message):
        return

    if storage.is_owner_message(connection_id, message):
        await _handle_owner_message(message, bot, storage, connection_id, http_session)
        return

    if storage.is_partner_message(connection_id, message):
        await _cache_message(message, bot, storage, connection_id)
        await _apply_mute(message, bot, storage, connection_id)
        await _maybe_afk_reply(message, bot, storage, connection_id)
        await _maybe_antispoiler(message, bot, storage, connection_id)


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot, storage: Storage) -> None:
    connection_id = message.business_connection_id
    if not connection_id:
        return

    connection = await _ensure_connection(bot, storage, connection_id)
    if connection is None:
        return

    settings = storage.get_settings_for_connection(connection_id)
    owner_id = storage.owner_user_id(connection_id)

    old = storage.find_cached(connection_id, message.chat.id, message.message_id)
    await _cache_message(message, bot, storage, connection_id)

    if settings.notify_edit_mode == "off" or owner_id is None:
        return

    fresh = storage.find_cached(connection_id, message.chat.id, message.message_id)
    new_media = fresh.media if fresh else None
    old_media = old.media if old else None

    partner = message.from_user.full_name if message.from_user else "Собеседник"
    chat_title = _chat_title(message)
    old_text = old.content if old else "— (не сохранено)"
    new_text = describe_message(message)
    flags = _flags_text(message, old)

    caption = (
        f"✏️ <b>Сообщение отредактировано</b>\n"
        f"Чат: <b>{html.escape(chat_title)}</b>\n"
        f"От: <b>{html.escape(partner)}</b>{flags}\n\n"
        f"<b>Было:</b>\n{html.escape(old_text)}\n\n"
        f"<b>Стало:</b>\n{html.escape(new_text)}"
    )

    media_changed = (
        old_media is not None
        and new_media is not None
        and old_media.file_id != new_media.file_id
    )

    await _dispatch_notification(
        bot, storage, connection_id, owner_id, settings.notify_edit_mode, "edit",
        caption=caption,
        media=new_media if new_media is not None else old_media,
        extra_before_media=old_media if media_changed else None,
    )


@router.deleted_business_messages()
async def on_deleted_business_messages(event: BusinessMessagesDeleted, bot: Bot, storage: Storage) -> None:
    connection_id = event.business_connection_id
    chat = event.chat
    chat_title = chat.full_name or chat.username or str(chat.id)

    await _ensure_connection(bot, storage, connection_id)
    settings = storage.get_settings_for_connection(connection_id)
    owner_id = storage.owner_user_id(connection_id)
    if owner_id is None or settings.notify_delete_mode == "off":
        for message_id in event.message_ids:
            storage.remove_cached(connection_id, chat.id, message_id)
            storage.was_bot_deleted(connection_id, chat.id, message_id)
        return

    for message_id in event.message_ids:
        bot_caused = storage.was_bot_deleted(connection_id, chat.id, message_id)
        cached = storage.remove_cached(connection_id, chat.id, message_id)

        if bot_caused and not settings.notify_own_deletions:
            continue

        sender = cached.from_user_name if cached else (chat.full_name or chat.username or "собеседник")
        body = cached.content if cached else "— (сообщение не было получено ботом, пока он был запущен)"
        flags = ""
        if cached and cached.flags:
            flags = "\n" + " ".join(cached.flags)
        origin_note = "\n<i>(удалено ботом)</i>" if bot_caused else ""

        caption = (
            f"🗑 <b>Сообщение удалено</b>\n"
            f"Чат: <b>{html.escape(chat_title)}</b>\n"
            f"От: <b>{html.escape(sender)}</b>\n"
            f"ID: <code>{message_id}</code>{flags}{origin_note}\n\n"
            f"<b>Содержимое:</b>\n{html.escape(body)}"
        )

        media = cached.media if cached else None
        await _dispatch_notification(
            bot, storage, connection_id, owner_id, settings.notify_delete_mode, "delete",
            caption=caption, media=media,
        )


# ---------------------------------------------------------------- infrastructure
async def _ensure_connection(bot: Bot, storage: Storage, connection_id: str) -> BusinessConnection | None:
    connection = storage.get_connection(connection_id)
    if connection is not None:
        return connection
    try:
        connection = await bot.get_business_connection(connection_id)
        storage.set_connection(connection)
        return connection
    except Exception:
        logger.exception("Не удалось получить business connection %s", connection_id)
        return None


async def _cache_message(message: Message, bot: Bot, storage: Storage, connection_id: str, *, bot_caused: bool = False) -> None:
    settings = storage.get_settings_for_connection(connection_id)
    media = extract_media(message)
    if media is not None:
        protected = bool(message.has_protected_content)
        if not protected or settings.save_protected_media:
            media = await download_media(bot, media, message.message_id, connection_id)
    storage.cache_message(connection_id, message, media=media, bot_caused=bot_caused)


async def _apply_mute(message: Message, bot: Bot, storage: Storage, connection_id: str) -> None:
    if not storage.mute_active(connection_id, message.chat.id):
        return
    try:
        storage.mark_bot_deleted(connection_id, message.chat.id, message.message_id)
        await bot.delete_business_messages(
            business_connection_id=connection_id, message_ids=[message.message_id]
        )
        storage.register_mute_deletion(connection_id, message.chat.id)
    except Exception:
        logger.exception("Не удалось удалить сообщение в mute-режиме")


async def _maybe_afk_reply(message: Message, bot: Bot, storage: Storage, connection_id: str) -> None:
    settings = storage.get_settings_for_connection(connection_id)
    if not settings.afk_enabled:
        return
    if not storage.should_send_afk_reply(connection_id, message.chat.id):
        return
    try:
        await bot.send_message(
            chat_id=message.chat.id, text=settings.afk_text, business_connection_id=connection_id
        )
    except Exception:
        logger.exception("Не удалось отправить AFK-автоответ")


async def _maybe_antispoiler(message: Message, bot: Bot, storage: Storage, connection_id: str) -> None:
    settings = storage.get_settings_for_connection(connection_id)
    if not settings.anti_spoiler or not message.has_media_spoiler:
        return
    media = extract_media(message)
    if media is None:
        return
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        return
    if media.local_path is None:
        media = await download_media(bot, media, message.message_id, connection_id)
    await send_media_copy(bot, owner_chat_id, media, caption="🙈 Спойлер снят (анти-спойлер)")


# ----------------------------------------------------------------- notifications
async def _dispatch_notification(
    bot: Bot,
    storage: Storage,
    connection_id: str,
    owner_id: int,
    mode: str,
    kind: str,
    *,
    caption: str,
    media: MediaRef | None,
    extra_before_media: MediaRef | None = None,
) -> None:
    if mode == "off":
        return

    if mode == "digest":
        storage.queue_add(owner_id, kind, caption, media=media)
        return

    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        return

    silent = mode == "silent"

    if extra_before_media is not None:
        try:
            await send_media_copy(bot, owner_chat_id, extra_before_media, caption="📎 Было:", disable_notification=silent)
        except Exception:
            logger.exception("Не удалось отправить старую версию медиа")

    if media is not None:
        await send_media_copy(bot, owner_chat_id, media, caption=caption, disable_notification=silent)
    else:
        try:
            await bot.send_message(chat_id=owner_chat_id, text=caption, disable_notification=silent)
        except Exception:
            logger.exception("Не удалось отправить уведомление владельцу")


async def _notify_owner(bot: Bot, storage: Storage, connection_id: str, text: str) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        logger.warning("Нет owner_chat_id для connection %s", connection_id)
        return
    try:
        await bot.send_message(chat_id=owner_chat_id, text=text)
    except Exception:
        logger.exception("Не удалось отправить уведомление владельцу")


# --------------------------------------------------------------- owner commands
_COMMAND_NAMES = {
    SpamCommand: "spam",
    MuteCommand: "mute",
    UnmuteCommand: "unmute",
    TypingCommand: "typing",
    TranslateCommand: "tr",
    QrCommand: "qr",
    ShortCommand: "short",
    SayCommand: "say",
    ViewCommand: "view",
}


def _command_name(command) -> str | None:
    if isinstance(command, TextTransformCommand):
        return command.mode
    if isinstance(command, SimpleCommand):
        return command.name
    return _COMMAND_NAMES.get(type(command))


async def _handle_owner_message(
    message: Message, bot: Bot, storage: Storage, connection_id: str, http_session: aiohttp.ClientSession
) -> None:
    text_source = message.text or message.caption
    command = parse_command(text_source)
    settings = storage.get_settings_for_connection(connection_id)
    owner_id = storage.owner_user_id(connection_id)
    chat_id = message.chat.id

    if isinstance(command, ViewCommand):
        if not message.photo:
            await _notify_owner(bot, storage, connection_id, "❌ .view работает только при отправке фото с этой подписью.")
            return
        await _handle_view(message, bot, storage, connection_id, command)
        return

    # кэшируем собственное сообщение владельца (для симметрии edit/delete и полной истории)
    await _cache_message(message, bot, storage, connection_id)

    if command is None:
        if settings.anti_search and message.text and not message.text.startswith("."):
            await _apply_antisearch(message, bot, connection_id)
        elif settings.anon_stickers and message.sticker and not (message.sticker.is_animated or message.sticker.is_video):
            await _anonymize_sticker(message, bot, storage, connection_id)
        return

    name = _command_name(command)
    flag = COMMAND_FLAG.get(name) if name else None
    if flag and not getattr(settings, flag, True):
        return  # команда выключена владельцем в /settings

    storage.mark_bot_deleted(connection_id, chat_id, message.message_id)
    try:
        await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
    except Exception:
        logger.debug("Не удалось удалить команду из чата", exc_info=True)

    await _dispatch(command, message, bot, storage, connection_id, chat_id, settings, owner_id, http_session)


async def _dispatch(
    command, message: Message, bot: Bot, storage: Storage, connection_id: str, chat_id: int,
    settings, owner_id: int | None, http_session: aiohttp.ClientSession,
) -> None:
    if isinstance(command, SpamCommand):
        await _run_spam(command, message, bot, storage, connection_id, chat_id)
        return

    if isinstance(command, MuteCommand):
        seconds = command.seconds if command.seconds is not None else settings.mute_default_seconds
        storage.start_mute(connection_id, chat_id, seconds=seconds)
        await _notify_owner(bot, storage, connection_id, f"🔇 Mute включён на <b>{seconds}</b> сек.")
        return

    if isinstance(command, UnmuteCommand):
        storage.stop_mute(connection_id, chat_id)
        await _notify_owner(bot, storage, connection_id, "🔊 Mute выключен")
        return

    if isinstance(command, TypingCommand):
        await _run_typing(bot, connection_id, chat_id, command.seconds)
        return

    if isinstance(command, TextTransformCommand):
        text = mock_text(command.text) if command.mode == "mock" else reverse_text(command.text)
        await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)
        return

    if isinstance(command, TranslateCommand):
        try:
            result = await translate(http_session, command.text, command.lang)
            text = f"🌐 {result}"
        except TranslateError as exc:
            text = f"❌ {exc}"
        await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)
        return

    if isinstance(command, QrCommand):
        try:
            png = make_qr_png(command.text)
            await bot.send_photo(
                chat_id=chat_id, photo=BufferedInputFile(png, filename="qr.png"), business_connection_id=connection_id
            )
        except QrError as exc:
            await bot.send_message(chat_id=chat_id, text=f"❌ {exc}", business_connection_id=connection_id)
        return

    if isinstance(command, ShortCommand):
        try:
            short_url = await shorten(http_session, command.url)
            text = f"🔗 {short_url}"
        except ShortenError as exc:
            text = f"❌ {exc}"
        await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)
        return

    if isinstance(command, SayCommand):
        await _handle_say(command, bot, storage, connection_id, chat_id, owner_id)
        return

    if isinstance(command, SimpleCommand):
        if command.name == "ping":
            await _notify_owner(bot, storage, connection_id, "🏓 pong — бот на связи")
            return
        if command.name == "id":
            await _notify_owner(
                bot, storage, connection_id,
                f"🆔 Chat ID: <code>{chat_id}</code>\nConnection ID: <code>{connection_id}</code>",
            )
            return


async def _run_spam(command: SpamCommand, message: Message, bot: Bot, storage: Storage, connection_id: str, chat_id: int) -> None:
    if command.text is not None:
        media = extract_media(message)
        for index in range(command.count):
            try:
                if media is not None:
                    await send_media_to_chat(bot, connection_id, chat_id, media, caption=command.text)
                else:
                    await bot.send_message(chat_id=chat_id, text=command.text, business_connection_id=connection_id)
            except Exception:
                logger.exception("Ошибка spam %s/%s", index + 1, command.count)
                break
            if index + 1 < command.count:
                await asyncio.sleep(0.05)
        return

    reply = message.reply_to_message
    if reply is None:
        await _notify_owner(
            bot, storage, connection_id,
            "❌ Укажите текст (<code>.spam 5 текст</code>) или ответьте (reply) на сообщение/медиа/стикер, "
            "которые нужно заспамить.",
        )
        return

    media = extract_media(reply)
    reply_text = reply.text or reply.caption
    for index in range(command.count):
        try:
            if media is not None:
                await send_media_to_chat(
                    bot, connection_id, chat_id, media, caption=reply_text if media.kind != "sticker" else None
                )
            elif reply_text:
                await bot.send_message(chat_id=chat_id, text=reply_text, business_connection_id=connection_id)
            else:
                break
        except Exception:
            logger.exception("Ошибка spam (reply) %s/%s", index + 1, command.count)
            break
        if index + 1 < command.count:
            await asyncio.sleep(0.05)


async def _run_typing(bot: Bot, connection_id: str, chat_id: int, seconds: int) -> None:
    for _ in range(seconds):
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING, business_connection_id=connection_id)
        await asyncio.sleep(1)


async def _handle_say(command: SayCommand, bot: Bot, storage: Storage, connection_id: str, chat_id: int, owner_id: int | None) -> None:
    if owner_id is None:
        return
    items = storage.preset_get(owner_id, command.name)
    if items is None:
        await _notify_owner(
            bot, storage, connection_id,
            f"❌ Пресет «{html.escape(command.name)}» не найден. Настройте в /settings → 🗂 Пресеты .say",
        )
        return
    for item in items:
        try:
            if item.get("type") == "text":
                await bot.send_message(chat_id=chat_id, text=item.get("content", ""), business_connection_id=connection_id)
            elif item.get("type") == "media":
                media = MediaRef(kind=item.get("kind", "document"), file_id=item.get("file_id", ""))
                await send_media_to_chat(bot, connection_id, chat_id, media)
        except Exception:
            logger.exception("Ошибка отправки пресета .say")
        await asyncio.sleep(0.05)


# --------------------------------------------------------------------- .view
async def _handle_view(message: Message, bot: Bot, storage: Storage, connection_id: str, command: ViewCommand) -> None:
    chat_id = message.chat.id
    message_id = message.message_id
    new_caption = command.caption or None

    try:
        await bot.edit_message_caption(
            business_connection_id=connection_id, chat_id=chat_id, message_id=message_id, caption=new_caption
        )
    except Exception:
        logger.exception("Не удалось очистить подпись .view")

    asyncio.create_task(_view_timer(bot, storage, connection_id, chat_id, message_id, command.seconds))


async def _view_timer(bot: Bot, storage: Storage, connection_id: str, chat_id: int, message_id: int, seconds: int) -> None:
    await asyncio.sleep(seconds)
    try:
        black = make_solid_png(512, 512)
        media = InputMediaPhoto(media=BufferedInputFile(black, filename="blackout.png"))
        await bot.edit_message_media(
            business_connection_id=connection_id, chat_id=chat_id, message_id=message_id, media=media
        )
    except Exception:
        logger.exception("Не удалось подменить фото на чёрное (.view)")

    await asyncio.sleep(2)
    try:
        storage.mark_bot_deleted(connection_id, chat_id, message_id)
        await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message_id])
    except Exception:
        logger.exception("Не удалось удалить сообщение (.view)")


# ---------------------------------------------------------- anti-search / anon stickers
async def _apply_antisearch(message: Message, bot: Bot, connection_id: str) -> None:
    transformed = antisearch_transform(message.text)
    if transformed == message.text:
        return
    try:
        await bot.edit_message_text(
            business_connection_id=connection_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=transformed,
        )
    except Exception:
        logger.exception("Не удалось применить антипоиск")


async def _anonymize_sticker(message: Message, bot: Bot, storage: Storage, connection_id: str) -> None:
    sticker = message.sticker
    data = await download_bytes(bot, sticker.file_id)
    if data is None:
        return
    chat_id = message.chat.id
    try:
        storage.mark_bot_deleted(connection_id, chat_id, message.message_id)
        await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
        photo = BufferedInputFile(data, filename="sticker.png")
        await bot.send_photo(chat_id=chat_id, photo=photo, business_connection_id=connection_id)
    except Exception:
        logger.exception("Не удалось анонимизировать стикер")


def _chat_title(message: Message) -> str:
    return message.chat.full_name or message.chat.username or str(message.chat.id)


def _flags_text(message: Message, old=None) -> str:
    from bot.media import media_flags

    flags = media_flags(message)
    if not flags and old and old.flags:
        flags = old.flags
    if not flags:
        return ""
    return "\n" + " ".join(flags)
