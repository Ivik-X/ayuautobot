from __future__ import annotations

import asyncio
import html
import logging
import random

from aiogram import Bot, Router
from aiogram.enums import ChatAction
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message

from bot.commands import (
    EmojiSpamCommand,
    MuteCommand,
    PurgeCommand,
    RandCommand,
    SimpleCommand,
    SpamCommand,
    TextTransformCommand,
    TypingCommand,
    UnmuteCommand,
    parse_command,
)
from bot.config import Config
from bot.fun import clown_text, mock_text, owo_text, reverse_text, zalgo_text
from bot.media import MediaRef, download_media, extract_media, send_media_copy
from bot.stats import format_chats_report
from bot.storage import Storage, describe_message
from bot.texts import Texts

logger = logging.getLogger(__name__)

router = Router(name="business")


@router.business_connection()
async def on_business_connection(connection: BusinessConnection, storage: Storage) -> None:
    storage.set_connection(connection)
    status = "подключён" if connection.is_enabled else "отключён"
    logger.info(
        "Business connection %s: %s (user=%s)",
        connection.id,
        status,
        connection.user.id,
    )


@router.business_message()
async def on_business_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    config: Config,
    texts: Texts,
) -> None:
    connection_id = message.business_connection_id
    if not connection_id:
        return

    connection = await _ensure_connection(bot, storage, connection_id)
    if connection is None or not connection.is_enabled:
        return

    if storage.is_bot_message(message):
        return

    logger.debug(
        "business_message id=%s chat=%s from=%s text=%s",
        message.message_id,
        message.chat.id,
        message.from_user.id if message.from_user else None,
        bool(message.text or message.caption),
    )

    await _cache_message(message, bot, storage, connection_id, config)

    if storage.is_owner_message(connection_id, message):
        await _handle_owner_message(message, bot, storage, connection_id, config, texts)
        return

    if storage.is_partner_message(connection_id, message):
        await _apply_mute(message, bot, storage, connection_id)


@router.edited_business_message()
async def on_edited_business_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    config: Config,
) -> None:
    connection_id = message.business_connection_id
    if not connection_id:
        return

    connection = await _ensure_connection(bot, storage, connection_id)
    if connection is None:
        return

    old = storage.find_cached(connection_id, message.chat.id, message.message_id)
    if old is None and not storage.is_partner_message(connection_id, message):
        return

    await _cache_message(message, bot, storage, connection_id, config)
    media = storage.find_cached(connection_id, message.chat.id, message.message_id)
    media_ref = media.media if media else None

    partner = message.from_user.full_name if message.from_user else "Собеседник"
    chat_title = _chat_title(message)

    old_text = old.content if old else "— (не сохранено)"
    new_text = describe_message(message)
    flags = media_flags_text(message, old)

    caption = (
        f"✏️ <b>Сообщение отредактировано</b>\n"
        f"Чат: <b>{html.escape(chat_title)}</b>\n"
        f"От: <b>{html.escape(partner)}</b>{flags}\n\n"
        f"<b>Было:</b>\n{html.escape(old_text)}\n\n"
        f"<b>Стало:</b>\n{html.escape(new_text)}"
    )

    if media_ref is not None:
        await _notify_owner_media(bot, storage, connection_id, media_ref, caption)
    elif old and old.media is not None:
        await _notify_owner_media(bot, storage, connection_id, old.media, caption)
    else:
        await _notify_owner(bot, storage, connection_id, caption)


@router.deleted_business_messages()
async def on_deleted_business_messages(
    event: BusinessMessagesDeleted,
    bot: Bot,
    storage: Storage,
) -> None:
    connection_id = event.business_connection_id
    chat = event.chat
    chat_title = chat.full_name or chat.username or str(chat.id)

    await _ensure_connection(bot, storage, connection_id)

    for message_id in event.message_ids:
        if storage.was_bot_deleted(connection_id, chat.id, message_id):
            continue

        cached = storage.remove_cached(connection_id, chat.id, message_id)
        if cached is None:
            logger.warning(
                "Кэш не найден: connection=%s chat=%s msg=%s (сообщение не приходило в business_message?)",
                connection_id,
                chat.id,
                message_id,
            )

        sender = cached.from_user_name if cached else (chat.full_name or chat.username or "собеседник")
        body = cached.content if cached else "— (сообщение не было получено ботом — отправьте его заново пока бот запущен)"
        flags = ""
        if cached and cached.flags:
            flags = "\n" + " ".join(cached.flags)

        caption = (
            f"🗑 <b>Сообщение удалено</b>\n"
            f"Чат: <b>{html.escape(chat_title)}</b>\n"
            f"От: <b>{html.escape(sender)}</b>\n"
            f"ID: <code>{message_id}</code>{flags}\n\n"
            f"<b>Содержимое:</b>\n{html.escape(body)}"
        )

        if cached and cached.media is not None:
            await _notify_owner_media(bot, storage, connection_id, cached.media, caption)
        else:
            await _notify_owner(bot, storage, connection_id, caption)


