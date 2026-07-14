from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from bot.settings import GlobalSettings
from bot.texts import Texts, load_texts

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int_set(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    result: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            try:
                result.add(int(chunk))
            except ValueError:
                pass
    return result


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    url: str            # публичный https-адрес, напр. https://bot.example.com
    path: str
    secret: str
    host: str
    port: int

    @property
    def enabled(self) -> bool:
        return bool(self.url)


@dataclass(frozen=True, slots=True)
class SttConfig:
    enabled: bool
    model_size: str          # tiny / base / small / medium / large-v3
    language: str | None     # None = автоопределение
    models_dir: Path


@dataclass(frozen=True, slots=True)
class Config:
    """Инфраструктурные настройки (задаются один раз через .env / переменные окружения).

    Всё, что можно менять на лету без передеплоя (бэкапы, кэш, квоты медиа,
    сохранение всех сообщений, персональные настройки владельцев) живёт в БД
    и управляется через /admin и /settings — см. bot.settings.
    """

    bot_token: str
    admin_ids: set[int]
    db_path: Path
    seed_global_settings: GlobalSettings
    stt: SttConfig
    webhook: WebhookConfig
    texts: Texts = field(default_factory=load_texts)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Укажите BOT_TOKEN в переменных окружения или в файле .env")

    db_path = Path(os.getenv("DB_PATH", str(DATA_DIR / "bot.db")))
    if not db_path.is_absolute():
        db_path = DATA_DIR.parent / db_path

    seed = GlobalSettings(
        backup_enabled=_bool("BACKUP_ENABLED", True),
        backup_interval_hours=_float("BACKUP_INTERVAL_HOURS", 12),
        backup_keep_local_hours=_float("BACKUP_KEEP_LOCAL_HOURS", 6),
        backup_compress=_bool("BACKUP_COMPRESS", True),
        cache_max_entries=_int("CACHE_MAX_ENTRIES", 800),
        cache_cleanup_interval_min=_int("CACHE_CLEANUP_INTERVAL_MIN", 10),
        media_max_total_mb=_int("MEDIA_MAX_TOTAL_MB", 2048),
        min_free_disk_gb=_float("MIN_FREE_DISK_GB", 5.0),
        profile_watch_interval_min=_int("PROFILE_WATCH_INTERVAL_MIN", 30),
    )

    stt = SttConfig(
        enabled=_bool("STT_ENABLED", False),
        model_size=os.getenv("STT_MODEL_SIZE", "base").strip() or "base",
        language=(os.getenv("STT_LANGUAGE", "").strip() or None),
        models_dir=DATA_DIR / "stt_models",
    )

    webhook = WebhookConfig(
        url=os.getenv("WEBHOOK_URL", "").strip().rstrip("/"),
        path=os.getenv("WEBHOOK_PATH", "/webhook").strip(),
        secret=os.getenv("WEBHOOK_SECRET", "").strip(),
        host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip(),
        port=_int("WEBAPP_PORT", 8080),
    )

    return Config(
        bot_token=token,
        admin_ids=_int_set("ADMIN_IDS"),
        db_path=db_path,
        seed_global_settings=seed,
        stt=stt,
        webhook=webhook,
        texts=load_texts(),
    )
