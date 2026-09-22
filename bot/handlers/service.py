from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Message,
)

from bot.backup import BackupManager
from bot.media import (
    MEDIA_DIR,
    MediaRef,
    compute_media_hash,
    directory_size_bytes,
    download_bytes,
    enforce_media_age,
    enforce_media_quota,
    extract_media,
    send_media_copy,
)
from bot import ghost, subscription
from bot.handlers import billing as billing_handlers
from bot.handlers import ghost as ghost_handlers
from bot.features.chat_export import build_export_html, build_export_json
from bot.keyboards import (
    admin_back_keyboard,
    admin_main_keyboard,
    admin_section_keyboard,
    admin_user_detail_keyboard,
    admin_users_keyboard,
    admin_whitelist_keyboard,
    chat_action_picker_keyboard,
    chat_actions_menu_keyboard,
    chats_export_keyboard,
    chats_recent_keyboard,
    delword_pick_chat_keyboard,
    delword_scope_keyboard,
    ghost_settings_keyboard,
    help_back_keyboard,
    help_topics_keyboard,
    menu_keyboard,
    notifications_keyboard,
    online_menu_keyboard,
    persistent_menu_keyboard,
    preset_creation_keyboard,
    presets_keyboard,
    recent_count_keyboard,
    section_keyboard,
)
from bot.settings import get_admin_field, get_owner_field, next_cycle_value, parse_value
from bot.stats import format_admin_overview
from bot.storage import Storage
from bot.texts import DEFAULT_ADMIN_HINT, HELP_INTRO, HELP_TOPIC_BODIES, Texts

logger = logging.getLogger(__name__)

router = Router(name="service")

SECTION_TITLES = {
    "notif": "🔔 Уведомления",
    "extra": "🧩 Доп. функции",
    "cmds": "🛠 Команды",
    "misc": "⚙️ Прочее",
}
ADMIN_SECTION_TITLES = {
    "backup": "📦 Бэкапы",
    "cache": "📥 Кэш и медиа",
    "data": "💾 Данные",
}

# user_id -> состояние текущего диалогового шага (ввод текста/сбор пресета)
_pending: dict[int, dict] = {}


# ------------------------------------------------------------------------ /start
@router.message(CommandStart())
async def cmd_start(message: Message, storage: Storage, texts: Texts) -> None:
    is_admin = storage.is_admin(message.from_user.id)
    is_new = storage.db.ensure_owner(message.from_user.id, is_admin=is_admin)

    if is_new and not is_admin:
        for admin_id in storage._admin_ids:
            try:
                user_info = f"<b>{html.escape(message.from_user.full_name)}</b>"
                if message.from_user.username:
                    user_info += f" (@{message.from_user.username})"
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=f"🆕 <b>Новый пользователь в боте!</b>\nID: <code>{message.from_user.id}</code>\nПользователь: {user_info}"
                )
            except Exception:
                pass

    me = await message.bot.get_me()
    username = f"@{me.username}" if me.username else "имя бота из его профиля"
    text = texts.start.replace("{admin_hint}", "").replace("{bot_username}", username)
    try:
        await message.bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=MenuButtonCommands())
    except Exception:
        pass
    await message.answer(text, reply_markup=persistent_menu_keyboard())


# ------------------------------------------------------------------------- /help
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_INTRO, reply_markup=help_topics_keyboard())


@router.callback_query(F.data.startswith("help:topic:"))
async def help_topic(call: CallbackQuery) -> None:
    key = call.data.split(":", 2)[2]
    body = HELP_TOPIC_BODIES.get(key, "Пока нет описания для этой темы.")
    await call.message.edit_text(body, reply_markup=help_back_keyboard())
    await call.answer()


@router.callback_query(F.data == "help:back")
async def help_back(call: CallbackQuery) -> None:
    await call.message.edit_text(HELP_INTRO, reply_markup=help_topics_keyboard())
    await call.answer()


@router.callback_query(F.data == "help:close")
async def help_close(call: CallbackQuery) -> None:
    if call.message:
        await call.message.delete()
    await call.answer()


# ---------------------------------------------------------------------- /menu
@router.message(F.text == "📱 Меню")
@router.message(Command("menu"))
async def cmd_menu(message: Message, storage: Storage) -> None:
    owner_id = message.from_user.id
    storage.db.ensure_owner(owner_id, is_admin=storage.is_admin(owner_id))
    connections = storage.connections_for_owner(owner_id)
    is_connected = bool(connections)
    status_icon = "🟢" if is_connected else "🔴"
    status_text = "Подключён" if is_connected else "Не подключён"

    text = (
        f"<b>🤖 AyuAutoBot — Меню</b>\n\n"
        f"🔗 <b>Статус:</b> {status_icon} {status_text}\n\n"
        "Выберите необходимый раздел:"
    )

    try:
        await message.bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=MenuButtonCommands())
    except Exception:
        pass
    await message.answer(text, reply_markup=menu_keyboard())



@router.callback_query(F.data == "us:close")
async def us_close(call: CallbackQuery) -> None:
    if call.message:
        await call.message.delete()
    await call.answer()


@router.callback_query(F.data == "us:back")
async def us_back(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    connections = storage.connections_for_owner(owner_id)
    is_connected = bool(connections)
    status_icon = "🟢" if is_connected else "🔴"
    status_text = "Подключён" if is_connected else "Не подключён"

    text = (
        f"<b>🤖 AyuAutoBot — Меню</b>\n\n"
        f"🔗 <b>Статус:</b> {status_icon} {status_text}\n\n"
        "Выберите необходимый раздел:"
    )
    await call.message.edit_text(text, reply_markup=menu_keyboard())
    await call.answer()


@router.callback_query(F.data == "us:noop")
async def us_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.in_({"us:open:notif", "us:open:extra", "us:open:cmds", "us:open:misc", "us:open:presets", "us:open:ghost"}))
async def us_open(call: CallbackQuery, storage: Storage) -> None:
    section = call.data.split(":", 2)[2]
    owner_id = call.from_user.id

    if section == "notif":
        settings = storage.get_settings(owner_id)
        digest_count = storage.queue_count(owner_id)
        await call.message.edit_text(
            "<b>🔔 Настройки уведомлений</b>\nНастройте получение сообщений об удалениях и редактированиях:",
            reply_markup=notifications_keyboard(settings, digest_count),
        )
        await call.answer()
        return

    if section == "presets":
        names = storage.preset_list(owner_id)
        text = "<b>🗂 Пресеты быстрых ответов (.say)</b>\n\n" + (
            "Список созданных пресетов:" if names else "У вас пока нет сохранённых пресетов."
        )
        await call.message.edit_text(text, reply_markup=presets_keyboard(names))
        await call.answer()
        return

    if section == "ghost":
        await call.message.edit_text(_ghost_settings_text(storage, owner_id), reply_markup=_ghost_settings_kb(storage, owner_id))
        await call.answer()
        return

    settings = storage.get_settings(owner_id)
    title = SECTION_TITLES.get(section, section)
    await call.message.edit_text(f"<b>{title}</b>", reply_markup=section_keyboard(section, settings))
    await call.answer()


@router.callback_query(F.data.startswith("us:toggle:"))
async def us_toggle(call: CallbackQuery, storage: Storage) -> None:
    try:
        _, _, section, key = call.data.split(":", 3)
        settings = storage.toggle_setting(call.from_user.id, key)
        with contextlib.suppress(Exception):
            if section == "notif":
                digest_count = storage.queue_count(call.from_user.id)
                await call.message.edit_reply_markup(reply_markup=notifications_keyboard(settings, digest_count))
            else:
                await call.message.edit_reply_markup(reply_markup=section_keyboard(section, settings))
    finally:
        with contextlib.suppress(Exception):
            await call.answer("Сохранено")


