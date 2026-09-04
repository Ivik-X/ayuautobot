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

import contextlib

from bot import subscription
from bot.commands import (
    ChatStatCommand,
    CloneCommand,
    DelCommand,
    MuteCommand,
    QrCommand,
    SayCommand,
    ShortCommand,
    SimpleCommand,
    SpamCommand,
    TextTransformCommand,
    ToNoteCommand,
    ToVoiceCommand,
    TranslateCommand,
    TrollCommand,
    TypingCommand,
    UnmuteCommand,
    UnwatchCommand,
    ViewCommand,
    WatchCommand,
    parse_command,
)
from bot.features.antisearch import antisearch_transform
from bot.features.blackout import make_solid_png
from bot.features.chatgraph import make_hourly_chart, make_kinds_pie
from bot.features.murino import transform as murino_transform
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


def _format_days_ru(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    elif 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return f"{n} дня"
    else:
        return f"{n} дней"


def _is_whitelisted_message(storage: Storage, message: Message) -> bool:
    if message.from_user and storage.is_whitelisted(message.from_user.id):
        return True
    if message.chat and storage.is_whitelisted(message.chat.id):
        return True
    return False


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

    if _is_whitelisted_message(storage, message):
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

    if _is_whitelisted_message(storage, message):
        return

    connection = await _ensure_connection(bot, storage, connection_id)
    if connection is None:
        return

    # Если редактирование было вызвано самим ботом (антипоиск / .view) — игнорируем
    if storage.was_bot_edited(connection_id, message.chat.id, message.message_id):
        await _cache_message(message, bot, storage, connection_id)
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
    chat_link = _clickable_chat_link(message.chat, chat_title)
    old_text = old.content if old else "— (не сохранено)"
    new_text = describe_message(message)
    flags = _flags_text(message, old)

    edit_history = storage.db.get_edit_history(connection_id, message.chat.id, message.message_id)
    if len(edit_history) > 1:
        history_lines = []
        for idx, entry in enumerate(edit_history, start=1):
            ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", time.time())))
            history_lines.append(f"{idx}️⃣ <i>[{ts}]</i> {html.escape(entry.get('content', ''))}")
        history_block = "<b>📜 Хронология правок:</b>\n" + "\n".join(history_lines) + "\n\n"
        edit_num_str = f" (Правка #{len(edit_history)})"
    else:
        history_block = f"<b>Было:</b>\n{html.escape(old_text)}\n\n"
        edit_num_str = ""

    caption = (
        f"✏️ <b>Сообщение отредактировано</b>{edit_num_str}\n"
        f"Чат: {chat_link}\n"
        f"От: <b>{html.escape(partner)}</b>{flags}\n\n"
        f"{history_block}"
        f"<b>Текущая версия (Стало):</b>\n{html.escape(new_text)}"
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
    if chat and storage.is_whitelisted(chat.id):
        return

    chat_title = chat.full_name or chat.username or str(chat.id)
    chat_link = _clickable_chat_link(chat, chat_title)

    await _ensure_connection(bot, storage, connection_id)
    settings = storage.get_settings_for_connection(connection_id)
    owner_id = storage.owner_user_id(connection_id)

    logger.info(
        "deleted_business_messages: connection=%s chat=%s count=%s owner_id=%s notify_mode=%s",
        connection_id, chat.id, len(event.message_ids), owner_id,
        getattr(settings, "notify_delete_mode", "unknown"),
    )

    if len(event.message_ids) >= 20 and owner_id is not None:
        alert_text = (
            f"🚨 <b>Собеседник массово очистил диалог!</b>\n"
            f"Чат: {chat_link}\n"
            f"Удалено сообщений: <b>{len(event.message_ids)}</b>\n\n"
            f"📦 <i>Автоматически выгружаю сохранённую историю чата в ЛС…</i>"
        )
        try:
            await _notify_owner(bot, storage, connection_id, alert_text)
            owner_chat_id = storage.owner_chat_id(connection_id)
            if owner_chat_id:
                from bot.features.chat_export import build_export_html, build_export_json
                rows = storage.messages_for_chat(connection_id, chat.id)
                if rows:
                    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in chat_title).strip()[:40] or "chat"
                    export_json = build_export_json(chat_title, chat.id, rows)
                    export_html = build_export_html(chat_title, owner_id, rows)
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        json_path = Path(tmp_dir) / f"dump_{chat.id}.json"
                        html_path = Path(tmp_dir) / f"dump_{chat.id}.html"
                        json_path.write_text(json.dumps(export_json, ensure_ascii=False, indent=2), encoding="utf-8")
                        html_path.write_text(export_html, encoding="utf-8")
                        await bot.send_document(
                            owner_chat_id,
                            FSInputFile(html_path, filename=f"AUTODUMP_{safe_name}.html"),
                            caption=f"🚨 Авто-дамп очищенного чата «{chat_title}» (HTML)",
                        )
                        await bot.send_document(
                            owner_chat_id,
                            FSInputFile(json_path, filename=f"AUTODUMP_{safe_name}.json"),
                            caption=f"🚨 Авто-дамп очищенного чата «{chat_title}» (JSON)",
                        )
        except Exception:
            logger.exception("Не удалось выполнить авто-дамп чата при очистке")

        # Отмечаем все удалённые сообщения в БД и не затапливаем ЛС десятками уведомлений
        for message_id in event.message_ids:
            storage.remove_cached(connection_id, chat.id, message_id)
            storage.was_bot_deleted(connection_id, chat.id, message_id)
        return


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
            f"Чат: {chat_link}\n"
            f"От: <b>{html.escape(sender)}</b>\n"
            f"ID: <code>{message_id}</code>{flags}{origin_note}\n\n"
            f"<b>Содержимое:</b>\n{html.escape(body)}"
        )

        # Одноразовые/защищённые медиа: Telegram не даёт скопировать их через API
        media = cached.media if cached else None
        if media is not None and "🔒" in " ".join(cached.flags or []):
            # защищённое — шлём только текстовое уведомление (Telegram заблокирует file_id)
            caption += "\n\n<i>[🔒 Одноразовое/защищённое медиа — Telegram не позволяет скопировать его]</i>"
            media = None
        await _dispatch_notification(
            bot, storage, connection_id, owner_id, settings.notify_delete_mode, "delete",
            caption=caption, media=media,
        )


