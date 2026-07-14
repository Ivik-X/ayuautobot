from __future__ import annotations

import asyncio
import html
import logging
import uuid
from pathlib import Path

import aiohttp
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from bot import subscription
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
    UnwatchCommand,
    ViewCommand,
    WatchCommand,
    parse_command,
)
from bot.features.antisearch import antisearch_transform
from bot.features.blackout import make_solid_png
from bot.features.qr import QrError, make_qr_png
from bot.features.shorten import ShortenError, shorten
from bot.features.translate import TranslateError, translate
from bot.fun import mock_text, reverse_text
from bot.handlers import ghost as ghost_handlers
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
        await ghost_handlers.relay_live_message(bot, storage, connection_id, message.chat.id, message)


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
    media = extract_media(message)
    if media is not None:
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
    owner_id = storage.owner_user_id(connection_id)
    if owner_id is not None and not subscription.feature_allowed(storage, owner_id, "afk"):
        return
    if not storage.should_send_afk_reply(connection_id, message.chat.id):
        return
    try:
        await bot.send_message(
            chat_id=message.chat.id, text=settings.afk_text, business_connection_id=connection_id
        )
    except Exception:
        logger.exception("Не удалось отправить AFK-автоответ")


async def _notify_upsell(bot: Bot, storage: Storage, connection_id: str, feature: str) -> None:
    await _notify_owner(
        bot, storage, connection_id,
        f"⭐ Функция «{feature}» доступна только с подпиской. Оформить можно в /menu → 💫 Подписка.",
    )


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

    if not subscription.is_premium(storage, owner_id):
        await _send_teaser(bot, storage, connection_id, owner_id, kind, caption, media)
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


KIND_LABEL_RU = {"delete": "удалено", "edit": "изменено"}


async def _send_teaser(
    bot: Bot, storage: Storage, connection_id: str, owner_id: int, kind: str, caption: str, media: MediaRef | None
) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        return
    row_id = storage.teaser_add(owner_id, kind, caption, media=media)
    remaining = subscription.reveal_remaining(storage, owner_id)
    label = KIND_LABEL_RU.get(kind, kind)

    if remaining > 0:
        text = (
            f"🔒 Сообщение {label}, текст скрыт (бесплатный тариф).\n"
            f"Осталось открытий в этом месяце: <b>{remaining}</b>"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔓 Открыть", callback_data=f"reveal:{row_id}")]]
        )
    else:
        text = (
            f"🔒 Сообщение {label}, текст скрыт (бесплатный тариф) — лимит открытий на этот месяц исчерпан."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💫 Оформить подписку", callback_data="sub:menu")]]
        )

    try:
        await bot.send_message(chat_id=owner_chat_id, text=text, reply_markup=keyboard)
    except Exception:
        logger.exception("Не удалось отправить тизер-уведомление")


