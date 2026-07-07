from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot

from bot.config import Config
from bot.media import MEDIA_DIR, enforce_media_quota
from bot.storage import Storage

logger = logging.getLogger(__name__)


async def run_cleanup_loop(storage: Storage, config: Config) -> None:
    interval = config.cache.cleanup_interval_min * 60

    while True:
        await asyncio.sleep(interval)
        removed = storage.purge_expired_all()
        media_removed = enforce_media_quota(MEDIA_DIR, config.media.max_total_mb)
        if removed or media_removed:
            logger.info("Автоочистка: кэш %s записей, медиа-квота %s файлов", removed, media_removed)


def startup_cleanup(storage: Storage, config: Config) -> int:
    removed = storage.purge_expired_all()
    media_removed = enforce_media_quota(MEDIA_DIR, config.media.max_total_mb)
    total = removed + media_removed
    if total:
        logger.info("Стартовая очистка: кэш %s, медиа-квота %s", removed, media_removed)
    return total


async def run_reminders_loop(bot: Bot, storage: Storage) -> None:
    """Восстанавливает и обслуживает напоминания (.remind) — переживают рестарт бота."""
    scheduled: set[int] = set()

    while True:
        pending = storage.reminders_pending()
        now = time.time()
        for row in pending:
            reminder_id = int(row["id"])
            if reminder_id in scheduled:
                continue
            scheduled.add(reminder_id)
            delay = max(0.0, float(row["fire_at"]) - now)
            asyncio.create_task(_fire_reminder(bot, storage, reminder_id, delay, row))
        await asyncio.sleep(30)


async def _fire_reminder(bot: Bot, storage: Storage, reminder_id: int, delay: float, row) -> None:
    try:
        await asyncio.sleep(delay)
        owner_chat_id = row["chat_id"]
        connection = storage.get_connection(row["connection_id"])
        target_chat = connection.user_chat_id if connection else owner_chat_id
        await bot.send_message(chat_id=target_chat, text=f"⏰ <b>Напоминание:</b>\n{row['text']}")
    except Exception:
        logger.exception("Не удалось отправить напоминание %s", reminder_id)
    finally:
        storage.reminder_delete(reminder_id)