async def _ensure_connection(
    bot: Bot,
    storage: Storage,
    connection_id: str,
) -> BusinessConnection | None:
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


async def _cache_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    connection_id: str,
    config: Config,
) -> None:
    media = extract_media(message)
    if media is not None and config.save_media:
        media = await download_media(bot, media, message.message_id)
        if (
            config.db.enabled
            and config.db.save_media
            and storage.db is not None
            and media.local_path is not None
        ):
            db_path = storage.db.persist_media_copy(
                media.local_path,
                message.message_id,
                media.kind,
            )
            if db_path is not None:
                media = MediaRef(
                    kind=media.kind,
                    file_id=media.file_id,
                    local_path=db_path,
                    mime_type=media.mime_type,
                    file_name=media.file_name,
                )

    storage.cache_message(connection_id, message, media=media)
    logger.info(
        "Сохранено сообщение %s в чате %s (тип: %s)",
        message.message_id,
        message.chat.id,
        media.kind if media else "text",
    )


async def _apply_mute(
    message: Message,
    bot: Bot,
    storage: Storage,
    connection_id: str,
) -> None:
    if not storage.mute_active(connection_id, message.chat.id):
        return

    try:
        storage.mark_bot_deleted(connection_id, message.chat.id, message.message_id)
        await bot.delete_business_messages(
            business_connection_id=connection_id,
            message_ids=[message.message_id],
        )
        storage.register_mute_deletion(connection_id, message.chat.id)
    except Exception:
        logger.exception("Не удалось удалить сообщение в mute-режиме")


async def _handle_owner_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    connection_id: str,
    config: Config,
    texts: Texts,
) -> None:
    command = parse_command(message.text)
    if command is None:
        return

    chat_id = message.chat.id

    try:
        await bot.delete_business_messages(
            business_connection_id=connection_id,
            message_ids=[message.message_id],
        )
    except Exception:
        logger.debug("Не удалось удалить команду из чата", exc_info=True)

    if isinstance(command, SpamCommand):
        await _run_spam(bot, connection_id, chat_id, command)
        return

    if isinstance(command, MuteCommand):
        storage.start_mute(connection_id, chat_id, seconds=command.seconds)
        await _notify_owner(
            bot,
            storage,
            connection_id,
            f"🔇 Mute включён на <b>{command.seconds}</b> сек. в чате "
            f"<b>{html.escape(_chat_title(message))}</b>",
        )
        return

    if isinstance(command, UnmuteCommand):
        storage.stop_mute(connection_id, chat_id)
        await _notify_owner(
            bot,
            storage,
            connection_id,
            f"🔊 Mute выключен в чате <b>{html.escape(_chat_title(message))}</b>",
        )
        return

    if isinstance(command, PurgeCommand):
        if command.minutes is None:
            removed = storage.purge_all()
            await _notify_owner(
                bot,
                storage,
                connection_id,
                f"🧹 Кэш полностью очищен: <b>{removed}</b> записей",
            )
        else:
            removed = storage.purge_older_than(command.minutes)
            await _notify_owner(
                bot,
                storage,
                connection_id,
                f"🧹 Удалено <b>{removed}</b> записей старше {command.minutes} мин.\n"
                f"В кэше осталось: <b>{storage.cached_count()}</b>",
            )
        return

    if isinstance(command, TypingCommand):
        await _run_typing(bot, connection_id, chat_id, command.seconds)
        return

    if isinstance(command, EmojiSpamCommand):
        await _run_emoji_spam(bot, connection_id, chat_id, command)
        return

    if isinstance(command, TextTransformCommand):
        text = _transform(command.mode, command.text)
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            business_connection_id=connection_id,
        )
        return

    if isinstance(command, RandCommand):
        value = random.randint(command.low, command.high)
        await bot.send_message(
            chat_id=chat_id,
            text=f"🎲 {value}",
            business_connection_id=connection_id,
        )
        return

    if isinstance(command, SimpleCommand):
        await _run_simple(bot, storage, connection_id, chat_id, command, message, config, texts)


