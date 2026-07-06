from __future__ import annotations

import asyncio
import logging

from bot.config import Config
from bot.storage import Storage

logger = logging.getLogger(__name__)


async def run_cleanup_loop(storage: Storage, config: Config) -> None:
    interval = config.cleanup_interval_min * 60
    ttl = config.cache_ttl_hours * 3600

    while True:
        await asyncio.sleep(interval)
        removed = storage.purge_expired(ttl)
        trimmed = storage.enforce_max_entries(config.cache_max_entries)
        db_removed = 0
        if config.db.enabled and storage.db and config.db.retention_days > 0:
            db_removed = storage.db.purge_older_than(config.db.retention_days)
        if removed or trimmed or db_removed:
            logger.info(
                "Автоочистка: кэш %s+%s, БД %s записей",
                removed,
                trimmed,
                db_removed,
            )


def startup_cleanup(storage: Storage, config: Config) -> int:
    ttl = config.cache_ttl_hours * 3600
    removed = storage.purge_expired(ttl)
    trimmed = storage.enforce_max_entries(config.cache_max_entries)
    db_removed = 0
    if config.db.enabled and storage.db and config.db.retention_days > 0:
        db_removed = storage.db.purge_older_than(config.db.retention_days)
    total = removed + trimmed + db_removed
    if total:
        logger.info("Стартовая очистка: кэш %s+%s, БД %s", removed, trimmed, db_removed)
    return total
