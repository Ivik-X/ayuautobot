from __future__ import annotations

import asyncio
import html
import logging
import random

import aiohttp
from aiogram import Bot, Router
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, BusinessConnection, BusinessMessagesDeleted, Message

from bot.commands import (
    AfkCommand,
    CalcCommand,
    CurrencyCommand,
    EightBallCommand,
    EmojiSpamCommand,
    MuteCommand,
    NoteAddCommand,
    NoteDelCommand,
    NoteListCommand,
    PurgeCommand,
    QrCommand,
    RandCommand,
    RemindCommand,
    SayCommand,
    SetCommand,
    ShortCommand,
    SimpleCommand,
    SpamCommand,
    TextTransformCommand,
    TranslateCommand,
    TypingCommand,
    UnmuteCommand,
    WeatherCommand,
    parse_command,
)
from bot.config import Config
from bot.features.calc import CalcError, calculate
from bot.features.currency import CurrencyError, convert
from bot.features.qr import QrError, make_qr_png
from bot.features.shorten import ShortenError, shorten
from bot.features.translate import TranslateError, translate
from bot.features.weather import WeatherError, get_weather
from bot.fun import clown_text, eightball, mock_text, owo_text, reverse_text, zalgo_text
from bot.media import download_media, extract_media, send_media_copy
from bot.settings import SETTINGS_FIELDS, get_field, parse_value
from bot.stats import format_chats_report
from bot.storage import Storage, describe_message
from bot.texts import Texts

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
    config: Config,
    texts: Texts,
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

    await _cache_message(message, bot, storage, connection_id, config)

    if storage.is_owner_message(connection_id, message):
        await _handle_owner_message(message, bot, storage, connection_id, config, texts, http_session)
        return

    if storage.is_partner_message(connection_id, message):
        await _apply_mute(message, bot, storage, connection_id)
        await _maybe_afk_reply(message, bot, storage, connection_id)


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

    settings = storage.get_settings_for_connection(connection_id)

    old = storage.find_cached(connection_id, message.chat.id, message.message_id)
    if old is None and not storage.is_partner_message(connection_id, message):
        return
    if not settings.notify_edit:
        await _cache_message(message, bot, storage, connection_id, config)
        return

    await _cache_message(message, bot, storage, connection_id, config)
    fresh = storage.find_cached(connection_id, message.chat.id, message.message_id)
    media_ref = fresh.media if fresh else None

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
    settings = storage.get_settings_for_connection(connection_id)

    for message_id in event.message_ids:
        if storage.was_bot_deleted(connection_id, chat.id, message_id):
            continue

        cached = storage.remove_cached(connection_id, chat.id, message_id)
        if not settings.notify_delete:
            continue

        if cached is None:
            logger.warning(
                "Кэш не найден: connection=%s chat=%s msg=%s (сообщение не приходило в business_message?)",
                connection_id, chat.id, message_id,
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


async def _cache_message(message: Message, bot: Bot, storage: Storage, connection_id: str, config: Config) -> None:
    settings = storage.get_settings_for_connection(connection_id)
    media = extract_media(message)
    if media is not None and settings.save_media:
        media = await download_media(bot, media, message.message_id, connection_id)

    storage.cache_message(connection_id, message, media=media)
    logger.info(
        "Сохранено сообщение %s в чате %s (тип: %s)",
        message.message_id, message.chat.id, media.kind if media else "text",
    )


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
            chat_id=message.chat.id,
            text=settings.afk_text,
            business_connection_id=connection_id,
        )
    except Exception:
        logger.exception("Не удалось отправить AFK-автоответ")


