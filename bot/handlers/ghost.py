from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot import ghost
from bot.keyboards import ghost_picker_keyboard, ghost_session_keyboard
from bot.media import extract_media, send_media_copy, send_media_to_chat
from bot.storage import Storage, describe_message

logger = logging.getLogger(__name__)

router = Router(name="ghost")

_HISTORY_TAIL = 10


# --------------------------------------------------------------------- /link
@router.message(Command("link"))
async def cmd_link(message: Message, storage: Storage) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/link КОД</code> — код выдаётся владельцем в "
            "/settings → 👻 Режим призрака → «Привязать второй аккаунт»."
        )
        return

    operator_id = message.from_user.id
    wait = ghost.is_locked_out(operator_id)
    if wait:
        await message.answer(f"⏳ Слишком много неверных попыток. Попробуйте снова через {int(wait // 60) + 1} мин.")
        return

    owner_id = ghost.try_link(storage, operator_id, message.chat.id, parts[1].strip())
    if owner_id is None:
        await message.answer("❌ Код неверный или устарел.")
        return

    await message.answer("✅ Аккаунт привязан. Отправьте /ghost, чтобы выбрать чат.")

    connections = storage.connections_for_owner(owner_id)
    if connections:
        owner_chat_id = storage.owner_chat_id(connections[0])
        if owner_chat_id:
            operator_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
            try:
                await message.bot.send_message(
                    owner_chat_id,
                    f"🔓 К режиму призрака привязан новый аккаунт: {html.escape(operator_name)} "
                    f"(id <code>{operator_id}</code>).\nОтвязать можно в /settings → 👻 Режим призрака.",
                )
            except Exception:
                logger.exception("Не удалось уведомить владельца о новой привязке")


# -------------------------------------------------------------------- /ghost
@router.message(Command("ghost"))
async def cmd_ghost(message: Message, storage: Storage) -> None:
    scope = ghost.resolve_operator_scope(storage, message.from_user.id)
    if scope is None:
        await message.answer(
            "❌ Режим призрака недоступен: либо он выключен владельцем, либо у вас нет привязки. "
            "Получите код у владельца в /settings → 👻 Режим призрака и отправьте <code>/link КОД</code>."
        )
        return
    owner_id, connection_id = scope
    ghost.close_session(message.from_user.id)
    await _send_picker(message, storage, owner_id, connection_id)


async def _send_picker(message: Message, storage: Storage, owner_id: int, connection_id: str) -> None:
    chats = storage.chats_with_unread(connection_id, owner_id)
    pinned_ids = storage.pinned_chat_ids(owner_id, connection_id)
    enriched = [(cid, title, total, unread, cid in pinned_ids) for cid, title, total, unread in chats]
    enriched.sort(key=lambda item: (not item[4], -item[2]))

    if not enriched:
        await message.answer(
            "Пока нет ни одного чата с сохранённой историей — просто дождитесь новых сообщений, "
            "бот сам начнёт копить историю для режима призрака."
        )
        return

    await message.answer("<b>👻 Выберите чат</b>", reply_markup=ghost_picker_keyboard(enriched))


@router.callback_query(F.data == "gh:list")
async def gh_list(call: CallbackQuery, storage: Storage) -> None:
    scope = ghost.resolve_operator_scope(storage, call.from_user.id)
    if scope is None:
        await call.answer("Доступ закрыт", show_alert=True)
        return
    owner_id, connection_id = scope
    ghost.close_session(call.from_user.id)
    await call.answer()
    await _send_picker(call.message, storage, owner_id, connection_id)


@router.callback_query(F.data == "gh:search")
async def gh_search(call: CallbackQuery, storage: Storage) -> None:
    scope = ghost.resolve_operator_scope(storage, call.from_user.id)
    if scope is None:
        await call.answer("Доступ закрыт", show_alert=True)
        return
    owner_id, connection_id = scope
    ghost.set_search_pending(call.from_user.id, owner_id, connection_id)
    await call.answer()
    await call.message.answer("Введите часть названия чата для поиска:")


@router.callback_query(F.data.startswith("gh:open:"))
async def gh_open(call: CallbackQuery, storage: Storage) -> None:
    scope = ghost.resolve_operator_scope(storage, call.from_user.id)
    if scope is None:
        await call.answer("Доступ закрыт", show_alert=True)
        return
    owner_id, connection_id = scope
    chat_id = int(call.data.split(":", 2)[2])
    await call.answer()
    await open_chat(call.message, storage, call.from_user.id, owner_id, connection_id, chat_id)


@router.callback_query(F.data.startswith("gh:read:"))
async def gh_read(call: CallbackQuery, storage: Storage) -> None:
    session = ghost.get_session(call.from_user.id)
    if session is None:
        await call.answer("Сессия закрыта, откройте /ghost заново", show_alert=True)
        return
    chat_id = int(call.data.split(":", 2)[2])
    unread = storage.unread_messages(session.connection_id, chat_id, session.owner_id)
    ids = [int(row["message_id"]) for row in unread]
    storage.mark_read(session.connection_id, chat_id, ids)

    for row in unread:
        try:
            await call.bot.read_business_message(
                business_connection_id=session.connection_id, chat_id=chat_id, message_id=int(row["message_id"])
            )
        except Exception:
            logger.debug("read_business_message failed", exc_info=True)

    await call.answer(f"Прочитано: {len(ids)}")
    await call.message.edit_reply_markup(
        reply_markup=ghost_session_keyboard(
            chat_id, pinned=storage.is_pinned(session.owner_id, session.connection_id, chat_id), has_unread=False
        )
    )