@router.callback_query(F.data.startswith("reveal:"))
async def cb_reveal(call: CallbackQuery, storage: Storage) -> None:
    row_id = int(call.data.split(":", 1)[1])
    row = storage.teaser_get(row_id)
    if row is None:
        await call.answer("Уже открыто или устарело", show_alert=True)
        return
    owner_id = call.from_user.id
    if int(row["owner_id"]) != owner_id:
        await call.answer("Недоступно", show_alert=True)
        return

    if not subscription.consume_reveal(storage, owner_id):
        await call.answer("Лимит открытий на этот месяц исчерпан", show_alert=True)
        return

    storage.teaser_delete(row_id)
    media = None
    if row["media_kind"] and row["media_file_id"]:
        local_path = Path(row["media_path"]) if row["media_path"] else None
        media = MediaRef(
            kind=row["media_kind"], file_id=row["media_file_id"],
            local_path=local_path if local_path and local_path.exists() else None,
        )

    await call.answer()
    if media is not None:
        await send_media_copy(bot=call.bot, owner_chat_id=call.message.chat.id, media=media, caption=row["payload"])
    else:
        await call.message.answer(row["payload"])
    try:
        await call.message.delete()
    except Exception:
        pass


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
    WatchCommand: "watch",
    UnwatchCommand: "unwatch",
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
        if owner_id is not None and not subscription.feature_allowed(storage, owner_id, "view"):
            await _notify_upsell(bot, storage, connection_id, ".view")
            return
        await _handle_view(message, bot, storage, connection_id, command)
        return

    # кэшируем собственное сообщение владельца (для симметрии edit/delete и полной истории)
    await _cache_message(message, bot, storage, connection_id)

    if command is None:
        if settings.anti_search and message.text and not message.text.startswith("."):
            if owner_id is None or subscription.feature_allowed(storage, owner_id, "antisearch"):
                await _apply_antisearch(message, bot, connection_id)
        elif settings.anon_stickers and message.sticker and not (message.sticker.is_animated or message.sticker.is_video):
            if owner_id is None or subscription.feature_allowed(storage, owner_id, "extra"):
                await _anonymize_sticker(message, bot, storage, connection_id)
        return

    name = _command_name(command)
    flag = COMMAND_FLAG.get(name) if name else None
    if flag and not getattr(settings, flag, True):
        return  # команда выключена владельцем в /settings

    if isinstance(command, (WatchCommand, UnwatchCommand)) and owner_id is not None:
        if not subscription.feature_allowed(storage, owner_id, "extra"):
            await _notify_upsell(bot, storage, connection_id, ".watch")
            return

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
        if command.count > SPAM_CONFIRM_THRESHOLD:
            await _request_spam_confirmation(command, message, bot, storage, connection_id, chat_id, owner_id)
        else:
            await _run_spam(command, message, bot, storage, connection_id, chat_id, owner_id)
        return

    if isinstance(command, MuteCommand):
        seconds = command.seconds if command.seconds is not None else settings.mute_default_seconds
        if owner_id is not None:
            allowed_seconds = subscription.mute_allowance(storage, owner_id, seconds)
            if allowed_seconds <= 0:
                await _notify_upsell(bot, storage, connection_id, ".mute (лимит бесплатного тарифа исчерпан)")
                return
            if allowed_seconds < seconds:
                await _notify_owner(
                    bot, storage, connection_id,
                    f"⚠️ На бесплатном тарифе доступно только {allowed_seconds} из {seconds} сек — "
                    "mute включён на урезанное время. Снять лимит: /menu → 💫 Подписка.",
                )
            subscription.consume_mute(storage, owner_id, allowed_seconds)
            seconds = allowed_seconds
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

    if isinstance(command, WatchCommand):
        await _handle_watch(message, bot, storage, connection_id, chat_id, owner_id)
        return

    if isinstance(command, UnwatchCommand):
        removed = storage.watch_remove(connection_id, chat_id)
        text = "👁 Слежение за профилем этого чата выключено." if removed else "Этот чат и так не отслеживался."
        await _notify_owner(bot, storage, connection_id, text)
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


SPAM_CONFIRM_THRESHOLD = 150
_SPAM_CONFIRM_TTL = 600  # секунд, на сколько живёт неподтверждённый запрос

# token -> {"command", "message", "connection_id", "chat_id"}
_pending_spam: dict[str, dict] = {}


async def _request_spam_confirmation(
    command: SpamCommand, message: Message, bot: Bot, storage: Storage, connection_id: str, chat_id: int, owner_id: int | None
) -> None:
    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        return

    token = uuid.uuid4().hex[:12]
    _pending_spam[token] = {
        "command": command,
        "message": message,
        "connection_id": connection_id,
        "chat_id": chat_id,
        "owner_id": owner_id,
    }
    asyncio.create_task(_expire_spam_token(token))

    if command.text is not None:
        preview = html.escape(command.text[:200])
    elif message.reply_to_message is not None:
        preview = "содержимое реплая (медиа/текст/стикер)"
    else:
        preview = "—"

    text = (
        f"⚠️ <b>Подтверждение .spam</b>\n"
        f"Запрошена отправка <b>{command.count}</b> сообщений в чат «{html.escape(_chat_title(message))}».\n"
        f"Содержимое: {preview}\n\n"
        f"Команда уже удалена из чата — собеседник её не видел.\n"
        f"Запрос действует {_SPAM_CONFIRM_TTL // 60} мин."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"spam:yes:{token}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"spam:no:{token}"),
            ]
        ]
    )
    await bot.send_message(chat_id=owner_chat_id, text=text, reply_markup=keyboard)