async def _handle_owner_message(
    message: Message,
    bot: Bot,
    storage: Storage,
    connection_id: str,
    config: Config,
    texts: Texts,
    http_session: aiohttp.ClientSession,
) -> None:
    command = parse_command(message.text)
    if command is None:
        return

    chat_id = message.chat.id
    settings = storage.get_settings_for_connection(connection_id)
    owner_id = storage.owner_user_id(connection_id)

    try:
        await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
    except Exception:
        logger.debug("Не удалось удалить команду из чата", exc_info=True)

    # ---- базовые ----
    if isinstance(command, SpamCommand):
        await _run_spam(bot, connection_id, chat_id, command)
        return

    if isinstance(command, MuteCommand):
        seconds = command.seconds if command.seconds is not None else settings.mute_default_seconds
        storage.start_mute(connection_id, chat_id, seconds=seconds)
        await _notify_owner(
            bot, storage, connection_id,
            f"🔇 Mute включён на <b>{seconds}</b> сек. в чате <b>{html.escape(_chat_title(message))}</b>",
        )
        return

    if isinstance(command, UnmuteCommand):
        storage.stop_mute(connection_id, chat_id)
        await _notify_owner(bot, storage, connection_id, f"🔊 Mute выключен в чате <b>{html.escape(_chat_title(message))}</b>")
        return

    if isinstance(command, PurgeCommand):
        await _handle_purge(command, bot, storage, connection_id)
        return

    if isinstance(command, TypingCommand):
        await _run_typing(bot, connection_id, chat_id, command.seconds)
        return

    # ---- развлечения (feature_fun) ----
    if isinstance(command, EmojiSpamCommand):
        if not settings.feature_fun:
            return
        await _run_emoji_spam(bot, connection_id, chat_id, command)
        return

    if isinstance(command, TextTransformCommand):
        if not settings.feature_fun:
            return
        text = _transform(command.mode, command.text)
        await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)
        return

    if isinstance(command, RandCommand):
        value = random.randint(command.low, command.high)
        await bot.send_message(chat_id=chat_id, text=f"🎲 {value}", business_connection_id=connection_id)
        return

    if isinstance(command, EightBallCommand):
        if not settings.feature_fun:
            return
        answer = eightball()
        prefix = f"❓ {html.escape(command.question)}\n" if command.question else ""
        await bot.send_message(chat_id=chat_id, text=f"{prefix}🎱 {answer}", business_connection_id=connection_id)
        return

    # ---- утилиты (feature_utility) ----
    if isinstance(command, CalcCommand):
        if not settings.feature_utility:
            return
        await _handle_calc(bot, connection_id, chat_id, command)
        return

    if isinstance(command, TranslateCommand):
        if not settings.feature_utility:
            return
        await _handle_translate(bot, connection_id, chat_id, command, http_session)
        return

    if isinstance(command, WeatherCommand):
        if not settings.feature_utility:
            return
        await _handle_weather(bot, connection_id, chat_id, command, http_session)
        return

    if isinstance(command, CurrencyCommand):
        if not settings.feature_utility:
            return
        await _handle_currency(bot, connection_id, chat_id, command, http_session)
        return

    if isinstance(command, QrCommand):
        if not settings.feature_utility:
            return
        await _handle_qr(bot, connection_id, chat_id, command)
        return

    if isinstance(command, ShortCommand):
        if not settings.feature_utility:
            return
        await _handle_short(bot, connection_id, chat_id, command, http_session)
        return

    # ---- заметки/напоминания/afk ----
    if isinstance(command, (NoteAddCommand, NoteDelCommand, NoteListCommand, SayCommand)):
        await _handle_notes(command, bot, storage, connection_id, chat_id, owner_id)
        return

    if isinstance(command, RemindCommand):
        if owner_id is not None:
            storage.reminder_add(owner_id, connection_id, chat_id, command.minutes, command.text)
        await _notify_owner(bot, storage, connection_id, f"⏰ Напомню через <b>{command.minutes:g}</b> мин.")
        return

    if isinstance(command, AfkCommand):
        await _handle_afk(command, storage, bot, connection_id, owner_id)
        return

    if isinstance(command, SetCommand):
        await _handle_set(command, storage, bot, connection_id, owner_id)
        return

    if isinstance(command, SimpleCommand):
        await _run_simple(bot, storage, connection_id, chat_id, command, message, config, texts)


async def _handle_purge(command: PurgeCommand, bot: Bot, storage: Storage, connection_id: str) -> None:
    if command.minutes is None:
        removed = storage.purge_all()
        await _notify_owner(bot, storage, connection_id, f"🧹 Кэш полностью очищен: <b>{removed}</b> записей")
    else:
        removed = storage.purge_older_than(command.minutes)
        await _notify_owner(
            bot, storage, connection_id,
            f"🧹 Удалено <b>{removed}</b> записей старше {command.minutes} мин.\n"
            f"В кэше осталось: <b>{storage.cached_count()}</b>",
        )


async def _handle_calc(bot: Bot, connection_id: str, chat_id: int, command: CalcCommand) -> None:
    try:
        result = calculate(command.expression)
        text = f"🧮 {command.expression} = <b>{result:g}</b>"
    except CalcError as exc:
        text = f"❌ {exc}"
    await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)


