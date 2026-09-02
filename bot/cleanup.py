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

        # 2. Удалить медиафайлы старше TTL
        age_removed = enforce_media_age(MEDIA_DIR, settings.media_max_age_hours)

        # 3. Удалить из БД строки сообщений старше TTL (7 дней)
        db_ttl_removed = storage.db.purge_messages_older_than(settings.media_max_age_hours)

        # 4. Квота медиа на диске с учётом резерва 2 ГБ
        media_reserve_mb = int(getattr(settings, "media_reserve_gb", 2.0) * 1024)
        effective_media_quota_mb = max(settings.media_max_total_mb - media_reserve_mb, 512)
        quota_removed = enforce_media_quota(MEDIA_DIR, effective_media_quota_mb)

        # 5. Лимит размера БД с учётом резерва 2 ГБ
        db_reserve_gb = getattr(settings, "db_reserve_gb", 2.0)
        effective_db_limit_gb = max(settings.db_max_size_gb - db_reserve_gb, 1.0)
        db_removed = storage.db.enforce_db_size_limit(effective_db_limit_gb)

        total_media = age_removed + quota_removed
        total_db = db_ttl_removed + db_removed
        if removed_cache or total_media or total_db:
            logger.info(
                "Автоочистка: кэш %s записей, медиа-age %s, БД-TTL %s записей, "
                "медиа-квота %s файлов (лимит %dМБ), БД-лимит %s строк (лимит %.1fГБ)",
                removed_cache, age_removed, db_ttl_removed, quota_removed,
                effective_media_quota_mb, db_removed, effective_db_limit_gb,
            )


def startup_cleanup(storage: Storage) -> int:
    settings = storage.get_global()

    removed_cache = storage.purge_expired_all()
    age_removed = enforce_media_age(MEDIA_DIR, settings.media_max_age_hours)
    db_ttl_removed = storage.db.purge_messages_older_than(settings.media_max_age_hours)

    media_reserve_mb = int(getattr(settings, "media_reserve_gb", 2.0) * 1024)
    effective_media_quota_mb = max(settings.media_max_total_mb - media_reserve_mb, 512)
    quota_removed = enforce_media_quota(MEDIA_DIR, effective_media_quota_mb)

    db_reserve_gb = getattr(settings, "db_reserve_gb", 2.0)
    effective_db_limit_gb = max(settings.db_max_size_gb - db_reserve_gb, 1.0)
    db_removed = storage.db.enforce_db_size_limit(effective_db_limit_gb)

    total = removed_cache + age_removed + db_ttl_removed + quota_removed + db_removed
    if total:
        logger.info(
            "Стартовая очистка: кэш %s, медиа-age %s, БД-TTL %s, "
            "медиа-квота %s (лимит %dМБ), БД-лимит %s (лимит %.1fГБ)",
            removed_cache, age_removed, db_ttl_removed, quota_removed,
            effective_media_quota_mb, db_removed, effective_db_limit_gb,
        )
    return total
