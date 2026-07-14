from __future__ import annotations

import asyncio
import logging
import shutil

from bot.backup import BackupManager
from bot.storage import Storage

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 300
PURGE_BATCH_SIZE = 500
MAX_BATCHES_PER_RUN = 200  # защита от бесконечного цикла, если места всё равно не хватает


async def run_disk_guard_loop(storage: Storage, backup: BackupManager) -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await _check_once(storage, backup)
        except Exception:
            logger.exception("Ошибка в диск-guard'е")


async def _check_once(storage: Storage, backup: BackupManager) -> None:
    min_free_gb = storage.get_global().min_free_disk_gb
    free_gb = _free_space_gb(storage)
    if free_gb >= min_free_gb:
        return

    logger.warning(
        "Свободно на диске %.2f ГБ < порога %.2f ГБ — делаю бэкап и запускаю аварийную очистку",
        free_gb, min_free_gb,
    )

    ok = await backup.backup_now()
    if not ok:
        logger.error("Аварийный бэкап не удался — очистку всё равно продолжаю, чтобы не забить диск")

    removed_total = 0
    for _ in range(MAX_BATCHES_PER_RUN):
        if _free_space_gb(storage) >= min_free_gb:
            break
        removed = storage.db.purge_oldest_batch(PURGE_BATCH_SIZE)
        removed_total += removed
        if removed == 0:
            break  # больше нечего удалять

    if removed_total:
        storage.db.vacuum()

    logger.warning(
        "Аварийная очистка завершена: удалено %s записей, свободно теперь %.2f ГБ",
        removed_total, _free_space_gb(storage),
    )


def _free_space_gb(storage: Storage) -> float:
    usage = shutil.disk_usage(storage.db.path.parent)
    return usage.free / (1024 ** 3)
