from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.cleanup import run_cleanup_loop, startup_cleanup
from bot.config import load_config
from bot.database import Database
from bot.handlers import business, service
from bot.media import DB_MEDIA_DIR
from bot.storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

ALLOWED_UPDATES = [
    "message",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


async def main() -> None:
    config = load_config()

    db: Database | None = None
    if config.db.enabled:
        db = Database(
            config.db.path,
            save_media=config.db.save_media,
            media_dir=DB_MEDIA_DIR,
        )
        logger.info(
            "БД включена: %s (медиа=%s, хранение %s дн.)",
            config.db.path,
            config.db.save_media,
            config.db.retention_days or "∞",
        )

    storage = Storage(db=db)
    startup_cleanup(storage, config)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp["storage"] = storage
    dp["config"] = config
    dp["texts"] = config.texts

    dp.include_router(service.router)
    dp.include_router(business.router)

    cleanup_task = asyncio.create_task(run_cleanup_loop(storage, config))
    logger.info(
        "Бот запущен (ttl=%sч, лимит=%s, очистка каждые %s мин, save_media=%s)",
        config.cache_ttl_hours,
        config.cache_max_entries,
        config.cleanup_interval_min,
        config.save_media,
    )
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        if db is not None:
            db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено")
