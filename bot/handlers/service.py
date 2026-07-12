from __future__ import annotations

import html
import json
import logging
import tempfile
import time
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.backup import BackupManager
from bot.media import MEDIA_DIR, MediaRef, directory_size_bytes, download_bytes, extract_media, send_media_copy
from bot import ghost
from bot.config import SttConfig
from bot.handlers import ghost as ghost_handlers
from bot.features.chat_export import build_export_html, build_export_json
from bot.features.stt_local import SttError, transcribe_local
from bot.keyboards import (
    admin_back_keyboard,
    admin_main_keyboard,
    admin_section_keyboard,
    chats_export_keyboard,
    chats_recent_keyboard,
    ghost_settings_keyboard,
    help_back_keyboard,
    help_topics_keyboard,
    main_settings_keyboard,
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
    storage.db.ensure_owner(message.from_user.id, is_admin=is_admin)
    hint = DEFAULT_ADMIN_HINT if is_admin else ""
    await message.answer(texts.start.replace("{admin_hint}", hint))


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


# ---------------------------------------------------------------------- /settings
@router.message(Command("settings"))
async def cmd_settings(message: Message, storage: Storage) -> None:
    owner_id = message.from_user.id
    storage.db.ensure_owner(owner_id, is_admin=storage.is_admin(owner_id))
    digest_count = storage.queue_count(owner_id)
    await message.answer(
        "<b>⚙️ Ваши настройки</b>\nВыберите раздел:",
        reply_markup=main_settings_keyboard(digest_count),
    )


@router.callback_query(F.data == "us:close")
async def us_close(call: CallbackQuery) -> None:
    if call.message:
        await call.message.delete()
    await call.answer()


@router.callback_query(F.data == "us:back")
async def us_back(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    digest_count = storage.queue_count(owner_id)
    await call.message.edit_text(
        "<b>⚙️ Ваши настройки</b>\nВыберите раздел:", reply_markup=main_settings_keyboard(digest_count)
    )
    await call.answer()


@router.callback_query(F.data == "us:noop")
async def us_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.startswith("us:open:"))
async def us_open(call: CallbackQuery, storage: Storage) -> None:
    section = call.data.split(":", 2)[2]
    owner_id = call.from_user.id

    if section == "presets":
        names = storage.preset_list(owner_id)
        text = "<b>🗂 Пресеты .say</b>\n" + (
            "Ваши пресеты:" if names else "У вас пока нет пресетов."
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
    _, _, section, key = call.data.split(":", 3)
    settings = storage.toggle_setting(call.from_user.id, key)
    await call.message.edit_reply_markup(reply_markup=section_keyboard(section, settings))
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith("us:cycle:"))
async def us_cycle(call: CallbackQuery, storage: Storage) -> None:
    _, _, section, key = call.data.split(":", 3)
    field = get_owner_field(key)
    if field is None:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    current_settings = storage.get_settings(call.from_user.id)
    new_value = next_cycle_value(field, getattr(current_settings, key))
    settings = storage.update_setting(call.from_user.id, key, new_value)
    await call.message.edit_reply_markup(reply_markup=section_keyboard(section, settings))
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith("us:edit:"))
async def us_edit(call: CallbackQuery) -> None:
    _, _, section, key = call.data.split(":", 3)
    field = get_owner_field(key)
    if field is None:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    _pending[call.from_user.id] = {"kind": "edit_user", "section": section, "key": key}
    await call.answer()
    await call.message.answer(f"Введите новое значение для «{field.label}» одним сообщением:")