async def _expire_spam_token(token: str) -> None:
    await asyncio.sleep(_SPAM_CONFIRM_TTL)
    _pending_spam.pop(token, None)


@router.callback_query(F.data.startswith("spam:yes:"))
async def cb_spam_confirm(call: CallbackQuery, bot: Bot, storage: Storage) -> None:
    token = call.data.split(":", 2)[2]
    entry = _pending_spam.pop(token, None)
    if entry is None:
        await call.answer("Запрос устарел или уже обработан", show_alert=True)
        return
    await call.answer("Отправляю…")
    await call.message.edit_text(call.message.text + "\n\n✅ Подтверждено, отправляю…")
    await _run_spam(entry["command"], entry["message"], bot, storage, entry["connection_id"], entry["chat_id"], entry["owner_id"])


@router.callback_query(F.data.startswith("spam:no:"))
async def cb_spam_cancel(call: CallbackQuery) -> None:
    token = call.data.split(":", 2)[2]
    _pending_spam.pop(token, None)
    await call.answer("Отменено")
    if call.message:
        await call.message.edit_text(call.message.text + "\n\n❌ Отменено.")


async def _run_spam(
    command: SpamCommand, message: Message, bot: Bot, storage: Storage, connection_id: str, chat_id: int,
    owner_id: int | None,
) -> None:
    allowed_count = command.count
    if owner_id is not None:
        allowed_count = subscription.spam_allowance(storage, owner_id, command.count)
        if allowed_count <= 0:
            await _notify_upsell(bot, storage, connection_id, ".spam (лимит бесплатного тарифа исчерпан)")
            return
        if allowed_count < command.count:
            await _notify_owner(
                bot, storage, connection_id,
                f"⚠️ На бесплатном тарифе доступно только {allowed_count} из {command.count} — "
                "остальное отправлено не будет. Снять лимит: /menu → 💫 Подписка.",
            )
        subscription.consume_spam(storage, owner_id, allowed_count)

    if command.text is not None:
        media = extract_media(message)
        for index in range(allowed_count):
            try:
                if media is not None:
                    await send_media_to_chat(bot, connection_id, chat_id, media, caption=command.text)
                else:
                    await bot.send_message(chat_id=chat_id, text=command.text, business_connection_id=connection_id)
            except Exception:
                logger.exception("Ошибка spam %s/%s", index + 1, allowed_count)
                break
            if index + 1 < allowed_count:
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
    for index in range(allowed_count):
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
            logger.exception("Ошибка spam (reply) %s/%s", index + 1, allowed_count)
            break
        if index + 1 < allowed_count:
            await asyncio.sleep(0.05)


async def _run_typing(bot: Bot, connection_id: str, chat_id: int, seconds: int) -> None:
    for _ in range(seconds):
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING, business_connection_id=connection_id)
        await asyncio.sleep(1)


async def _handle_watch(message: Message, bot: Bot, storage: Storage, connection_id: str, chat_id: int, owner_id: int | None) -> None:
    if owner_id is None:
        return
    chat_title = _chat_title(message)
    try:
        chat = await bot.get_chat(chat_id)
        photo_unique_id = chat.photo.small_file_unique_id if chat.photo else None
        snapshot = {
            "first_name": chat.first_name,
            "last_name": chat.last_name,
            "username": chat.username,
            "photo_unique_id": photo_unique_id,
        }
    except Exception:
        logger.exception("Не удалось получить снимок профиля для .watch")
        snapshot = {"first_name": None, "last_name": None, "username": None, "photo_unique_id": None}

    storage.watch_upsert(connection_id, chat_id, owner_id, chat_title, snapshot)
    await _notify_owner(
        bot, storage, connection_id,
        "👁 Слежение за профилем этого чата включено — уведомлю при смене имени, username или фото.",
    )


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
        # Загружаем стикер заново как НОВЫЙ файл (а не по старому file_id) —
        # тогда он не привязан ни к какому стикерпаку, и у собеседника не
        # появляется возможность через него перейти/добавить исходный пак.
        upload = BufferedInputFile(data, filename="sticker.webp")
        await bot.send_sticker(chat_id=chat_id, sticker=upload, business_connection_id=connection_id)
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
