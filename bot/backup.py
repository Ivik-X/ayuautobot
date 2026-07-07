from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
import time
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from bot.config import Config
from bot.media import MEDIA_DIR, directory_size_bytes, enforce_media_quota
from bot.storage import Storage

logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parent.parent / "data" / "tmp"


class BackupManager:
    """Раз в BACKUP_INTERVAL_HOURS часов:

    1. делает консистентный снимок SQLite (не останавливая бота);
    2. гзипует и отправляет админам в личку одним файлом;
    3. после успешной отправки схлопывает локальную историю (VACUUM),
       чтобы реально освободить место на диске;
    4. попутно следит за квотой на медиа-файлы.

    Это единственный способ держать 10 GB NVMe в порядке при постоянно
    растущей истории переписок — вся "холодная" история живёт у админа в ЛС,
    а на сервере остаётся только горячий хвост.
    """

    def __init__(self, bot: Bot, storage: Storage, config: Config) -> None:
        self._bot = bot
        self._storage = storage
        self._config = config
        self.last_backup_ts: float | None = None

    async def run_loop(self) -> None:
        if not self._config.backup.enabled or not self._config.admin_ids:
            logger.info("Автобэкап отключён (BACKUP_ENABLED=false или нет ADMIN_IDS)")
            return
        interval = self._config.backup.interval_hours * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                await self.backup_now()
            except Exception:
                logger.exception("Ошибка автобэкапа")

    async def backup_now(self) -> bool:
        if not self._config.admin_ids:
            logger.warning("Бэкап пропущен: не задан ни один ADMIN_IDS")
            return False

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        snapshot_path = TMP_DIR / f"backup-{stamp}.sqlite3"
        send_path = snapshot_path

        try:
            self._storage.db.snapshot(snapshot_path)

            if self._config.backup.compress:
                gz_path = snapshot_path.with_suffix(snapshot_path.suffix + ".gz")
                with open(snapshot_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                snapshot_path.unlink(missing_ok=True)
                send_path = gz_path

            size_mb = send_path.stat().st_size / (1024 * 1024)
            caption = (
                "🗄 <b>Бэкап базы данных</b>\n"
                f"Дата: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Размер: {size_mb:.2f} МБ"
            )

            sent_to_anyone = False
            for admin_id in self._config.admin_ids:
                try:
                    await self._bot.send_document(
                        chat_id=admin_id,
                        document=FSInputFile(send_path),
                        caption=caption,
                    )
                    sent_to_anyone = True
                except Exception:
                    logger.exception("Не удалось отправить бэкап админу %s", admin_id)

            if not sent_to_anyone:
                logger.error("Бэкап не удалось отправить ни одному админу — локальные данные НЕ удаляются")
                return False

            removed_rows = self._storage.db.trim_after_backup(self._config.backup.keep_local_hours)
            removed_media = enforce_media_quota(MEDIA_DIR, self._config.media.max_total_mb)
            self.last_backup_ts = time.time()
            logger.info(
                "Бэкап отправлен (%.2f МБ), локально удалено %s записей и %s медиафайлов",
                size_mb, removed_rows, removed_media,
            )
            return True
        finally:
            send_path.unlink(missing_ok=True)

    def storage_report(self) -> str:
        db_mb = self._storage.db.file_size_bytes() / (1024 * 1024)
        media_mb = directory_size_bytes(MEDIA_DIR) / (1024 * 1024)
        return f"БД: {db_mb:.1f} МБ · медиа: {media_mb:.1f} МБ"
