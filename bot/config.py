from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from bot.settings import OwnerSettings
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
class BackupConfig:
    enabled: bool
    interval_hours: float
    keep_local_hours: float
    compress: bool


@dataclass(frozen=True, slots=True)
class CacheConfig:
    max_entries: int
    cleanup_interval_min: int


@dataclass(frozen=True, slots=True)
class MediaConfig:
    max_total_mb: int


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: set[int]
    db_path: Path
    cache: CacheConfig
    media: MediaConfig
    backup: BackupConfig
    default_settings: OwnerSettings
    texts: Texts = field(default_factory=load_texts)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Укажите BOT_TOKEN в переменных окружения или в файле .env")

    db_path = Path(os.getenv("DB_PATH", str(DATA_DIR / "bot.db")))
    if not db_path.is_absolute():
        db_path = DATA_DIR.parent / db_path

    return Config(
        bot_token=token,
        admin_ids=_int_set("ADMIN_IDS"),
        db_path=db_path,
        cache=CacheConfig(
            max_entries=_int("CACHE_MAX_ENTRIES", 800),
            cleanup_interval_min=_int("CACHE_CLEANUP_INTERVAL_MIN", 10),
        ),
        media=MediaConfig(
            max_total_mb=_int("MEDIA_MAX_TOTAL_MB", 2048),
        ),
        backup=BackupConfig(
            enabled=_bool("BACKUP_ENABLED", True),
            interval_hours=_float("BACKUP_INTERVAL_HOURS", 12),
            keep_local_hours=_float("BACKUP_KEEP_LOCAL_HOURS", 6),
            compress=_bool("BACKUP_COMPRESS", True),
        ),
        default_settings=OwnerSettings(
            cache_ttl_hours=_float("DEFAULT_CACHE_TTL_HOURS", 24),
            store_history=_bool("DEFAULT_STORE_HISTORY", True),
            save_media=_bool("DEFAULT_SAVE_MEDIA", True),
            notify_edit=_bool("DEFAULT_NOTIFY_EDIT", True),
            notify_delete=_bool("DEFAULT_NOTIFY_DELETE", True),
            mute_default_seconds=_int("DEFAULT_MUTE_SECONDS", 60),
            afk_enabled=False,
            afk_text="Сейчас недоступен(на), отвечу как только смогу 🙌",
            feature_fun=True,
            feature_utility=True,
        ),
        texts=load_texts(),
    )
