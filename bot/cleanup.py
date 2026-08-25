from __future__ import annotations

import asyncio
import logging

from bot.media import MEDIA_DIR, enforce_media_age, enforce_media_quota
from bot.storage import Storage

logger = logging.getLogger(__name__)


async def run_cleanup_loop(storage: Storage) -> None:
    while True:
        settings = storage.get_global()
        interval_min = settings.cache_cleanup_interval_min
        await asyncio.sleep(max(interval_min, 1) * 60)

        settings = storage.get_global()  # перечитываем после сна — настройки могли измениться

        # 1. Чистка RAM-кэша по персональному TTL владельцев
        removed_cache = storage.purge_expired_all()

        # 2. Удалить медиафайлы старше TTL (по умолчанию 48 ч)
        age_removed = enforce_media_age(MEDIA_DIR, settings.media_max_age_hours)

        # 3. Квота на суммарный объём медиа на диске (по умолчанию 7 ГБ)
        quota_removed = enforce_media_quota(MEDIA_DIR, settings.media_max_total_mb)

        # 4. Лимит размера БД (по умолчанию 20 ГБ) — удаляем самые старые строки
        db_removed = storage.db.enforce_db_size_limit(settings.db_max_size_gb)

        total_media = age_removed + quota_removed
        if removed_cache or total_media or db_removed:
            logger.info(
                "Автоочистка: кэш %s записей, медиа-age %s, медиа-квота %s файлов, БД %s строк",
                removed_cache, age_removed, quota_removed, db_removed,
            )


def startup_cleanup(storage: Storage) -> int:
    settings = storage.get_global()

    removed_cache = storage.purge_expired_all()
    age_removed = enforce_media_age(MEDIA_DIR, settings.media_max_age_hours)
    quota_removed = enforce_media_quota(MEDIA_DIR, settings.media_max_total_mb)
    db_removed = storage.db.enforce_db_size_limit(settings.db_max_size_gb)

    total = removed_cache + age_removed + quota_removed + db_removed
    if total:
        logger.info(
            "Стартовая очистка: кэш %s, медиа-age %s, медиа-квота %s, БД %s",
            removed_cache, age_removed, quota_removed, db_removed,
        )
    return total