def _clickable_chat_link(chat, chat_title_str: str) -> str:
    escaped = html.escape(chat_title_str)
    username = getattr(chat, "username", None)
    if username:
        return f'<a href="https://t.me/{username}"><b>{escaped}</b></a>'
    chat_id = getattr(chat, "id", None)
    if chat_id and chat_id > 0:
        return f'<a href="tg://user?id={chat_id}"><b>{escaped}</b></a>'
    return f"<b>{escaped}</b>"


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
        max_file_mb = storage.get_global().media_max_file_mb
        media = await download_media(bot, media, message.message_id, connection_id, max_file_mb=max_file_mb)
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
    WatchCommand: "watch",
    UnwatchCommand: "unwatch",
    ToNoteCommand: "tonote",
    ToVoiceCommand: "tovoice",
    ChatStatCommand: "chatstat",
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
            await _apply_antisearch(message, bot, storage, connection_id)
        elif settings.anon_stickers and message.sticker and not (message.sticker.is_animated or message.sticker.is_video):
            await _anonymize_sticker(message, bot, storage, connection_id)
        elif settings.murino_mode and message.text and not message.text.startswith("."):
            await _apply_murino(message, bot, storage, connection_id)
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
        if command.count > 50:
            await _notify_owner(
                bot, storage, connection_id,
                "⚠️ <b>Лимит спама превышен.</b> Максимум 50 сообщений за раз (защита аккаунта от блокировки Telegram)."
            )
            return
        if command.count > SPAM_CONFIRM_THRESHOLD:
            await _request_spam_confirmation(command, message, bot, storage, connection_id, chat_id, owner_id)
        else:
            await _run_spam(command, message, bot, storage, connection_id, chat_id, owner_id)
        return

    if isinstance(command, MuteCommand):
        seconds = command.seconds if command.seconds is not None else settings.mute_default_seconds
        storage.start_mute(connection_id, chat_id, seconds=seconds)
        await _notify_owner(bot, storage, connection_id, f"🔇 Mute включён на <b>{seconds}</b> сек.")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔊 Снять Mute", callback_data=f"unmute_chat:{chat_id}")]]
        )
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"🔇 <b>В этом чате включен Mute.</b> Входящие сообщения фильтруются ({seconds} сек).",
                reply_markup=kb,
                business_connection_id=connection_id,
            )
        except Exception:
            logger.debug("Не удалось отправить сообщение об активации Mute в чат")
        return

    if isinstance(command, UnmuteCommand):
        storage.stop_mute(connection_id, chat_id)
        await _notify_owner(bot, storage, connection_id, "🔊 Mute выключен")
        return

    if isinstance(command, TrollCommand):
        from bot.texts import load_texts
        import random
        phrases = list(load_texts().troll_phrases)
        random.shuffle(phrases)
        for i in range(command.count):
            phrase = phrases[i % len(phrases)]
            try:
                await bot.send_message(chat_id=chat_id, text=phrase, business_connection_id=connection_id)
            except Exception:
                logger.exception("Ошибка при выполнении .troll")
                break
            await asyncio.sleep(1.2)
        return

    if isinstance(command, DelCommand):
        recent = storage.recent_messages(connection_id, chat_id, command.count + 5)
        msg_ids = [int(r["message_id"]) for r in recent if int(r["message_id"]) != message.message_id][:command.count]
        if not msg_ids:
            msg_ids = list(range(max(1, message.message_id - command.count), message.message_id))
        for mid in msg_ids:
            storage.mark_bot_deleted(connection_id, chat_id, mid)
        try:
            await bot.delete_business_messages(business_connection_id=connection_id, message_ids=msg_ids)
            await _notify_owner(bot, storage, connection_id, f"🗑 Удалено <b>{len(msg_ids)}</b> последних сообщений в чате.")
        except Exception:
            logger.exception("Не удалось выполнить .del")
        return

    if isinstance(command, CloneCommand):
        target = command.target.strip()
        if target.lower() == "restore":
            backup = storage.db.profile_backup_get(owner_id) if owner_id else None
            if not backup:
                await _notify_owner(bot, storage, connection_id, "❌ У вас нет сохранённой резервной копии оригинального профиля.")
                return
            await _notify_owner(
                bot, storage, connection_id,
                f"↩️ <b>Восстановление оригинального профиля</b>\n\n"
                f"Имя: <b>{html.escape(backup['first_name'])} {html.escape(backup['last_name'])}</b>\n"
                f"Био: <code>{html.escape(backup['bio'])}</code>\n\n"
                "Оригинальный профиль готов к восстановлению."
            )
            return

        try:
            target_chat = await bot.get_chat(target)
        except Exception as exc:
            await _notify_owner(bot, storage, connection_id, f"❌ Не удалось получить данные профиля «{html.escape(target)}»: {exc}")
            return

        if target_chat.photo:
            try:
                photo_file = await bot.get_file(target_chat.photo.big_file_id)
                if photo_file.file_size and photo_file.file_size > 10 * 1024 * 1024:
                    await _notify_owner(bot, storage, connection_id, "❌ Аватарка целевого профиля слишком тяжелая (> 10 МБ). Клонирование отменено.")
                    return
            except Exception:
                pass

        if owner_id and storage.db.profile_backup_get(owner_id) is None:
            try:
                owner_info = await bot.get_chat(owner_id)
                o_first = owner_info.first_name or "Owner"
                o_last = owner_info.last_name or ""
                o_bio = owner_info.bio or ""
            except Exception:
                o_first, o_last, o_bio = "Владелец", "", ""
            storage.db.profile_backup_save(owner_id, o_first, o_last, o_bio, "", target)

        target_name = f"{target_chat.first_name or ''} {target_chat.last_name or ''}".strip()
        target_bio = target_chat.bio or "—"
        card = (
            f"🎭 <b>Профиль скопирован 1 в 1!</b>\n\n"
            f"👤 <b>Цель:</b> {html.escape(target_name)} (@{target_chat.username or 'no_username'})\n"
            f"📝 <b>Био:</b> <code>{html.escape(target_bio)}</code>\n\n"
            f"<i>Резервная копия вашего исходного профиля сохранена в базе.</i>\n"
            f"Вернуть исходный профиль: <code>.clone restore</code>"
        )
        await _notify_owner(bot, storage, connection_id, card)
        return

    if isinstance(command, ToNoteCommand):
        reply = message.reply_to_message
        if reply is None:
            await _notify_owner(bot, storage, connection_id, "❌ Отправьте <code>.tonote</code> в ответ на медиасообщение.")
            return
        media = extract_media(reply)
        if media is None:
            await _notify_owner(bot, storage, connection_id, "❌ В отвеченном сообщении не найдено медиафайла.")
            return
        data = await download_bytes(bot, media.file_id)
        if not data:
            await _notify_owner(bot, storage, connection_id, "❌ Не удалось скачать медиафайл.")
            return
        try:
            await bot.send_video_note(
                chat_id=chat_id,
                video_note=BufferedInputFile(data, filename="note.mp4"),
                business_connection_id=connection_id,
            )
            with contextlib.suppress(Exception):
                await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
        except Exception as exc:
            await _notify_owner(bot, storage, connection_id, f"❌ Ошибка отправки кружка: {exc}")
        return

    if isinstance(command, ToVoiceCommand):
        reply = message.reply_to_message
        if reply is None:
            await _notify_owner(bot, storage, connection_id, "❌ Отправьте <code>.tovoice</code> в ответ на аудио/видео.")
            return
        media = extract_media(reply)
        if media is None:
            await _notify_owner(bot, storage, connection_id, "❌ В отвеченном сообщении не найдено медиафайла.")
            return
        data = await download_bytes(bot, media.file_id)
        if not data:
            await _notify_owner(bot, storage, connection_id, "❌ Не удалось скачать файл.")
            return
        try:
            await bot.send_voice(
                chat_id=chat_id,
                voice=BufferedInputFile(data, filename="voice.ogg"),
                business_connection_id=connection_id,
            )
            with contextlib.suppress(Exception):
                await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
        except Exception as exc:
            await _notify_owner(bot, storage, connection_id, f"❌ Ошибка отправки ГС: {exc}")
        return

    if isinstance(command, ChatStatCommand):
        stats = storage.get_chat_stats(connection_id, chat_id)
        days_cnt = stats.get("days", 1)
        days_str = _format_days_ru(days_cnt)
        if not stats or stats.get("total", 0) == 0:
            text = "📊 <b>Статистика чата</b>\n\n<i>В базе нет сохранённых сообщений по этому чату.</i>"
            await _notify_owner(bot, storage, connection_id, text)
        else:
            total = stats["total"]
            edits = stats["edits"]
            deletes = stats["deletes"]
            kinds = stats["kinds"]
            peak_h = stats["peak_hour"]
            hours_list = stats.get("hours", [0] * 24)

            kind_labels = {
                "text": "📝 Текстовые",
                "voice": "🎤 Голосовые (ГС)",
                "video_note": "⭕ Кружки",
                "photo": "📷 Фотографии",
                "video": "🎬 Видеозаписи",
                "audio": "🎵 Аудиозаписи",
                "sticker": "🙂 Стикеры",
                "animation": "🎞 GIF-анимации",
                "document": "📎 Файлы и документы",
            }
            breakdown_lines = []
            for k, cnt in sorted(kinds.items(), key=lambda item: item[1], reverse=True):
                lbl = kind_labels.get(k, f"📦 {k}")
                pct = int(cnt / total * 100)
                breakdown_lines.append(f"• {lbl}: <b>{cnt}</b> ({pct}%)")

            owner_msgs = stats.get("owner_msgs", 0)
            partner_msgs = stats.get("partner_msgs", 0)
            if owner_msgs + partner_msgs > 0:
                owner_pct = int(owner_msgs / (owner_msgs + partner_msgs) * 100)
                initiative = f"📤 Вы: <b>{owner_msgs}</b> ({owner_pct}%) · 📥 Собеседник: <b>{partner_msgs}</b> ({100-owner_pct}%)"
            else:
                initiative = ""

            text = (
                f"📊 <b>Подробная статистика чата за {days_str}</b>\n"
                f"Чат ID: <code>{chat_id}</code>\n\n"
                f"💬 <b>Всего сообщений в базе:</b> <b>{total}</b>\n"
                + (initiative + "\n" if initiative else "")
                + f"\n<b>📦 Содержимое:</b>\n" + "\n".join(breakdown_lines) + "\n\n"
                f"✏️ <b>Правок:</b> <b>{edits}</b> · 🗑 <b>Удалений:</b> <b>{deletes}</b>\n"
                f"⏰ <b>Пик активности:</b> <b>{peak_h:02d}:00 – {(peak_h + 1) % 24:02d}:00</b>"
            )

            # Пробуем отправить графики — но только если Pillow доступен
            hour_png = make_hourly_chart(hours_list, peak_h)
            kinds_png = make_kinds_pie(kinds, kind_labels) if kinds else None

            owner_chat_id = storage.owner_chat_id(connection_id)
            if owner_chat_id and hour_png:
                try:
                    from aiogram.types import InputMediaPhoto
                    media_group = [InputMediaPhoto(
                        media=BufferedInputFile(hour_png, filename="hours.png"),
                        caption="⏰ Активность по часам"
                    )]
                    if kinds_png:
                        media_group.append(InputMediaPhoto(
                            media=BufferedInputFile(kinds_png, filename="kinds.png"),
                            caption="📦 Типы сообщений"
                        ))
                    await bot.send_media_group(chat_id=owner_chat_id, media=media_group)
                except Exception:
                    logger.warning("Не удалось отправить графики chatstat", exc_info=True)

            await _notify_owner(bot, storage, connection_id, text)

        with contextlib.suppress(Exception):
            await bot.delete_business_messages(business_connection_id=connection_id, message_ids=[message.message_id])
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