async def _handle_translate(
    bot: Bot, connection_id: str, chat_id: int, command: TranslateCommand, session: aiohttp.ClientSession
) -> None:
    try:
        result = await translate(session, command.text, command.lang)
        text = f"🌐 {result}"
    except TranslateError as exc:
        text = f"❌ {exc}"
    await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)


async def _handle_weather(
    bot: Bot, connection_id: str, chat_id: int, command: WeatherCommand, session: aiohttp.ClientSession
) -> None:
    try:
        text = await get_weather(session, command.city)
    except WeatherError as exc:
        text = f"❌ {exc}"
    await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)


async def _handle_currency(
    bot: Bot, connection_id: str, chat_id: int, command: CurrencyCommand, session: aiohttp.ClientSession
) -> None:
    try:
        amount, converted = await convert(session, command.amount, command.frm, command.to)
        text = f"💱 {amount:g} {command.frm} = <b>{converted:.2f} {command.to}</b>"
    except CurrencyError as exc:
        text = f"❌ {exc}"
    await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)


async def _handle_qr(bot: Bot, connection_id: str, chat_id: int, command: QrCommand) -> None:
    try:
        png = make_qr_png(command.text)
        photo = BufferedInputFile(png, filename="qr.png")
        await bot.send_photo(chat_id=chat_id, photo=photo, business_connection_id=connection_id)
    except QrError as exc:
        await bot.send_message(chat_id=chat_id, text=f"❌ {exc}", business_connection_id=connection_id)


async def _handle_short(
    bot: Bot, connection_id: str, chat_id: int, command: ShortCommand, session: aiohttp.ClientSession
) -> None:
    try:
        short_url = await shorten(session, command.url)
        text = f"🔗 {short_url}"
    except ShortenError as exc:
        text = f"❌ {exc}"
    await bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)


async def _handle_notes(command, bot: Bot, storage: Storage, connection_id: str, chat_id: int, owner_id: int | None) -> None:
    if owner_id is None:
        return

    if isinstance(command, NoteAddCommand):
        storage.note_add(owner_id, command.name, command.text)
        await _notify_owner(bot, storage, connection_id, f"💾 Заметка «{html.escape(command.name)}» сохранена.")
        return

    if isinstance(command, NoteDelCommand):
        ok = storage.note_delete(owner_id, command.name)
        text = "🗑 Заметка удалена." if ok else "❌ Заметка не найдена."
        await _notify_owner(bot, storage, connection_id, text)
        return

    if isinstance(command, NoteListCommand):
        names = storage.note_list(owner_id)
        if not names:
            text = "У вас пока нет заметок. Добавьте: <code>.note add имя текст</code>"
        else:
            text = "📝 <b>Заметки:</b>\n" + "\n".join(f"• {html.escape(n)}" for n in names)
        await _notify_owner(bot, storage, connection_id, text)
        return

    if isinstance(command, SayCommand):
        content = storage.note_get(owner_id, command.name)
        if content is None:
            await _notify_owner(bot, storage, connection_id, f"❌ Заметка «{html.escape(command.name)}» не найдена.")
            return
        await bot.send_message(chat_id=chat_id, text=content, business_connection_id=connection_id)


async def _handle_afk(command: AfkCommand, storage: Storage, bot: Bot, connection_id: str, owner_id: int | None) -> None:
    if owner_id is None:
        return
    if command.enable:
        settings = storage.get_settings(owner_id)
        text = command.text or settings.afk_text
        storage.update_setting(owner_id, "afk_enabled", True)
        storage.update_setting(owner_id, "afk_text", text)
        await _notify_owner(bot, storage, connection_id, f"💤 AFK включён. Автоответ:\n{html.escape(text)}")
    else:
        storage.update_setting(owner_id, "afk_enabled", False)
        await _notify_owner(bot, storage, connection_id, "🙋 AFK выключен.")


