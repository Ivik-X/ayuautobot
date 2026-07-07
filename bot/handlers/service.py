from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.backup import BackupManager
from bot.config import Config
from bot.keyboards import CB_CLOSE, CB_EDIT, CB_TOGGLE, settings_keyboard
from bot.media import MEDIA_DIR, directory_size_bytes
from bot.settings import get_field, parse_value
from bot.stats import format_admin_overview
from bot.storage import Storage
from bot.texts import Texts

logger = logging.getLogger(__name__)

router = Router(name="service")

# owner_id -> ключ настройки, которую сейчас редактируют текстом
_pending_edit: dict[int, str] = {}


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(CommandStart())
async def cmd_start(message: Message, texts: Texts, storage: Storage) -> None:
    storage.db.ensure_owner(message.from_user.id)
    await message.answer(texts.start)


@router.message(Command("help"))
async def cmd_help(message: Message, texts: Texts) -> None:
    await message.answer(texts.help)


@router.message(Command("settings"))
async def cmd_settings(message: Message, storage: Storage) -> None:
    owner_id = message.from_user.id
    storage.db.ensure_owner(owner_id)
    settings = storage.get_settings(owner_id)
    await message.answer(
        "<b>⚙️ Гибкие настройки бота</b>\n"
        "Нажмите пункт, чтобы включить/выключить, либо изменить значение.\n"
        "Настройки персональные — у каждого владельца бизнес-подключения свои.",
        reply_markup=settings_keyboard(settings),
    )


@router.callback_query(F.data == CB_CLOSE)
async def cb_close(call: CallbackQuery) -> None:
    if call.message:
        await call.message.delete()
    await call.answer()


@router.callback_query(F.data.startswith(CB_TOGGLE))
async def cb_toggle(call: CallbackQuery, storage: Storage) -> None:
    key = call.data[len(CB_TOGGLE):]
    field = get_field(key)
    if field is None or field.kind != "bool":
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    settings = storage.toggle_setting(call.from_user.id, key)
    await call.message.edit_reply_markup(reply_markup=settings_keyboard(settings))
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith(CB_EDIT))
async def cb_edit(call: CallbackQuery) -> None:
    key = call.data[len(CB_EDIT):]
    field = get_field(key)
    if field is None:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    _pending_edit[call.from_user.id] = key
    await call.answer()
    await call.message.answer(f"Введите новое значение для «{field.label}» одним сообщением:")


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def private_text_input(message: Message, storage: Storage) -> None:
    key = _pending_edit.pop(message.from_user.id, None)
    if key is None:
        return
    field = get_field(key)
    if field is None:
        return
    try:
        value = parse_value(field.kind, message.text)
    except ValueError as exc:
        _pending_edit[message.from_user.id] = key
        await message.answer(f"❌ Некорректное значение: {exc}. Попробуйте ещё раз.")
        return
    settings = storage.update_setting(message.from_user.id, key, value)
    await message.answer(
        f"✅ Сохранено: {field.label} = {value}",
        reply_markup=settings_keyboard(settings),
    )


# ------------------------------------------------------------------- admin --


@router.message(Command("admin"))
async def cmd_admin(message: Message, storage: Storage, config: Config, backup: BackupManager) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    text = format_admin_overview(
        owners_count=storage.db.owners_count(),
        connections_count=storage.db.connections_count(),
        db_size_mb=storage.db.file_size_bytes() / (1024 * 1024),
        db_messages=storage.db.count_messages(None),
        db_messages_total=storage.db.count_all_messages(),
        media_mb=directory_size_bytes(MEDIA_DIR) / (1024 * 1024),
        backup_enabled=config.backup.enabled,
        backup_interval_hours=config.backup.interval_hours,
        last_backup_ts=backup.last_backup_ts,
    )
    await message.answer(
        text
        + "\n\n<i>Команды:</i> /users · /backupnow · /broadcast текст"
    )


@router.message(Command("users"))
async def cmd_users(message: Message, storage: Storage, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    rows = storage.db.all_connections()
    if not rows:
        await message.answer("Пока нет ни одного подключённого бизнес-аккаунта.")
        return
    lines = ["<b>👥 Подключения</b>"]
    for row in rows:
        status = "🟢" if row["is_enabled"] else "🔴"
        lines.append(
            f"{status} owner=<code>{row['owner_id']}</code> "
            f"conn=<code>{row['connection_id']}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("backupnow"))
async def cmd_backup_now(message: Message, storage: Storage, config: Config, backup: BackupManager) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await message.answer("⏳ Делаю бэкап и отправляю…")
    ok = await backup.backup_now()
    if ok:
        await message.answer("✅ Бэкап отправлен, локальная история схлопнута.")
    else:
        await message.answer("❌ Не удалось выполнить бэкап (см. логи).")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, storage: Storage, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2:
        await message.answer("Использование: /broadcast текст сообщения")
        return
    payload = text[1]
    rows = storage.db.all_connections()
    sent = 0
    for row in rows:
        try:
            await message.bot.send_message(chat_id=row["user_chat_id"], text=f"📢 {payload}")
            sent += 1
        except Exception:
            logger.exception("Не удалось отправить broadcast owner=%s", row["owner_id"])
    await message.answer(f"Отправлено {sent} из {len(rows)}.")
