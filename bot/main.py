from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.backup import BackupManager
from bot.cleanup import run_cleanup_loop, startup_cleanup
from bot.config import load_config
from bot.database import Database
from bot.handlers import business, service
from bot.storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


async def main() -> None:
    config = load_config()

    db = Database(config.db_path)

    # Если у глобальных настроек ещё нет сохранённого значения в БД (первый
    # запуск) — используем то, что задано через .env, как стартовый посев.
    if db.get_global_settings_raw() is None:
        db.save_global_settings(config.seed_global_settings.to_json())

    storage = Storage(db, config.admin_ids)
    startup_cleanup(storage)

    for admin_id in config.admin_ids:
        db.ensure_owner(admin_id, is_admin=True)

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    http_session = aiohttp.ClientSession()
    backup_manager = BackupManager(bot, storage, config.admin_ids)

    dp = Dispatcher()
    dp["storage"] = storage
    dp["texts"] = config.texts
    dp["http_session"] = http_session
    dp["backup"] = backup_manager

    dp.include_router(service.router)
    dp.include_router(business.router)

    background_tasks = [
        asyncio.create_task(run_cleanup_loop(storage)),
        asyncio.create_task(backup_manager.run_loop()),
    ]

    global_settings = storage.get_global()
    logger.info(
        "Бот запущен: кэш=%s записей, автобэкап=%s (каждые %sч), медиа-квота=%sМБ, "
        "сохранять все сообщения=%s, админов=%s",
        global_settings.cache_max_entries,
        global_settings.backup_enabled,
        global_settings.backup_interval_hours,
        global_settings.media_max_total_mb,
        global_settings.store_all_messages,
        len(config.admin_ids),
    )
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await http_session.close()
        db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено")
