from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from bot.storage import Storage

logger = logging.getLogger(__name__)


async def run_profile_watch_loop(bot: Bot, storage: Storage) -> None:
    """Раз в profile_watch_interval_min (настройка админа) проверяет чаты,
    добавленные командой .watch, на смену имени/фамилии/username/фото.

    Bot API не присылает push-уведомления о смене профиля собеседника,
    поэтому единственный способ — периодический опрос getChat. Запросы
    намеренно разносятся по времени (пауза между каждым чатом), чтобы не
    создавать всплеск нагрузки на API даже при большом списке наблюдения.
    """
    while True:
        interval_min = storage.get_global().profile_watch_interval_min
        await asyncio.sleep(max(interval_min, 1) * 60)

        rows = storage.watch_all()
        for row in rows:
            try:
                await _check_one(bot, storage, row)
            except Exception:
                logger.exception("Ошибка проверки watch для chat_id=%s", row["chat_id"])
            await asyncio.sleep(1.0)


async def _check_one(bot: Bot, storage: Storage, row) -> None:
    connection_id = row["connection_id"]
    chat_id = row["chat_id"]
    owner_id = row["owner_id"]

    try:
        chat = await bot.get_chat(chat_id)
    except Exception:
        logger.warning("Не удалось получить чат %s для watch (недоступен?)", chat_id)
        return

    photo_unique_id = chat.photo.small_file_unique_id if chat.photo else None
    new_first = chat.first_name or ""
    new_last = chat.last_name or ""
    new_username = chat.username or ""

    old_first = row["first_name"] or ""
    old_last = row["last_name"] or ""
    old_username = row["username"] or ""
    old_photo = row["photo_unique_id"]

    changes: list[str] = []
    if new_first != old_first:
        changes.append(f"Имя: «{old_first or '—'}» → «{new_first or '—'}»")
    if new_last != old_last:
        changes.append(f"Фамилия: «{old_last or '—'}» → «{new_last or '—'}»")
    if new_username != old_username:
        changes.append(f"Username: «@{old_username}» → «@{new_username}»" if (old_username or new_username) else "")
    if photo_unique_id != old_photo:
        changes.append("Изменилось фото профиля")
    changes = [c for c in changes if c]

    storage.watch_upsert(
        connection_id, chat_id, owner_id, row["chat_title"],
        {
            "first_name": chat.first_name,
            "last_name": chat.last_name,
            "username": chat.username,
            "photo_unique_id": photo_unique_id,
        },
    )

    if not changes:
        return

    owner_chat_id = storage.owner_chat_id(connection_id)
    if owner_chat_id is None:
        logger.warning("Нет owner_chat_id для connection %s — уведомление о смене профиля потеряно", connection_id)
        return

    text = (
        "👁 <b>Изменение профиля в отслеживаемом чате</b>\n"
        f"Чат: <b>{row['chat_title']}</b>\n\n" + "\n".join(changes)
    )
    try:
        await bot.send_message(chat_id=owner_chat_id, text=text)
    except Exception:
        logger.exception("Не удалось уведомить о смене профиля")