async def _run_simple(
    bot: Bot,
    storage: Storage,
    connection_id: str,
    chat_id: int,
    command: SimpleCommand,
    message: Message,
    config: Config,
    texts: Texts,
) -> None:
    if command.name == "help":
        await _notify_owner(bot, storage, connection_id, texts.help)
        return

    if command.name == "dice":
        await bot.send_dice(chat_id=chat_id, business_connection_id=connection_id)
        return

    if command.name == "flip":
        result = random.choice(["🪙 Орёл", "🪙 Решка"])
        await bot.send_message(chat_id=chat_id, text=result, business_connection_id=connection_id)
        return

    if command.name == "stats":
        muted = "да" if storage.mute_active(connection_id, chat_id) else "нет"
        db_line = ""
        if config.db.enabled:
            db_line = (
                f"\nБД: <b>{storage.db_count() or 0}</b> активных · "
                f"<b>{storage.db_total_count() or 0}</b> всего"
            )
        await _notify_owner(
            bot,
            storage,
            connection_id,
            f"📦 <b>Кэш RAM</b>\n"
            f"Записей: <b>{storage.cached_count()}</b> / {config.cache_max_entries}\n"
            f"Медиафайлов: <b>{storage.media_files_count()}</b>\n"
            f"Автоочистка: <b>{config.cache_ttl_hours:g}</b> ч"
            f"{db_line}\n"
            f"Mute в этом чате: <b>{muted}</b>\n\n"
            f"Подробнее: <code>.chats</code>",
        )
        return

    if command.name == "chats":
        report = format_chats_report(
            storage.chat_stats(connection_id),
            cache_size=storage.cached_count(connection_id),
            media_files=storage.media_files_count(),
            ttl_hours=config.cache_ttl_hours,
            max_entries=config.cache_max_entries,
            db_active=storage.db_count(connection_id) if config.db.enabled else None,
            db_total=storage.db_total_count() if config.db.enabled else None,
        )
        await _notify_owner(bot, storage, connection_id, report)
        return

    if command.name == "ping":
        await _notify_owner(bot, storage, connection_id, texts.pong)


async def _run_spam(bot: Bot, connection_id: str, chat_id: int, command: SpamCommand) -> None:
    for index in range(command.count):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=command.text,
                business_connection_id=connection_id,
            )
        except Exception:
            logger.exception("Ошибка при отправке spam-сообщения %s/%s", index + 1, command.count)
            break
        if index + 1 < command.count:
            await asyncio.sleep(0.05)


async def _run_typing(bot: Bot, connection_id: str, chat_id: int, seconds: int) -> None:
    for _ in range(seconds):
        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.TYPING,
            business_connection_id=connection_id,
        )
        await asyncio.sleep(1)


async def _run_emoji_spam(
    bot: Bot,
    connection_id: str,
    chat_id: int,
    command: EmojiSpamCommand,
) -> None:
    for index in range(command.count):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=command.emoji,
                business_connection_id=connection_id,
            )
        except Exception:
            logger.exception("Ошибка emoji-spam %s/%s", index + 1, command.count)
            break
        if index + 1 < command.count:
            await asyncio.sleep(0.05)


def _transform(mode: str, text: str) -> str:
    if mode == "mock":
        return mock_text(text)
    if mode == "clown":
        return clown_text(text)
    if mode == "owo":
        return owo_text(text)
    if mode == "reverse":
        return reverse_text(text)
    if mode == "zalgo":
        return zalgo_text(text)
    return text


def _chat_title(message: Message) -> str:
    return message.chat.full_name or message.chat.username or str(message.chat.id)


def media_flags_text(message: Message, old=None) -> str:
    from bot.media import media_flags

    flags = media_flags(message)
    if not flags and old and old.flags:
        flags = old.flags
    if not flags:
        return ""
    return "\n" + " ".join(flags)


async def _notify_owner(
    bot: Bot,
    storage: Storage,
    connection_id: str,
    text: str,
) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        logger.warning("Нет owner_chat_id для connection %s", connection_id)
        return
    try:
        await bot.send_message(chat_id=owner_chat_id, text=text)
    except Exception:
        logger.exception("Не удалось отправить уведомление владельцу")


async def _notify_owner_media(
    bot: Bot,
    storage: Storage,
    connection_id: str,
    media,
    caption: str,
) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        return
    await send_media_copy(bot, owner_chat_id, media, caption=caption)