async def _handle_set(command: SetCommand, storage: Storage, bot: Bot, connection_id: str, owner_id: int | None) -> None:
    if owner_id is None:
        return
    field = get_field(command.key)
    if field is None:
        available = ", ".join(f.key for f in SETTINGS_FIELDS)
        await _notify_owner(bot, storage, connection_id, f"❌ Неизвестная настройка. Доступные: {available}")
        return
    try:
        value = parse_value(field.kind, command.value)
    except ValueError as exc:
        await _notify_owner(bot, storage, connection_id, f"❌ {exc}")
        return
    storage.update_setting(owner_id, command.key, value)
    await _notify_owner(bot, storage, connection_id, f"✅ {field.label} = {value}")


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
    settings = storage.get_settings_for_connection(connection_id)

    if command.name == "help":
        await _notify_owner(bot, storage, connection_id, texts.help)
        return

    if command.name == "dice":
        if not settings.feature_fun:
            return
        await bot.send_dice(chat_id=chat_id, business_connection_id=connection_id)
        return

    if command.name == "flip":
        if not settings.feature_fun:
            return
        result = random.choice(["🪙 Орёл", "🪙 Решка"])
        await bot.send_message(chat_id=chat_id, text=result, business_connection_id=connection_id)
        return

    if command.name == "stats":
        muted = "да" if storage.mute_active(connection_id, chat_id) else "нет"
        db_line = (
            f"\nБД: <b>{storage.db_count(connection_id)}</b> активных · "
            f"<b>{storage.db_total_count()}</b> всего"
        )
        await _notify_owner(
            bot, storage, connection_id,
            f"📦 <b>Кэш и БД</b>\n"
            f"Записей в RAM: <b>{storage.cached_count(connection_id)}</b>\n"
            f"Медиафайлов в RAM: <b>{storage.media_files_count()}</b>\n"
            f"Персональный TTL кэша: <b>{settings.cache_ttl_hours:g}</b> ч"
            f"{db_line}\n"
            f"Mute в этом чате: <b>{muted}</b>\n\n"
            f"Подробнее: <code>.chats</code> · Настройки: <code>.settings</code>",
        )
        return

    if command.name == "chats":
        report = format_chats_report(
            storage.chat_stats(connection_id),
            cache_size=storage.cached_count(connection_id),
            media_files=storage.media_files_count(),
            ttl_hours=settings.cache_ttl_hours,
            max_entries=config.cache.max_entries,
            db_active=storage.db_count(connection_id),
            db_total=storage.db_total_count(),
        )
        await _notify_owner(bot, storage, connection_id, report)
        return

    if command.name == "ping":
        await _notify_owner(bot, storage, connection_id, texts.pong)
        return

    if command.name == "info":
        chat_title = _chat_title(message)
        await _notify_owner(
            bot, storage, connection_id,
            f"ℹ️ <b>Информация о чате</b>\n"
            f"Название: <b>{html.escape(chat_title)}</b>\n"
            f"Chat ID: <code>{chat_id}</code>\n"
            f"Connection ID: <code>{connection_id}</code>\n"
            f"Сообщений в кэше (этот чат/всего): "
            f"<b>{storage.cached_count(connection_id)}</b>",
        )
        return

    if command.name == "id":
        await _notify_owner(
            bot, storage, connection_id,
            f"🆔 Chat ID: <code>{chat_id}</code>\nConnection ID: <code>{connection_id}</code>",
        )
        return

    if command.name == "settings":
        await _notify_owner(
            bot, storage, connection_id,
            "⚙️ Откройте личный чат с ботом и отправьте /settings, чтобы изменить настройки, "
            "либо используйте <code>.set ключ значение</code> прямо здесь.",
        )
        return


async def _run_spam(bot: Bot, connection_id: str, chat_id: int, command: SpamCommand) -> None:
    for index in range(command.count):
        try:
            await bot.send_message(chat_id=chat_id, text=command.text, business_connection_id=connection_id)
        except Exception:
            logger.exception("Ошибка при отправке spam-сообщения %s/%s", index + 1, command.count)
            break
        if index + 1 < command.count:
            await asyncio.sleep(0.05)


async def _run_typing(bot: Bot, connection_id: str, chat_id: int, seconds: int) -> None:
    for _ in range(seconds):
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING, business_connection_id=connection_id)
        await asyncio.sleep(1)


async def _run_emoji_spam(bot: Bot, connection_id: str, chat_id: int, command: EmojiSpamCommand) -> None:
    for index in range(command.count):
        try:
            await bot.send_message(chat_id=chat_id, text=command.emoji, business_connection_id=connection_id)
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


async def _notify_owner(bot: Bot, storage: Storage, connection_id: str, text: str) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        logger.warning("Нет owner_chat_id для connection %s", connection_id)
        return
    try:
        await bot.send_message(chat_id=owner_chat_id, text=text)
    except Exception:
        logger.exception("Не удалось отправить уведомление владельцу")


async def _notify_owner_media(bot: Bot, storage: Storage, connection_id: str, media, caption: str) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        return
    await send_media_copy(bot, owner_chat_id, media, caption=caption)
