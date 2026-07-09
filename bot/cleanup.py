from __future__ import annotations

import asyncio
import logging

from bot.media import MEDIA_DIR, enforce_media_quota
from bot.storage import Storage

logger = logging.getLogger(__name__)


async def run_cleanup_loop(storage: Storage) -> None:
    while True:
        interval_min = storage.get_global().cache_cleanup_interval_min
        await asyncio.sleep(max(interval_min, 1) * 60)
        removed = storage.purge_expired_all()
        media_removed = enforce_media_quota(MEDIA_DIR, storage.get_global().media_max_total_mb)
        if removed or media_removed:
            logger.info("Автоочистка: кэш %s записей, медиа-квота %s файлов", removed, media_removed)


def startup_cleanup(storage: Storage) -> int:
    removed = storage.purge_expired_all()
    media_removed = enforce_media_quota(MEDIA_DIR, storage.get_global().media_max_total_mb)
    total = removed + media_removed
    if total:
        logger.info("Стартовая очистка: кэш %s, медиа-квота %s", removed, media_removed)
    return total