@router.callback_query(F.data.startswith("us:cycle:"))
async def us_cycle(call: CallbackQuery, storage: Storage) -> None:
    try:
        _, _, section, key = call.data.split(":", 3)
        field = get_owner_field(key)
        if field is None:
            await call.answer("Неизвестная настройка", show_alert=True)
            return
        current_settings = storage.get_settings(call.from_user.id)
        new_value = next_cycle_value(field, getattr(current_settings, key))
        settings = storage.update_setting(call.from_user.id, key, new_value)
        with contextlib.suppress(Exception):
            if section == "notif":
                digest_count = storage.queue_count(call.from_user.id)
                await call.message.edit_reply_markup(reply_markup=notifications_keyboard(settings, digest_count))
            else:
                await call.message.edit_reply_markup(reply_markup=section_keyboard(section, settings))
    finally:
        with contextlib.suppress(Exception):
            await call.answer("Сохранено")


@router.callback_query(F.data.startswith("us:edit:"))
async def us_edit(call: CallbackQuery) -> None:
    _, _, section, key = call.data.split(":", 3)
    field = get_owner_field(key)
    if field is None:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    _pending[call.from_user.id] = {"kind": "edit_user", "section": section, "key": key, "created_at": time.time()}
    await call.answer()
    await call.message.answer(f"Введите новое значение для «{field.label}» одним сообщением:")


@router.callback_query(F.data == "us:afktext")
async def us_afktext(call: CallbackQuery) -> None:
    _pending[call.from_user.id] = {"kind": "afk_text", "created_at": time.time()}
    await call.answer()
    await call.message.answer("Отправьте текст автоответа для AFK-режима одним сообщением:")


