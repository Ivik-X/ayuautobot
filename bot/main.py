from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.backup import BackupManager
from bot.cleanup import run_cleanup_loop, startup_cleanup
from bot.config import load_config
from bot.database import Database
from bot.diskguard import run_disk_guard_loop
from bot.handlers import billing, business, service
from bot.handlers import ghost as ghost_handlers
from bot.storage import Storage
from bot.watchers import run_profile_watch_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "pre_checkout_query",
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
    dp["stt_config"] = config.stt

    dp.include_router(service.router)
    dp.include_router(billing.router)
    dp.include_router(ghost_handlers.router)
    dp.include_router(business.router)

    background_tasks = [
        asyncio.create_task(run_cleanup_loop(storage)),
        asyncio.create_task(backup_manager.run_loop()),
        asyncio.create_task(run_profile_watch_loop(bot, storage)),
        asyncio.create_task(run_disk_guard_loop(storage, backup_manager)),
    ]

    global_settings = storage.get_global()
    logger.info(
        "Бот запущен: кэш=%s записей, автобэкап=%s (каждые %sч), медиа-квота=%sМБ, "
        "мин. свободно на диске=%.1fГБ, админов=%s",
        global_settings.cache_max_entries,
        global_settings.backup_enabled,
        global_settings.backup_interval_hours,
        global_settings.media_max_total_mb,
        global_settings.min_free_disk_gb,
        len(config.admin_ids),
    )
    try:
        if config.webhook.enabled:
            await _run_webhook(bot, dp, config)
        else:
            await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await http_session.close()
        db.close()


async def _run_webhook(bot: Bot, dp: Dispatcher, config) -> None:
    """Режим webhook — Telegram сам пушит апдейты на публичный HTTPS-адрес
    вместо того, чтобы бот их периодически опрашивал (long polling). Ниже
    задержка доставки апдейта, что особенно важно для гонки со скачиванием
    одноразовых медиа. Требует публичный домен с валидным TLS-сертификатом
    (например, за reverse-proxy с Let's Encrypt) — см. README.
    """
    webhook_url = f"{config.webhook.url}{config.webhook.path}"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=config.webhook.secret or None,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=False,
    )
    logger.info("Webhook установлен: %s", webhook_url)

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=config.webhook.secret or None
    ).register(app, path=config.webhook.path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=config.webhook.host, port=config.webhook.port)
    await site.start()
    logger.info("Webhook-сервер слушает %s:%s", config.webhook.host, config.webhook.port)

    try:
        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено")