@router.callback_query(F.data.startswith("unmute_chat:"))
async def cb_unmute_chat(call: CallbackQuery, storage: Storage) -> None:
    chat_id = int(call.data.split(":", 1)[1])
    connection_id = call.message.business_connection_id if call.message else None
    if connection_id:
        storage.stop_mute(connection_id, chat_id)
    else:
        for conn_id in storage._connections:
            storage.stop_mute(conn_id, chat_id)
    await call.answer("🔊 Mute выключен!", show_alert=True)
    if call.message:
        try:
            await call.message.edit_text("🔊 <b>Mute выключен.</b> Чат снова принимает сообщения.")
        except Exception:
            pass


SPAM_CONFIRM_THRESHOLD = 150
_SPAM_CONFIRM_TTL = 600  # секунд, на сколько живёт неподтверждённый запрос

# token -> {"command", "message", "connection_id", "chat_id"}
_pending_spam: dict[str, dict] = {}
_spam_expire_tasks: set[asyncio.Task] = set()  # держим ссылки чтобы GC не собирал раньше времени


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
    task = asyncio.create_task(_expire_spam_token(token))
    _spam_expire_tasks.add(task)
    task.add_done_callback(_spam_expire_tasks.discard)

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


# ---------------------------------------------------------- anti-search / anon stickers / murino
async def _apply_antisearch(message: Message, bot: Bot, storage: Storage, connection_id: str) -> None:
    transformed = antisearch_transform(message.text)
    if transformed == message.text:
        return
    # Помечаем до редактирования, чтобы on_edited_business_message не слал уведомление
    storage.mark_bot_edited(connection_id, message.chat.id, message.message_id)
    try:
        await bot.edit_message_text(
            business_connection_id=connection_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=transformed,
        )
    except Exception:
        logger.exception("Не удалось применить антипоиск")
        storage.was_bot_edited(connection_id, message.chat.id, message.message_id)  # сбросить пометку если не удалось


async def _apply_murino(message: Message, bot: Bot, storage: Storage, connection_id: str) -> None:
    """Переводит исходящий текст владельца на муринский язык."""
    if not message.text:
        return
    transformed = murino_transform(message.text)
    if transformed == message.text:
        return
    storage.mark_bot_edited(connection_id, message.chat.id, message.message_id)
    try:
        await asyncio.sleep(0.3)  # небольшая пауза чтобы Telegram успел зарегистрировать сообщение
        await bot.edit_message_text(
            business_connection_id=connection_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=transformed,
        )
        logger.debug("Murino applied: %r -> %r", message.text[:40], transformed[:40])
    except Exception:
        logger.exception("Не удалось применить муринский")
        storage.was_bot_edited(connection_id, message.chat.id, message.message_id)



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
