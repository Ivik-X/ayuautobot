import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot.texts import Texts, load_texts

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DbConfig:
    enabled: bool
    path: Path
    save_media: bool
    retention_days: int


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    cache_ttl_hours: float
    cache_max_entries: int
    cleanup_interval_min: int
    save_media: bool
    db: DbConfig
    texts: Texts


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Укажите BOT_TOKEN в переменных окружения или в файле .env")

    db_path = Path(os.getenv("DB_PATH", str(DATA_DIR / "bot.db")))
    if not db_path.is_absolute():
        db_path = DATA_DIR.parent / db_path

    return Config(
        bot_token=token,
        cache_ttl_hours=_float("CACHE_TTL_HOURS", 12),
        cache_max_entries=_int("CACHE_MAX_ENTRIES", 1500),
        cleanup_interval_min=_int("CACHE_CLEANUP_INTERVAL_MIN", 10),
        save_media=_bool("SAVE_MEDIA", True),
        db=DbConfig(
            enabled=_bool("DB_ENABLED", False),
            path=db_path,
            save_media=_bool("DB_SAVE_MEDIA", False),
            retention_days=_int("DB_RETENTION_DAYS", 0),
        ),
        texts=load_texts(),
    )