@router.callback_query(F.data == "us:afktext")
async def us_afktext(call: CallbackQuery) -> None:
    _pending[call.from_user.id] = {"kind": "afk_text"}
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
    await call.message.edit_text(
        "<b>⚙️ Ваши настройки</b>\nВыберите раздел:", reply_markup=main_settings_keyboard(digest_count)
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
            "Пока нет сохранённых сообщений для экспорта. Включите «сохранять все сообщения» "
            "в /admin → 💾 Данные, если хотите копить историю для экспорта.",
            show_alert=True,
        )
        return

    await call.message.edit_text(
        "<b>📤 Экспорт переписки</b>\nВыберите чат (в скобках — число сохранённых сообщений):",
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


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
    return cleaned[:60] or "chat"


@router.callback_query(F.data == "us:recent")
async def us_recent(call: CallbackQuery, storage: Storage) -> None:
    owner_id = call.from_user.id
    connection_ids = storage.connections_for_owner(owner_id)
    chats: list[tuple[int, str, int]] = []
    for connection_id in connection_ids:
        chats.extend(storage.chats_for_connection(connection_id))
    chats.sort(key=lambda item: item[2], reverse=True)

    if not chats:
        await call.answer(
            "Пока нет сохранённых сообщений. Включите «сохранять все сообщения» в /admin → 💾 Данные.",
            show_alert=True,
        )
        return

    await call.message.edit_text(
        "<b>👁‍🗨 Последние сообщения</b>\nВыберите чат:", reply_markup=chats_recent_keyboard(chats)
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
async def us_preset_add(call: CallbackQuery) -> None:
    _pending[call.from_user.id] = {"kind": "preset_name"}
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


# -------------------------------------------------------------------------- /admin
def _admin_overview_text(storage: Storage, backup: BackupManager) -> str:
    settings = storage.get_global()
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


@router.callback_query(F.data.startswith("ad:open:"))
async def ad_open(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    section = call.data.split(":", 2)[2]

    if section == "users":
        rows = storage.db.all_connections()
        if not rows:
            text = "Пока нет ни одного подключённого бизнес-аккаунта."
        else:
            lines = ["<b>👥 Подключения</b>"]
            for row in rows:
                status = "🟢" if row["is_enabled"] else "🔴"
                lines.append(f"{status} owner=<code>{row['owner_id']}</code> conn=<code>{row['connection_id']}</code>")
            text = "\n".join(lines)
        await call.message.edit_text(text, reply_markup=admin_back_keyboard())
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
    _pending[call.from_user.id] = {"kind": "edit_admin", "section": section, "key": key}
    await call.answer()
    await call.message.answer(f"Введите новое значение для «{field.label}» одним сообщением:")


@router.callback_query(F.data == "ad:backupnow")
async def ad_backupnow(call: CallbackQuery, storage: Storage, backup: BackupManager) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    await call.answer("Делаю бэкап…")
    ok = await backup.backup_now()
    await call.message.answer("✅ Бэкап отправлен, локальная история схлопнута." if ok else "❌ Не удалось выполнить бэкап (см. логи).")


@router.callback_query(F.data == "ad:broadcast")
async def ad_broadcast(call: CallbackQuery, storage: Storage) -> None:
    if not storage.is_admin(call.from_user.id):
        await call.answer()
        return
    _pending[call.from_user.id] = {"kind": "broadcast"}
    await call.answer()
    await call.message.answer("Отправьте текст рассылки одним сообщением — уйдёт всем подключённым владельцам:")


@router.callback_query(F.data == "ad:cancel")
async def ad_cancel(call: CallbackQuery) -> None:
    _pending.pop(call.from_user.id, None)
    await call.answer("Отменено")


# ------------------------------------------------------------- catch-all private input
@router.message(F.chat.type == "private")
async def private_input(message: Message, storage: Storage, stt_config: SttConfig) -> None:
    user_id = message.from_user.id
    state = _pending.get(user_id)

    if state is None:
        if await ghost_handlers.handle_search_input(message, storage):
            return
        if await ghost_handlers.handle_session_relay(message, storage):
            return
        # Голосовые/кружки/аудио, отправленные боту напрямую (не часть другого
        # диалога) — расшифровываем, если настроен STT.
        if message.voice or message.video_note or message.audio:
            await _handle_stt(message, stt_config)
        return

    kind = state["kind"]

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
        _pending[user_id] = {"kind": "preset_items", "name": name, "items": []}
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
        text = message.text or message.caption
        if not text:
            await message.answer("❌ Поддерживается только текст. Попробуйте ещё раз:")
            return
        _pending.pop(user_id, None)
        rows = storage.db.all_connections()
        sent = 0
        for row in rows:
            try:
                await message.bot.send_message(chat_id=row["user_chat_id"], text=f"📢 {text}")
                sent += 1
            except Exception:
                logger.exception("Не удалось отправить рассылку owner=%s", row["owner_id"])
        await message.answer(f"Отправлено {sent} из {len(rows)}.")
        return


async def _handle_stt(message: Message, stt_config: SttConfig) -> None:
    if not stt_config.enabled:
        return  # STT выключен (STT_ENABLED=false в .env) — молчим, чтобы не спамить объяснением на каждое гс
    media = extract_media(message)
    if media is None:
        return
    data = await download_bytes(message.bot, media.file_id)
    if data is None:
        await message.answer("❌ Не удалось скачать файл для распознавания.")
        return
    status = await message.answer("🎙 Распознаю локально (первый запуск может занять время — грузится модель)…")
    try:
        text = await transcribe_local(
            data, model_size=stt_config.model_size, models_dir=stt_config.models_dir, language=stt_config.language
        )
        await status.edit_text(f"📝 <b>Расшифровка:</b>\n{text}")
    except SttError as exc:
        await status.edit_text(f"❌ Не удалось распознать: {exc}")


def _message_to_preset_item(message: Message) -> dict | None:
    media = extract_media(message)
    if media is not None:
        return {"type": "media", "kind": media.kind, "file_id": media.file_id}
    text = message.text or message.caption
    if text:
        return {"type": "text", "content": text}
    return None