@router.callback_query(F.data == "us:digest")
async def us_digest(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    rows = storage.queue_list(owner_id)
    if not rows:
        await call.answer("Очередь пуста", show_alert=True)
        return
    await call.answer()
    for row in rows:
        media = None
        if row["media_kind"] and row["media_file_id"]:
            local_path = Path(row["media_path"]) if row["media_path"] else None
            media = MediaRef(
                kind=row["media_kind"],
                file_id=row["media_file_id"],
                local_path=local_path if local_path and local_path.exists() else None,
            )
        if media is not None:
            await send_media_copy(call.bot, call.message.chat.id, media, caption=row["payload"])
        else:
            await call.bot.send_message(call.message.chat.id, row["payload"])
    storage.queue_clear(owner_id)
    digest_count = storage.queue_count(owner_id)
    settings = storage.get_settings(owner_id)
    await call.message.edit_text(
        "<b>🔔 Уведомления</b>", reply_markup=notifications_keyboard(settings, digest_count)
    )


@router.callback_query(F.data == "us:export")
async def us_export(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    connection_ids = storage.connections_for_owner(owner_id)
    chats: list[tuple[int, str, int]] = []
    for connection_id in connection_ids:
        chats.extend(storage.chats_for_connection(connection_id))
    chats.sort(key=lambda item: item[2], reverse=True)

    if not chats:
        await call.answer(
            "Пока нет сохранённых сообщений — история появится сама по мере переписки.",
            show_alert=True,
        )
        return

    await call.message.edit_text(
        "<b>📤 Экспорт последней истории</b>\nЭто не полный архив чата, а то, что бот успел сохранить "
        "локально (старое может быть уже вытеснено бэкапом/очисткой).\nВыберите чат "
        "(в скобках — число сохранённых сообщений):",
        reply_markup=chats_export_keyboard(chats),
    )
    await call.answer()


@router.callback_query(F.data.startswith("us:export:chat:"))
async def us_export_chat(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    chat_id = int(call.data.split(":", 3)[3])
    connection_ids = storage.connections_for_owner(owner_id)

    rows = []
    chat_title = str(chat_id)
    used_connection_id = None
    for connection_id in connection_ids:
        candidate = storage.messages_for_chat(connection_id, chat_id)
        if candidate:
            rows = candidate
            used_connection_id = connection_id
            for cid, title, _count in storage.chats_for_connection(connection_id):
                if cid == chat_id:
                    chat_title = title
            break

    if not rows or used_connection_id is None:
        await call.answer("Нет сохранённых сообщений для этого чата.", show_alert=True)
        return

    await call.answer("Готовлю файлы…")

    safe_name = _safe_filename(chat_title)
    export_json = build_export_json(chat_title, chat_id, rows)
    export_html = build_export_html(chat_title, owner_id, rows)

    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = Path(tmp_dir) / "export.json"
        html_path = Path(tmp_dir) / "export.html"
        json_path.write_text(json.dumps(export_json, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(export_html, encoding="utf-8")

        await call.message.answer_document(
            FSInputFile(json_path, filename=f"{safe_name}.json"),
            caption=f"📤 Экспорт «{chat_title}» — JSON ({len(rows)} сообщ.)",
        )
        await call.message.answer_document(
            FSInputFile(html_path, filename=f"{safe_name}.html"),
            caption=(
                "HTML-версия — откройте в браузере. Если нужен PDF, откройте файл в браузере "
                "и нажмите «Печать → Сохранить как PDF» (Ctrl+P)."
            ),
        )


@router.callback_query(F.data == "us:search")
async def us_search(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.record_feature_usage("search_db")
    _pending[owner_id] = {"kind": "db_search", "created_at": time.time()}
    await call.answer()
    await call.message.answer(
        "🔍 <b>Умный поиск по базе сообщений</b>\n\n"
        "Вы можете отправить:\n"
        "• <b>Текст, фразу или регулярку</b> — бот сам определит регулярное выражение или текст\n"
        "• <b>ID</b> сообщения или чата (например: <code>#123</code> или <code>id:123</code>)\n"
        "• <b>Медиафайл</b> (фото, голосовое, кружок, видео, аудио, документ) — бот найдёт все совпадения по полезному контенту (даже если формат или метаданные изменились)!"
    )


def _safe_filename(name: str) -> str:

    cleaned = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
    return cleaned[:60] or "chat"


@router.callback_query(F.data == "us:recent")
async def us_recent(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.record_feature_usage("recent_messages")
    chats = storage.recent_chats(owner_id)

    if not chats:
        await call.answer(
            "Пока нет сохранённых сообщений — история появится сама по мере переписки.",
            show_alert=True,
        )
        return

    await call.message.edit_text(
        "<b>💬 Последние сообщения</b>\nВыберите чат (новые сверху):", reply_markup=chats_recent_keyboard(chats)
    )
    await call.answer()


@router.callback_query(F.data.startswith("us:recent:chat:"))
async def us_recent_chat(call: CallbackQuery) -> None:
    chat_id = int(call.data.split(":", 3)[3])
    await call.message.edit_text(
        "Сколько последних сообщений показать?", reply_markup=recent_count_keyboard(chat_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("us:recent:show:"))
async def us_recent_show(call: CallbackQuery, storage: Storage) -> None:
    _, _, _, chat_id_raw, n_raw = call.data.split(":", 4)
    chat_id = int(chat_id_raw)
    limit = int(n_raw)
    owner_id = call.from_user.id
    connection_ids = storage.connections_for_owner(owner_id)

    rows = []
    chat_title = str(chat_id)
    for connection_id in connection_ids:
        candidate = storage.recent_messages(connection_id, chat_id, limit)
        if candidate:
            rows = candidate
            for cid, title, _count in storage.chats_for_connection(connection_id):
                if cid == chat_id:
                    chat_title = title
            break

    if not rows:
        await call.answer("Нет сохранённых сообщений.", show_alert=True)
        return

    await call.answer()
    for chunk in _format_recent_messages(chat_title, rows):
        await call.message.answer(chunk)


def _format_recent_messages(chat_title: str, rows) -> list[str]:
    header = f"<b>👁‍🗨 {chat_title} — последние {len(rows)} сообщ.</b>\n\n"
    lines = []
    for row in rows:
        ts = time.strftime("%d.%m %H:%M", time.localtime(row["cached_at"]))
        sender = html.escape(row["from_user_name"] or "?")
        content = html.escape(row["content"] or "")
        suffix = " 🗑" if row["deleted_at"] else (" ✏️" if row["edited_at"] else "")
        lines.append(f"<b>{sender}</b> <i>{ts}</i>{suffix}\n{content}")

    chunks: list[str] = []
    current = header
    for line in lines:
        candidate = current + line + "\n\n"
        if len(candidate) > 3500:
            chunks.append(current)
            current = line + "\n\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks


def _ghost_settings_text(storage: Storage, owner_id: int) -> str:
    settings = storage.get_settings(owner_id)
    if not settings.ghost_mode_enabled:
        return (
            "<b>👻 Режим призрака</b>\n\n"
            "Позволяет читать и писать в чаты собеседников через бота — со своего "
            "аккаунта или с привязанного второго, без захода в само приложение "
            "Telegram. Сейчас выключен."
        )
    operators = storage.ghost_operators_for_owner(owner_id)
    lines = [
        "<b>👻 Режим призрака</b>\n",
        "Откройте /ghost в этом чате (или с привязанного аккаунта), чтобы выбрать чат.",
    ]
    if operators:
        lines.append(f"\nПривязанных аккаунтов: <b>{len(operators)}</b>")
    return "\n".join(lines)


def _ghost_settings_kb(storage: Storage, owner_id: int):
    settings = storage.get_settings(owner_id)
    operators = storage.ghost_operators_for_owner(owner_id)
    return ghost_settings_keyboard(settings.ghost_mode_enabled, operators)


@router.callback_query(F.data == "gs:toggle")
async def gs_toggle(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.toggle_setting(owner_id, "ghost_mode_enabled")
    await call.message.edit_text(_ghost_settings_text(storage, owner_id), reply_markup=_ghost_settings_kb(storage, owner_id))
    await call.answer("Сохранено")


@router.callback_query(F.data == "gs:gencode")
async def gs_gencode(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    code = ghost.generate_link_code(storage, owner_id)
    minutes = ghost.CODE_TTL_SECONDS // 60
    await call.answer()
    await call.message.answer(
        f"🔑 Код привязки: <code>{code}</code>\n\n"
        f"Действует {minutes} минут, одноразовый. Со <b>второго аккаунта</b> откройте чат с этим "
        f"ботом, нажмите /start, затем отправьте:\n<code>/link {code}</code>",
    )


@router.callback_query(F.data.startswith("gs:unlink:"))
async def gs_unlink(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    operator_id = int(call.data.split(":", 2)[2])
    link = storage.ghost_operator_owner(operator_id)
    if link is None or int(link["owner_id"]) != owner_id:
        await call.answer("Не найдено", show_alert=True)
        return
    storage.ghost_link_remove(operator_id)
    try:
        await call.bot.send_message(int(link["operator_chat_id"]), "🔌 Доступ к режиму призрака отозван владельцем.")
    except Exception:
        logger.exception("Не удалось уведомить оператора об отвязке")
    await call.message.edit_text(_ghost_settings_text(storage, owner_id), reply_markup=_ghost_settings_kb(storage, owner_id))
    await call.answer("Отвязано")


# ---------------------------------------------------------------- say presets
@router.callback_query(F.data.startswith("us:preset:del:"))
async def us_preset_del(call: CallbackQuery, storage: Storage) -> None:
    name = call.data.split(":", 3)[3]
    storage.preset_delete(call.from_user.id, name)
    names = storage.preset_list(call.from_user.id)
    await call.message.edit_reply_markup(reply_markup=presets_keyboard(names))
    await call.answer("Удалено")


@router.callback_query(F.data == "us:preset:add")
async def us_preset_add(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    _pending[owner_id] = {"kind": "preset_name", "created_at": time.time()}
    await call.answer()
    await call.message.answer("Введите имя нового пресета (одно слово, буквы/цифры/подчёркивание):")


@router.callback_query(F.data == "us:preset:done")
async def us_preset_done(call: CallbackQuery, storage: Storage) -> None:
    state = _pending.pop(call.from_user.id, None)
    if not state or state.get("kind") != "preset_items":
        await call.answer()
        return
    items = state.get("items", [])
    if items:
        storage.preset_add(call.from_user.id, state["name"], items)
        await call.message.answer(f"✅ Пресет «{state['name']}» сохранён ({len(items)} сообщ.)")
    else:
        await call.message.answer("❌ Пресет пуст, отменено.")
    await call.answer()


@router.callback_query(F.data == "us:preset:cancel")
async def us_preset_cancel(call: CallbackQuery) -> None:
    _pending.pop(call.from_user.id, None)
    await call.message.answer("Отменено.")
    await call.answer()


# ----------------------------------------------------------- online mode
async def _online_worker(bot: Bot, storage: Storage, owner_id: int, duration_sec: int | None) -> None:
    end_time = (time.time() + duration_sec) if duration_sec else None
    try:
        while True:
            if end_time and time.time() >= end_time:
                break
            conns = storage.connections_for_owner(owner_id)
            for cid in conns:
                chat_id = storage.owner_chat_id(cid)
                if chat_id:
                    with contextlib.suppress(Exception):
                        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING, business_connection_id=cid)
            await asyncio.sleep(4.5)
    except asyncio.CancelledError:
        pass
    finally:
        storage.stop_online(owner_id)


@router.callback_query(F.data == "us:open:online")
async def us_open_online(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    is_active = storage.is_online(owner_id)
    rem = storage.get_online_remaining_seconds(owner_id)
    is_admin = storage.is_admin(owner_id)
    if is_active:
        if rem is None:
            status = "🟢 <b>Онлайн-режим активен:</b> бессрочно"
        else:
            m, s = divmod(rem, 60)
            status = f"🟢 <b>Онлайн-режим активен:</b> осталось {m}м {s}с"
    else:
        status = "⚪ <b>Онлайн-режим выключен.</b>\nВыберите желаемую длительность:"

    text = (
        f"🟢 <b>Имитация Online-статуса</b>\n\n"
        f"{status}\n\n"
        f"<i>Бот поддерживает видимость вашего аккаунта «в сети» через подключение Telegram Business.</i>"
    )
    await call.message.edit_text(text, reply_markup=online_menu_keyboard(is_active, rem, is_admin))
    await call.answer()


@router.callback_query(F.data.startswith("us:online:start:"))
async def us_online_start(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    duration = int(call.data.split(":", 3)[3])
    duration_sec = duration if duration > 0 else None
    is_admin = storage.is_admin(owner_id)
    if not is_admin and duration_sec and duration_sec > 3600:
        duration_sec = 3600

    task = asyncio.create_task(_online_worker(call.bot, storage, owner_id, duration_sec))
    storage.start_online(owner_id, duration_sec, task)
    await call.answer("Онлайн-режим запущен!", show_alert=False)
    await us_open_online(call, storage)


@router.callback_query(F.data == "us:online:stop")
async def us_online_stop(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.stop_online(owner_id)
    await call.answer("Онлайн-режим остановлен")
    await us_open_online(call, storage)


@router.callback_query(F.data == "us:online:custom")
async def us_online_custom(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    is_admin = storage.is_admin(owner_id)
    limit_note = "" if is_admin else " (до 60 минут)"
    _pending[owner_id] = {"kind": "online_custom_min", "created_at": time.time()}
    await call.answer()
    await call.message.answer(
        f"⏱ <b>Онлайн-режим</b>\n\nВведите желаемое время в минутах{limit_note}:"
    )


# ----------------------------------------------------------- secret chat actions
@router.callback_query(F.data == "us:open:actions")
async def us_open_actions(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.record_feature_usage("chat_actions_menu")
    chats = storage.recent_chats(owner_id)
    text = (
        "⚡️ <b>Действия над чатом (Скрытый режим)</b>\n\n"
        "Здесь вы можете выполнять команды (Mute, спам, удаление, typing, клонирование) "
        "<b>напрямую из меню бота</b>, чтобы в чате с собеседником не мелькали точки и служебные сообщения!\n\n"
        "Выберите недавний диалог или укажите Chat ID вручную:"
    )
    chat_list = [{"chat_id": c["chat_id"], "title": c["title"]} for c in chats]
    await call.message.edit_text(text, reply_markup=chat_actions_menu_keyboard(chat_list))
    await call.answer()


@router.callback_query(F.data == "act:manual")
async def act_manual(call: CallbackQuery, storage: Storage) -> None:
    _pending[call.from_user.id] = {"kind": "act_manual_chat", "created_at": time.time()}
    await call.answer()
    await call.message.answer("Введите числовой Chat ID целевого собеседника:")


@router.callback_query(F.data.startswith("act:chat:"))
async def act_chat_pick(call: CallbackQuery, storage: Storage) -> None:
    chat_id = int(call.data.split(":", 2)[2])
    text = f"⚙️ <b>Управление чатом</b> <code>{chat_id}</code>:\n\nВыберите действие:"
    await call.message.edit_text(text, reply_markup=chat_action_picker_keyboard(chat_id))
    await call.answer()


@router.callback_query(F.data.startswith("act:do:"))
async def act_execute(call: CallbackQuery, storage: Storage) -> None:
    parts = call.data.split(":")
    action = parts[2]
    chat_id = int(parts[3])
    owner_id = call.from_user.id
    conns = storage.connections_for_owner(owner_id)
    if not conns:
        await call.answer("Нет подключённого Telegram Business", show_alert=True)
        return
    conn_id = conns[0]

    if action == "mute_perm":
        storage.start_mute(conn_id, chat_id, seconds=None)
        await call.answer("🔇 Mute включён навсегда", show_alert=True)
        return
    if action == "mute_1h":
        storage.start_mute(conn_id, chat_id, seconds=3600)
        await call.answer("🔇 Mute включён на 1 час", show_alert=True)
        return
    if action == "unmute":
        storage.stop_mute(conn_id, chat_id)
        await call.answer("🔊 Mute выключен", show_alert=True)
        return
    if action == "typing":
        await call.answer("⌨️ Отправлен статус typing")
        try:
            await call.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING, business_connection_id=conn_id)
        except Exception:
            pass
        return
    if action == "clone":
        await call.answer("👤 Клонирование…")
        try:
            from bot.handlers.business import _run_clone
            await _run_clone(call.bot, storage, conn_id, owner_id, str(chat_id))
            await call.message.answer("✅ Профиль собеседника скопирован!")
        except Exception as exc:
            await call.message.answer(f"❌ Ошибка клонирования: {exc}")
        return
    if action == "del":
        _pending[owner_id] = {"kind": "act_del_count", "chat_id": chat_id, "conn_id": conn_id, "created_at": time.time()}
        await call.answer()
        await call.message.answer("Сколько последних сообщений удалить из чата? (например 10):")
        return
    if action == "spam":
        _pending[owner_id] = {"kind": "act_spam_prompt", "chat_id": chat_id, "conn_id": conn_id, "created_at": time.time()}
        await call.answer()
        await call.message.answer("Отправьте сообщение для спама (текст или медиа):")
        return
    if action == "delword":
        _pending[owner_id] = {"kind": "act_delword_prompt", "chat_id": chat_id, "conn_id": conn_id, "created_at": time.time()}
        await call.answer()
        await call.message.answer("Введите слово, фразу или регулярку для удаления в этом чате:")
        return
    if action == "delregex":
        _pending[owner_id] = {"kind": "act_delregex_prompt", "chat_id": chat_id, "conn_id": conn_id, "created_at": time.time()}
        await call.answer()
        await call.message.answer("Введите регулярное выражение для удаления в этом чате:")
        return


@router.callback_query(F.data == "us:open:delword")
async def us_open_delword(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.record_feature_usage("delword")
    conns = storage.connections_for_owner(owner_id)
    if not conns:
        await call.answer("Нет подключённого Telegram Business", show_alert=True)
        return
    text = (
        "🗑 <b>Очистка по слову, фразе или регулярке</b>\n\n"
        "Где вы хотите выполнить поиск и удаление сообщений?\n\n"
        "• <b>🎯 Только в одном чате</b> — выбор конкретного чата из списка\n"
        "• <b>🌐 Во ВСЕХ чатах сразу</b> — массовая зачистка по всем перепискам"
    )
    await call.message.edit_text(text, reply_markup=delword_scope_keyboard())
    await call.answer()


@router.callback_query(F.data == "delword:scope:single")
async def delword_scope_single(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    storage.record_feature_usage("delword")
    chats = storage.recent_chats(owner_id)
    chat_list = [{"chat_id": c["chat_id"], "title": c["title"]} for c in chats]
    text = "🎯 <b>Выберите чат для очистки</b> или введите Chat ID вручную:"
    await call.message.edit_text(text, reply_markup=delword_pick_chat_keyboard(chat_list))
    await call.answer()


@router.callback_query(F.data == "delword:pick:manual")
async def delword_pick_manual(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    _pending[owner_id] = {"kind": "del_manual_chat", "created_at": time.time()}
    await call.answer()
    await call.message.answer("Введите числовой Chat ID целевого собеседника:")


@router.callback_query(F.data.startswith("delword:pick:"))
async def delword_pick_chat(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    chat_id = int(call.data.split(":", 2)[2])
    conns = storage.connections_for_owner(owner_id)
    conn_id = conns[0] if conns else ""
    _pending[owner_id] = {
        "kind": "act_delword_prompt",
        "chat_id": chat_id,
        "conn_id": conn_id,
        "created_at": time.time(),
    }
    await call.answer()
    await call.message.answer(
        f"🎯 <b>Очистка чата <code>{chat_id}</code></b>\n\n"
        "Введите слово, фразу или регулярку для поиска и удаления:"
    )


@router.callback_query(F.data == "delword:scope:all")
async def delword_scope_all(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    _pending[owner_id] = {
        "kind": "del_all_chats_prompt",
        "created_at": time.time(),
    }
    await call.answer()
    await call.message.answer(
        "🌐 <b>Массовое удаление во ВСЕХ чатах</b>\n\n"
        "⚠️ Сообщения будут найдены и удалены <b>во всех ваших диалогах</b>!\n\n"
        "Введите слово, фразу или регулярное выражение:"
    )


# -------------------------------------------------------------------------- /admin
def _admin_overview_text(storage: Storage, backup: BackupManager) -> str:
    settings = storage.get_global()
    s48 = storage.get_stats_48h()
    return format_admin_overview(
        owners_count=storage.db.owners_count(),
        connections_count=storage.db.connections_count(),
        db_size_mb=storage.db.file_size_bytes() / (1024 * 1024),
        db_messages=storage.db.count_messages(None),
        db_messages_total=storage.db.count_all_messages(),
        media_mb=directory_size_bytes(MEDIA_DIR) / (1024 * 1024),
        backup_enabled=settings.backup_enabled,
        backup_interval_hours=settings.backup_interval_hours,
        last_backup_ts=backup.last_backup_ts,
        total_stars=storage.payments_total_stars(),
        active_users_48h=s48["active_users"],
        msgs_48h=s48["messages"],
        media_msgs_48h=s48["media_msgs"],
        edits_48h=s48["edits"],
        deletes_48h=s48["deletes"],
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, storage: Storage, backup: BackupManager) -> None:
    if not storage.is_admin(message.from_user.id):
        return
    await message.answer(_admin_overview_text(storage, backup), reply_markup=admin_main_keyboard())


@router.callback_query(F.data == "ad:close")
async def ad_close(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    if call.message:
        await call.message.delete()
    await call.answer()


@router.callback_query(F.data == "ad:back")
async def ad_back(call: CallbackQuery, storage: Storage, backup: BackupManager) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    await call.message.edit_text(_admin_overview_text(storage, backup), reply_markup=admin_main_keyboard())
    await call.answer()


@router.callback_query(F.data == "ad:feature_stats")
async def ad_feature_stats(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return

    stats = storage.get_feature_usage_stats()
    if not stats:
        text = "📊 <b>Статистика популярности функций</b>\n\n<i>Пока нет данных об использовании функций.</i>"
    else:
        total_uses = sum(item["count"] for item in stats)
        lines = [
            "📊 <b>Популярность использования функций бота</b>\n",
            f"Всего использований: <b>{total_uses}</b>\n",
        ]

        feature_labels = {
            ".spam": ".spam (спам сообщениями)",
            ".mute": ".mute / .unmute (мут чата)",
            ".typing": ".typing (имитация набора)",
            ".del": ".del (удаление сообщений)",
            ".mock": ".mock (заборчик)",
            ".reverse": ".reverse (реверс текста)",
            ".troll": ".troll (троллинг)",
            ".tr": ".tr (переводчик)",
            ".qr": ".qr (генерация QR)",
            ".short": ".short (сокращение ссылок)",
            ".id": ".id (ID и инфо чата)",
            ".ping": ".ping (проверка отклика)",
            ".say": ".say (быстрые ответы/пресеты)",
            ".view": ".view (самоуничтожающиеся фото)",
            ".watch": ".watch (отслеживание профилей)",
            ".clone": ".clone (клонирование профиля)",
            ".tonote": ".tonote (в кружок)",
            ".tovoice": ".tovoice (в голосовое)",
            ".chatstat": ".stats / .chatstat (статистика)",
            ".stats": ".stats (статистика чата)",
            "anti_search": "🕵️ Антипоиск (подмена букв)",
            "murino_mode": "🐱 Муринский язык",
            "anon_stickers": "🎭 Анонимные стикеры",
            "afk_reply": "💤 AFK-автоответ",
            "recent_messages": "💬 Последние сообщения",
            "chat_actions_menu": "⚡️ Действия над чатом (меню)",
            "delword": "🗑 Очистка по слову/рег.",
            "export_history": "📤 Экспорт истории",
            "search_db": "🔍 Умный поиск по базе",
            "online_mode": "🟢 Онлайн-режим",
            "ghost_mode": "👻 Режим призрака",
        }

        for idx, item in enumerate(stats, 1):
            feat = item["feature"]
            cnt = item["count"]
            last = item["last_used"]
            pct = (cnt / total_uses * 100) if total_uses > 0 else 0
            label = feature_labels.get(feat, feat)

            diff_sec = max(0, time.time() - last)
            diff_min = int(diff_sec / 60)
            if diff_min < 1:
                ago = "только что"
            elif diff_min < 60:
                ago = f"{diff_min}м назад"
            elif diff_min < 1440:
                ago = f"{diff_min // 60}ч назад"
            else:
                ago = f"{diff_min // 1440}д назад"

            lines.append(f"<b>{idx}.</b> <code>{label}</code>: <b>{cnt}</b> ({pct:.1f}%) — <i>{ago}</i>")

        text = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("ad:open:"))
async def ad_open(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    section = call.data.split(":", 2)[2]

    if section == "users":
        owners = storage.all_owners_with_stats()
        if not owners:
            text = "Пока нет ни одного пользователя."
            await call.message.edit_text(text, reply_markup=admin_back_keyboard())
        else:
            text = (
                f"<b>👥 Пользователи бота ({len(owners)})</b>\n\n"
                "Нажмите на пользователя для просмотра подробной статистики и управления доступом:\n"
                "🟢 — подключён | ⚪ — не подключён | ⭐ — админ | 🔴 — бан"
            )
            await call.message.edit_text(text, reply_markup=admin_users_keyboard(owners, page=1))
        await call.answer()
        return

    if section == "whitelist":
        items = storage.whitelist_all()
        text = (
            f"<b>⚪️ Белый список (Whitelist) ({len(items)})</b>\n"
            "Пользователи и чаты в этом списке полностью игнорируются ботом "
            "(сообщения не сохраняются, Mute, AFK, антипоиск и анонимизация не применяются)."
        )
        await call.message.edit_text(text, reply_markup=admin_whitelist_keyboard(items))
        await call.answer()
        return

    settings = storage.get_global()
    title = ADMIN_SECTION_TITLES.get(section, section)
    await call.message.edit_text(f"<b>{title}</b>", reply_markup=admin_section_keyboard(section, settings))
    await call.answer()


@router.callback_query(F.data.startswith("ad:toggle:"))
async def ad_toggle(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _, _, section, key = call.data.split(":", 3)
    settings = storage.toggle_global(key)
    await call.message.edit_reply_markup(reply_markup=admin_section_keyboard(section, settings))
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith("ad:edit:"))
async def ad_edit(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _, _, section, key = call.data.split(":", 3)
    field = get_admin_field(key)
    if field is None:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    _pending[call.from_user.id] = {"kind": "edit_admin", "section": section, "key": key, "created_at": time.time()}
    await call.answer()
    await call.message.answer(f"Введите новое значение для «{field.label}» одним сообщением:")


@router.callback_query(F.data == "ad:backupnow")
async def ad_backupnow(call: CallbackQuery, storage: Storage, backup: BackupManager) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    await call.answer("Делаю бэкап…")
    ok = await backup.backup_now()
    await call.message.answer("✅ Бэкап отправлен." if ok else "❌ Не удалось выполнить бэкап (см. логи).")


@router.callback_query(F.data == "ad:broadcast")
async def ad_broadcast(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _pending[call.from_user.id] = {"kind": "broadcast", "created_at": time.time()}
    await call.answer()
    await call.message.answer("Отправьте текст рассылки одним сообщением — уйдёт всем подключённым владельцам:")


@router.callback_query(F.data == "ad:cancel")
async def ad_cancel(call: CallbackQuery) -> None:
    _pending.pop(call.from_user.id, None)
    await call.answer("Отменено")


@router.callback_query(F.data == "ad:noop")
async def ad_noop(call: CallbackQuery) -> None:
    """Заглушка для кнопок без действия (например, заголовки строк в списке промокодов)."""
    await call.answer()


@router.callback_query(F.data == "ad:wl:add")
async def ad_wl_add(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _pending[call.from_user.id] = {"kind": "whitelist_add", "created_at": time.time()}
    await call.answer()
    await call.message.answer(
        "Отправьте Telegram ID пользователя или чата, который нужно внести в белый список:\n"
        "<i>(Также можно отправить ID и через пробел заметку, например: <code>123456789 Имя</code>)</i>"
    )


@router.callback_query(F.data.startswith("ad:wl:del:"))
async def ad_wl_del(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    target_id = int(call.data.split(":", 3)[3])
    storage.whitelist_remove(target_id)
    items = storage.whitelist_all()
    await call.message.edit_reply_markup(reply_markup=admin_whitelist_keyboard(items))
    await call.answer(f"ID {target_id} удалён из белого списка")


@router.callback_query(F.data.startswith("ad:clean:"))
async def ad_clean_manual(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    action = call.data.split(":", 2)[2]
    settings = storage.get_global()

    if action == "db":
        removed = storage.db.purge_messages_older_than(settings.media_max_age_hours)
        db_lim_removed = storage.db.enforce_db_size_limit(settings.db_max_size_gb)
        await call.answer(f"✅ БД очищена: {removed + db_lim_removed} записей удалено", show_alert=True)
    elif action == "media":
        age_removed = enforce_media_age(MEDIA_DIR, settings.media_max_age_hours)
        quota_removed = enforce_media_quota(MEDIA_DIR, settings.media_max_total_mb)
        await call.answer(f"✅ Медиа очищено: {age_removed + quota_removed} файлов удалено", show_alert=True)
    elif action == "cache":
        cleared = storage.clear_cache()
        await call.answer(f"✅ RAM-кэш сброшен: {cleared} записей выгружено", show_alert=True)


@router.callback_query(F.data.startswith("ad:user:ban:"))
@router.callback_query(F.data.startswith("ad:users:page:"))
async def ad_users_page(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    page = int(call.data.split(":", 3)[3])
    owners = storage.all_owners_with_stats()
    text = (
        f"<b>👥 Пользователи бота ({len(owners)})</b>\n\n"
        "Нажмите на пользователя для просмотра подробной статистики и управления доступом:\n"
        "🟢 — подключён | ⚪ — не подключён | ⭐ — админ | 🔴 — бан"
    )
    await call.message.edit_text(text, reply_markup=admin_users_keyboard(owners, page=page))
    await call.answer()


@router.callback_query(F.data.startswith("ad:user:view:"))
async def ad_user_view(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    uid = int(call.data.split(":", 3)[3])
    details = storage.db.get_owner_details(uid)
    if not details:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    is_admin = storage.is_admin(uid)
    is_banned = bool(details.get("is_banned", False))
    conns = details.get("connections", [])
    active_conns = sum(1 for c in conns if c.get("is_enabled"))
    name = details.get("full_name") or "Без имени"
    uname = f"@{details['username']}" if details.get("username") else "отсутствует"
    reg_date = time.strftime("%d.%m.%Y %H:%M", time.localtime(details.get("created_at", time.time())))
    last_seen = time.strftime("%d.%m.%Y %H:%M", time.localtime(details.get("last_seen", time.time())))

    conn_ids = [c["connection_id"] for c in conns]
    total_media_bytes = 0
    for cid in conn_ids:
        total_media_bytes += directory_size_bytes(MEDIA_DIR / cid)
    media_mb = total_media_bytes / (1024 * 1024)

    status = "🔴 ЗАБАНЕН" if is_banned else ("⭐ Администратор" if is_admin else ("🟢 Подключён" if active_conns else "⚪ Не подключён"))

    text = (
        f"👤 <b>Статистика пользователя</b>\n\n"
        f"• <b>Имя:</b> {html.escape(name)}\n"
        f"• <b>Username:</b> {uname}\n"
        f"• <b>Telegram ID:</b> <code>{uid}</code>\n"
        f"• <b>Статус:</b> {status}\n\n"
        f"📅 <b>Первый запуск:</b> {reg_date}\n"
        f"👀 <b>Был в сети:</b> {last_seen}\n\n"
        f"🔗 <b>Telegram Business:</b> {active_conns} акк. (всего {len(conns)})\n"
        f"💬 <b>Сообщений в БД:</b> <b>{details['messages_total']}</b>\n"
        f"✏️ <b>Правок:</b> <b>{details['edits_total']}</b> · 🗑 <b>Удалений:</b> <b>{details['deletes_total']}</b>\n"
        f"🖼 <b>Медиафайлов:</b> <b>{details['media_count']}</b> ({media_mb:.1f} МБ)"
    )
    await call.message.edit_text(text, reply_markup=admin_user_detail_keyboard(uid, is_banned, is_admin))
    await call.answer()


@router.callback_query(F.data == "ad:user:find")
async def ad_user_find(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _pending[call.from_user.id] = {"kind": "find_user", "created_at": time.time()}
    await call.answer()
    await call.message.answer("Введите числовой Telegram ID пользователя для просмотра статистики:")


@router.callback_query(F.data.startswith("ad:user:ban:"))
async def ad_user_ban(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    parts = call.data.split(":")
    target_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 1
    if target_id == call.from_user.id:
        await call.answer("Себя забанить нельзя", show_alert=True)
        return
    storage.ban_user(target_id)
    owners = storage.all_owners_with_stats()
    await call.message.edit_reply_markup(reply_markup=admin_users_keyboard(owners, page=page))
    await call.answer(f"🚫 Пользователь {target_id} забанен")


@router.callback_query(F.data.startswith("ad:user:unban:"))
async def ad_user_unban(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    parts = call.data.split(":")
    target_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 1
    storage.unban_user(target_id)
    owners = storage.all_owners_with_stats()
    await call.message.edit_reply_markup(reply_markup=admin_users_keyboard(owners, page=page))
    await call.answer(f"✅ Пользователь {target_id} разбанен")


@router.callback_query(F.data == "ad:restore")
async def ad_restore(call: CallbackQuery, storage: Storage) -> None:
    """Admins sends .db file -> bot will hot-swap current database."""
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _pending[call.from_user.id] = {"kind": "restore_db", "created_at": time.time()}
    await call.answer()
    await call.message.answer(
        "⚠️ <b>Загрузка бэкапа</b>\n\n"
        "Отправьте файл <code>.db</code> единственным сообщением.\n"
        "Текущая БД будет заменена <b>без перезапуска бота</b>.\n"
        "Данные в RAM-кэше (<i>не сохранённые в БД до загрузки</i>) будут потеряны.\n\n"
        "Для отмены отправьте /cancel.",
    )


# ------------------------------------------------------------- catch-all private input
def _not_a_command(message: Message) -> bool:
    if message.successful_payment is not None:
        return False
    return not (message.text and message.text.startswith("/"))


@router.message(F.chat.type == "private", _not_a_command)
async def private_input(message: Message, storage: Storage) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    now = time.time()
    state = _pending.get(user_id)

    # Автоочистка устаревших состояний диалога (> 10 минут)
    if state and now - state.get("created_at", now) > 600:
        _pending.pop(user_id, None)
        state = None

    if state is None:
        if await ghost_handlers.handle_search_input(message, storage):
            return
        if await ghost_handlers.handle_session_relay(message, storage):
            return
        return

    kind = state["kind"]

    if kind == "db_search":
        media = extract_media(message)
        _pending.pop(user_id, None)

        if media is not None:
            status_msg = await message.answer("🔍 <i>Анализирую медиа и вычисляю хэш контента...</i>")
            try:
                data = await download_bytes(message.bot, media.file_id)
                content_hash = compute_media_hash(data, media.kind) if data else None
                rows = storage.db.search_messages_by_media(
                    user_id,
                    file_unique_id=media.file_unique_id,
                    content_hash=content_hash,
                    kind=media.kind,
                )
                media_label = {
                    "photo": "🖼 Фото",
                    "voice": "🎤 Голосовое сообщение",
                    "video_note": "📹 Видеосообщение (кружок)",
                    "video": "🎬 Видео",
                    "audio": "🎵 Аудио",
                    "document": "📄 Документ/файл",
                    "sticker": "🎭 Стикер",
                    "animation": "🎞 GIF/Анимация",
                }.get(media.kind, "📁 Медиафайл")

                if not rows:
                    note = " (файл не удалось загрузить для вычисления хэша — возможно, он превышает лимит Bot API 20 МБ)" if not data else ""
                    await status_msg.edit_text(
                        f"🔍 <b>Поиск по медиа ({media_label}):</b>\n\nНичего не найдено в базе сохранённых сообщений{note}."
                    )
                    return

                lines = [f"🔍 <b>Найдено совпадений по медиа ({media_label}) — {len(rows)}:</b>\n"]
                for r in rows:
                    ts = time.strftime("%d.%m %H:%M", time.localtime(r["cached_at"]))
                    sender = html.escape(r["from_user_name"] or "?")
                    chat_t = html.escape(r["chat_title"] or str(r["chat_id"]))
                    content = html.escape(r["content"] or "")
                    suffix = " 🗑 (удалено)" if r["deleted_at"] else (" ✏️ (изменено)" if r["edited_at"] else "")
                    lines.append(f"💬 <b>{chat_t}</b> | <b>{sender}</b> <i>{ts}</i>{suffix}\n{content}\n")

                text = "\n".join(lines)
                if len(text) > 4000:
                    text = text[:3900] + "\n\n<i>…показаны первые результаты</i>"
                await status_msg.edit_text(text)
                return
            except Exception as exc:
                logger.exception("Ошибка при поиске по медиа")
                await status_msg.edit_text(f"❌ Ошибка поиска по медиа: {exc}")
                return

        query = (message.text or "").strip()
        if not query:
            await message.answer("❌ Введите текст или отправьте медиафайл. Попробуйте ещё раз:")
            return

        rows = storage.db.search_messages(user_id, query)
        if not rows:
            await message.answer(f"🔍 <b>Поиск по «{html.escape(query)}»:</b>\n\nНичего не найдено.")
            return

        lines = [f"🔍 <b>Результаты поиска по «{html.escape(query)}» ({len(rows)}):</b>\n"]
        for r in rows:
            ts = time.strftime("%d.%m %H:%M", time.localtime(r["cached_at"]))
            sender = html.escape(r["from_user_name"] or "?")
            chat_t = html.escape(r["chat_title"] or str(r["chat_id"]))
            content = html.escape(r["content"] or "")
            suffix = " 🗑 (удалено)" if r["deleted_at"] else (" ✏️ (изменено)" if r["edited_at"] else "")
            lines.append(f"💬 <b>{chat_t}</b> | <b>{sender}</b> <i>{ts}</i>{suffix}\n{content}\n")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>…показаны первые результаты</i>"
        await message.answer(text)
        return

    if kind == "edit_user":

        field = get_owner_field(state["key"])
        if field is None or not message.text:
            return
        try:
            value = parse_value(field.kind, message.text)
        except ValueError as exc:
            await message.answer(f"❌ Некорректное значение: {exc}. Попробуйте ещё раз.")
            return
        settings = storage.update_setting(user_id, state["key"], value)
        _pending.pop(user_id, None)
        await message.answer(
            f"✅ Сохранено: {field.label} = {value}",
            reply_markup=section_keyboard(state["section"], settings),
        )
        return

    if kind == "edit_admin":
        if not storage.is_admin(user_id):
            _pending.pop(user_id, None)
            return
        field = get_admin_field(state["key"])
        if field is None or not message.text:
            return
        try:
            value = parse_value(field.kind, message.text)
        except ValueError as exc:
            await message.answer(f"❌ Некорректное значение: {exc}. Попробуйте ещё раз.")
            return
        settings = storage.update_global(state["key"], value)
        _pending.pop(user_id, None)
        await message.answer(
            f"✅ Сохранено: {field.label} = {value}",
            reply_markup=admin_section_keyboard(state["section"], settings),
        )
        return

    if kind == "restore_db":
        if not storage.is_admin(user_id):
            _pending.pop(user_id, None)
            return
        doc = message.document
        if doc is None:
            await message.answer("❌ Нужен документ (.db файл). Попробуйте ещё раз или /cancel для отмены.")
            return
        if not (doc.file_name or "").endswith(".db"):
            await message.answer("❌ Файл должен иметь расширение .db. Попробуйте ещё раз или /cancel.")
            return
        _pending.pop(user_id, None)
        status = await message.answer("⏳ Загружаю файл и заменяю БД…")
        try:
            data = await download_bytes(message.bot, doc.file_id)
            if not data:
                await status.edit_text("❌ Не удалось скачать файл.")
                return
            await asyncio.get_event_loop().run_in_executor(None, storage.db.restore_from_bytes, data)
            # Сбрасываем RAM-кэши которые теперь устарели
            storage._settings_cache.clear()
            storage._global_settings = None
            await status.edit_text(
                "✅ <b>База данных успешно восстановлена.</b>\n"
                "RAM-кэш сообщений остался от старой версии — он вытеснится автоматически при следующей записи.\n"
                "Настройки (admin и user) уже загружены из новой БД."
            )
        except Exception as exc:
            logger.exception("Ошибка при восстановлении БД из бэкапа")
            await status.edit_text(f"❌ Ошибка при восстановлении: {exc}")
        return

    if kind == "afk_text":
        if not message.text:
            await message.answer("❌ Нужен именно текст. Попробуйте ещё раз:")
            return
        storage.update_setting(user_id, "afk_text", message.text)
        _pending.pop(user_id, None)
        await message.answer("✅ Текст AFK-автоответа сохранён.")
        return

    if kind == "preset_name":
        name = (message.text or "").strip().lower()
        if not name or not name.replace("_", "").isalnum():
            await message.answer("❌ Имя должно быть одним словом: буквы/цифры/подчёркивание. Попробуйте снова:")
            return
        _pending[user_id] = {"kind": "preset_items", "name": name, "items": [], "created_at": time.time()}
        await message.answer(
            f"Пресет «{name}»: отправьте одно или несколько сообщений (текст, голосовые, кружки, фото и т.д.) — "
            "они будут отправляться по очереди при вызове <code>.say " + name + "</code>. "
            "Когда закончите — нажмите «Готово».",
            reply_markup=preset_creation_keyboard(),
        )
        return

    if kind == "preset_items":
        item = _message_to_preset_item(message)
        if item is None:
            await message.answer("⚠️ Этот тип сообщения не поддерживается в пресетах, пропущено.", reply_markup=preset_creation_keyboard())
            return
        state["items"].append(item)
        await message.answer(
            f"Добавлено ({len(state['items'])}). Ещё сообщение или нажмите «Готово».",
            reply_markup=preset_creation_keyboard(),
        )
        return

    if kind == "broadcast":
        if not storage.is_admin(user_id):
            _pending.pop(user_id, None)
            return
        _pending.pop(user_id, None)
        html_text = message.html_text or message.html_caption or (f"📢 {message.text}" if message.text else "")
        owners = storage.db.all_owners()
        sent = 0
        for row in owners:
            target_chat_id = int(row["owner_id"])
            try:
                if message.photo:
                    await message.bot.send_photo(chat_id=target_chat_id, photo=message.photo[-1].file_id, caption=html_text)
                elif message.video:
                    await message.bot.send_video(chat_id=target_chat_id, video=message.video.file_id, caption=html_text)
                elif message.document:
                    await message.bot.send_document(chat_id=target_chat_id, document=message.document.file_id, caption=html_text)
                elif message.voice:
                    await message.bot.send_voice(chat_id=target_chat_id, voice=message.voice.file_id, caption=html_text)
                elif message.sticker:
                    await message.bot.send_sticker(chat_id=target_chat_id, sticker=message.sticker.file_id)
                elif html_text:
                    await message.bot.send_message(chat_id=target_chat_id, text=html_text)
                sent += 1
            except Exception:
                pass
        await message.answer(f"✅ Рассылка завершена: отправлено {sent} из {len(owners)}.")
        return

    if kind == "find_user":
        _pending.pop(user_id, None)
        raw = (message.text or "").strip()
        if not raw.isdigit():
            await message.answer("❌ ID должен состоять только из цифр.")
            return
        target_uid = int(raw)
        details = storage.db.get_owner_details(target_uid)
        if not details:
            await message.answer(f"❌ Пользователь с ID <code>{target_uid}</code> не найден.")
            return
        is_admin_target = storage.is_admin(target_uid)
        is_banned_target = bool(details.get("is_banned", False))
        conns = details.get("connections", [])
        active_conns = sum(1 for c in conns if c.get("is_enabled"))
        name = details.get("full_name") or "Без имени"
        uname = f"@{details['username']}" if details.get("username") else "отсутствует"
        reg_date = time.strftime("%d.%m.%Y %H:%M", time.localtime(details.get("created_at", time.time())))
        last_seen = time.strftime("%d.%m.%Y %H:%M", time.localtime(details.get("last_seen", time.time())))
        status = "🔴 ЗАБАНЕН" if is_banned_target else ("⭐ Администратор" if is_admin_target else ("🟢 Подключён" if active_conns else "⚪ Не подключён"))

        text = (
            f"👤 <b>Статистика пользователя</b>\n\n"
            f"• <b>Имя:</b> {html.escape(name)}\n"
            f"• <b>Username:</b> {uname}\n"
            f"• <b>Telegram ID:</b> <code>{target_uid}</code>\n"
            f"• <b>Статус:</b> {status}\n\n"
            f"📅 <b>Первый запуск:</b> {reg_date}\n"
            f"👀 <b>Был в сети:</b> {last_seen}\n\n"
            f"🔗 <b>Telegram Business:</b> {active_conns} акк. (всего {len(conns)})\n"
            f"💬 <b>Сообщений в БД:</b> <b>{details['messages_total']}</b>\n"
            f"✏️ <b>Правок:</b> <b>{details['edits_total']}</b> · 🗑 <b>Удалений:</b> <b>{details['deletes_total']}</b>\n"
            f"🖼 <b>Медиафайлов:</b> <b>{details['media_count']}</b>"
        )
        await message.answer(text, reply_markup=admin_user_detail_keyboard(target_uid, is_banned_target, is_admin_target))
        return

    if kind == "act_manual_chat":
        _pending.pop(user_id, None)
        raw = (message.text or "").strip()
        if not raw.lstrip("-").isdigit():
            await message.answer("❌ Введите корректный числовой Chat ID.")
            return
        target_chat = int(raw)
        text = f"⚙️ <b>Управление чатом</b> <code>{target_chat}</code>:\n\nВыберите действие:"
        await message.answer(text, reply_markup=chat_action_picker_keyboard(target_chat))
        return

    if kind == "act_del_count":
        chat_id = state["chat_id"]
        conn_id = state["conn_id"]
        _pending.pop(user_id, None)
        raw = (message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await message.answer("❌ Введите положительное число.")
            return
        cnt = int(raw)
        is_admin = storage.is_admin(user_id)
        if not is_admin:
            cnt = min(cnt, 100)
        recent = storage.recent_messages(conn_id, chat_id, cnt + 10)
        msg_ids = [int(r["message_id"]) for r in recent][:cnt]
        total_del = 0
        for i in range(0, len(msg_ids), 100):
            chunk = msg_ids[i:i + 100]
            for mid in chunk:
                storage.mark_bot_deleted(conn_id, chat_id, mid)
            try:
                await message.bot.delete_business_messages(business_connection_id=conn_id, message_ids=chunk)
                total_del += len(chunk)
            except Exception:
                pass
        await message.answer(f"🗑 Удалено <b>{total_del}</b> сообщений в чате <code>{chat_id}</code>.")
        return

    if kind == "online_custom_min":
        _pending.pop(user_id, None)
        raw = (message.text or "").strip()
        if not raw.isdigit():
            await message.answer("❌ Введите целое число минут (например: 25).")
            return
        minutes = int(raw)
        if minutes <= 0:
            await message.answer("❌ Время должно быть больше 0 минут.")
            return
        is_admin = storage.is_admin(user_id)
        if not is_admin and minutes > 60:
            minutes = 60
            await message.answer("⚠️ Максимальное время онлайн-режима — 60 минут (1 час). Установлено на 60 минут.")
        duration_sec = minutes * 60
        task = asyncio.create_task(_online_worker(message.bot, storage, user_id, duration_sec))
        storage.start_online(user_id, duration_sec, task)
        await message.answer(f"🟢 Онлайн-режим успешно запущен на <b>{minutes}</b> мин.!")
        return

    if kind == "del_manual_chat":
        _pending.pop(user_id, None)
        raw = (message.text or "").strip()
        if not raw.lstrip("-").isdigit():
            await message.answer("❌ Введите корректный числовой Chat ID.")
            return
        target_chat = int(raw)
        conns = storage.connections_for_owner(user_id)
        conn_id = conns[0] if conns else ""
        _pending[user_id] = {
            "kind": "act_delword_prompt",
            "chat_id": target_chat,
            "conn_id": conn_id,
            "created_at": time.time(),
        }
        await message.answer(
            f"🎯 <b>Очистка чата <code>{target_chat}</code></b>\n\n"
            "Введите слово, фразу или регулярку для удаления:"
        )
        return

    if kind in ("act_delword_prompt", "act_delregex_prompt"):
        chat_id = state["chat_id"]
        conn_id = state["conn_id"]
        _pending.pop(user_id, None)
        raw_pattern = (message.text or "").strip()
        if not raw_pattern:
            await message.answer("❌ Шаблон пуст.")
            return

        is_explicit = raw_pattern.startswith(("re:", "regex:")) or (
            raw_pattern.startswith("/") and raw_pattern.endswith("/") and len(raw_pattern) > 2
        )
        pattern = raw_pattern
        if raw_pattern.startswith(("re:", "regex:")):
            pattern = raw_pattern.split(":", 1)[1].strip()
        elif raw_pattern.startswith("/") and raw_pattern.endswith("/") and len(raw_pattern) > 2:
            pattern = raw_pattern[1:-1]

        regex_chars = r".*+?[]{}()^$|\\"
        is_regex = is_explicit or any(c in pattern for c in regex_chars) or (kind == "act_delregex_prompt")

        matched_ids = []
        if is_regex:
            try:
                import re
                re.compile(pattern)
                matched_ids = storage.db.find_messages_matching(conn_id, chat_id, pattern, is_regex=True)
            except Exception:
                is_regex = False
        if not is_regex or (not matched_ids and not is_explicit and kind != "act_delregex_prompt"):
            matched_ids = storage.db.find_messages_matching(conn_id, chat_id, raw_pattern, is_regex=False)

        total_del = 0
        for i in range(0, len(matched_ids), 100):
            chunk = matched_ids[i : i + 100]
            for mid in chunk:
                storage.mark_bot_deleted(conn_id, chat_id, mid)
            try:
                await message.bot.delete_business_messages(business_connection_id=conn_id, message_ids=chunk)
                total_del += len(chunk)
            except Exception:
                pass
        await message.answer(
            f"🗑 Удалено <b>{total_del}</b> сообщений в чате <code>{chat_id}</code> по фильтру «{html.escape(raw_pattern)}»."
        )
        return

    if kind == "del_all_chats_prompt":
        _pending.pop(user_id, None)
        raw_pattern = (message.text or "").strip()
        if not raw_pattern:
            await message.answer("❌ Шаблон пуст.")
            return

        is_explicit = raw_pattern.startswith(("re:", "regex:")) or (
            raw_pattern.startswith("/") and raw_pattern.endswith("/") and len(raw_pattern) > 2
        )
        pattern = raw_pattern
        if raw_pattern.startswith(("re:", "regex:")):
            pattern = raw_pattern.split(":", 1)[1].strip()
        elif raw_pattern.startswith("/") and raw_pattern.endswith("/") and len(raw_pattern) > 2:
            pattern = raw_pattern[1:-1]

        regex_chars = r".*+?[]{}()^$|\\"
        is_regex = is_explicit or any(c in pattern for c in regex_chars)

        all_rows = []
        if is_regex:
            try:
                import re
                re.compile(pattern)
                all_rows = storage.db.find_messages_matching_all(user_id, pattern, is_regex=True)
            except Exception:
                is_regex = False
        if not is_regex or (not all_rows and not is_explicit):
            all_rows = storage.db.find_messages_matching_all(user_id, raw_pattern, is_regex=False)

        by_chat: dict[tuple[str, int], list[int]] = {}
        for r in all_rows:
            by_chat.setdefault((r["connection_id"], int(r["chat_id"])), []).append(int(r["message_id"]))

        total_del = 0
        for (conn_id, cid), mids in by_chat.items():
            for i in range(0, len(mids), 100):
                chunk = mids[i : i + 100]
                for mid in chunk:
                    storage.mark_bot_deleted(conn_id, cid, mid)
                try:
                    await message.bot.delete_business_messages(business_connection_id=conn_id, message_ids=chunk)
                    total_del += len(chunk)
                except Exception:
                    pass

        await message.answer(
            f"🌐 <b>Массовая зачистка завершена</b>\n\n"
            f"Удалено <b>{total_del}</b> сообщений в <b>{len(by_chat)}</b> чатах по запросу «{html.escape(raw_pattern)}»."
        )
        return

    if kind == "act_spam_prompt":
        chat_id = state["chat_id"]
        conn_id = state["conn_id"]
        _pending.pop(user_id, None)
        count = 5
        is_admin = storage.is_admin(user_id)
        if not is_admin:
            count = min(count, 50)
        sent = 0
        for _ in range(count):
            try:
                if message.photo:
                    await message.bot.send_photo(chat_id=chat_id, photo=message.photo[-1].file_id, caption=message.caption, business_connection_id=conn_id)
                elif message.sticker:
                    await message.bot.send_sticker(chat_id=chat_id, sticker=message.sticker.file_id, business_connection_id=conn_id)
                elif message.video:
                    await message.bot.send_video(chat_id=chat_id, video=message.video.file_id, caption=message.caption, business_connection_id=conn_id)
                elif message.text:
                    await message.bot.send_message(chat_id=chat_id, text=message.text, business_connection_id=conn_id)
                sent += 1
            except Exception:
                break
            await asyncio.sleep(0.3)
        await message.answer(f"💣 Спам отправлен: <b>{sent}</b> сообщений.")
        return



async def _message_to_preset_item(message: Message) -> dict | None:
    media = extract_media(message)
    if media is not None:
        return {"type": "media", "kind": media.kind, "file_id": media.file_id}
    text = message.text or message.caption
    if text:
        return {"type": "text", "content": text}
    return None