@router.callback_query(F.data.startswith("gh:pin:"))
async def gh_pin(call: CallbackQuery, storage: Storage) -> None:
    session = ghost.get_session(call.from_user.id)
    if session is None:
        await call.answer("Сессия закрыта, откройте /ghost заново", show_alert=True)
        return
    chat_id = int(call.data.split(":", 2)[2])
    if storage.is_pinned(session.owner_id, session.connection_id, chat_id):
        storage.pin_remove(session.owner_id, session.connection_id, chat_id)
        pinned_now = False
        await call.answer("Откреплено")
    else:
        storage.pin_add(session.owner_id, session.connection_id, chat_id)
        pinned_now = True
        await call.answer("Закреплено")

    unread = storage.unread_messages(session.connection_id, chat_id, session.owner_id)
    await call.message.edit_reply_markup(
        reply_markup=ghost_session_keyboard(chat_id, pinned=pinned_now, has_unread=bool(unread))
    )


# ---------------------------------------------------------- shared logic (called from service.py)
async def open_chat(message: Message, storage: Storage, operator_id: int, owner_id: int, connection_id: str, chat_id: int) -> None:
    rows_by_id: dict[int, object] = {}
    for row in storage.recent_messages(connection_id, chat_id, _HISTORY_TAIL):
        rows_by_id[int(row["message_id"])] = row
    unread_rows = storage.unread_messages(connection_id, chat_id, owner_id)
    for row in unread_rows:
        rows_by_id[int(row["message_id"])] = row

    rows = sorted(rows_by_id.values(), key=lambda r: r["cached_at"])
    chat_title = rows[0]["chat_title"] if rows else str(chat_id)

    ghost.open_session(operator_id, owner_id, connection_id, chat_id, chat_title)

    if not rows:
        await message.answer(f"<b>👻 {html.escape(str(chat_title))}</b>\nИстория пуста. Пишите — сообщения уйдут в чат.")
    else:
        text = _format_history(chat_title, rows, owner_id)
        await message.answer(text)

    pinned = storage.is_pinned(owner_id, connection_id, chat_id)
    await message.answer(
        "Управление чатом:", reply_markup=ghost_session_keyboard(chat_id, pinned=pinned, has_unread=bool(unread_rows))
    )


def _format_history(chat_title, rows, owner_id: int) -> str:
    lines = [f"<b>👻 {html.escape(str(chat_title))}</b>\n"]
    for row in rows:
        is_owner = row["from_user_id"] == owner_id
        marker = "🟢 Вы" if is_owner else "🔵 Собеседник"
        unread_marker = " 🆕" if row["read_at"] is None and not is_owner else ""
        content = html.escape(row["content"] or "")
        lines.append(f"<b>{marker}{unread_marker}:</b> {content}")
    return "\n".join(lines)


async def handle_search_input(message: Message, storage: Storage) -> bool:
    """Возвращает True, если сообщение обработано как поисковый запрос."""
    state = ghost.pop_search_pending(message.from_user.id)
    if state is None:
        return False

    query = (message.text or "").strip().lower()
    chats = storage.chats_with_unread(state["connection_id"], state["owner_id"])
    pinned_ids = storage.pinned_chat_ids(state["owner_id"], state["connection_id"])
    matches = [
        (cid, title, total, unread, cid in pinned_ids)
        for cid, title, total, unread in chats
        if query in title.lower()
    ]
    if not matches:
        await message.answer("Ничего не найдено. Отправьте /ghost, чтобы начать заново.")
        return True

    await message.answer("<b>Результаты поиска:</b>", reply_markup=ghost_picker_keyboard(matches))
    return True


async def handle_session_relay(message: Message, storage: Storage) -> bool:
    """Если у отправителя открыта сессия — пересылает сообщение в реальный чат. Возвращает True, если обработано."""
    session = ghost.get_session(message.from_user.id)
    if session is None:
        return False

    media = extract_media(message)
    text = message.text or message.caption

    try:
        if media is not None:
            await send_media_to_chat(
                message.bot, session.connection_id, session.chat_id, media,
                caption=text if media.kind != "sticker" else None,
            )
        elif text:
            await message.bot.send_message(chat_id=session.chat_id, text=text, business_connection_id=session.connection_id)
        else:
            return True
    except Exception:
        logger.exception("Не удалось переслать сообщение из режима призрака")
        await message.answer("❌ Не удалось отправить сообщение в чат.")
        return True

    await message.answer("✅ отправлено", disable_notification=True)
    return True


async def relay_live_message(bot, storage: Storage, connection_id: str, chat_id: int, message: Message) -> None:
    """Живая трансляция входящего сообщения собеседника всем, у кого сейчас открыт этот чат."""
    watchers = ghost.sessions_watching(connection_id, chat_id)
    if not watchers:
        return
    content = describe_message(message)
    text = f"🔵 <b>Собеседник:</b> {html.escape(content)}"
    media = extract_media(message)
    for operator_id in watchers:
        try:
            if media is not None:
                await send_media_copy(bot, operator_id, media, caption=text)
            else:
                await bot.send_message(chat_id=operator_id, text=text)
        except Exception:
            logger.exception("Не удалось транслировать сообщение в открытую ghost-сессию")
