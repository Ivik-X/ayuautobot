from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Any

DEFAULT_AFK_TEXT = "Сейчас недоступен(на), отвечу как только смогу 🙌"

NOTIFY_MODES = ["off", "instant", "silent", "digest"]
NOTIFY_MODE_LABELS = {
    "off": "🚫 выключено",
    "instant": "🔔 сразу",
    "silent": "🔕 сразу, без звука",
    "digest": "📬 копить и показывать по запросу",
}


# ------------------------------------------------------------- owner settings
@dataclass(slots=True)
class OwnerSettings:
    """Персональные настройки владельца бизнес-подключения."""

    # Уведомления
    notify_delete_mode: str = "instant"     # off / instant / silent / digest
    notify_edit_mode: str = "instant"       # off / instant / silent / digest
    notify_own_deletions: bool = False      # показывать даже удаления, сделанные самим ботом (mute/.view/команды)

    # Доп. функции
    afk_enabled: bool = False
    afk_text: str = DEFAULT_AFK_TEXT
    anon_stickers: bool = False             # пересылать стикеры как картинку без ссылки на стикерпак
    anti_search: bool = False               # подменять буквы на визуальные twin-символы (антипоиск)
    ghost_mode_enabled: bool = False        # разрешить управление чатами через /ghost (себе и привязанным операторам)

    # Команды (включение/выключение целиком)
    cmd_spam: bool = True
    cmd_mute: bool = True
    cmd_typing: bool = True
    cmd_mock: bool = True
    cmd_reverse: bool = True
    cmd_tr: bool = True
    cmd_qr: bool = True
    cmd_short: bool = True
    cmd_id: bool = True
    cmd_ping: bool = True
    cmd_say: bool = True
    cmd_view: bool = True
    cmd_watch: bool = True

    # Прочее
    cache_ttl_hours: float = 24
    mute_default_seconds: int = 60

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None, default: "OwnerSettings") -> "OwnerSettings":
        if not raw:
            return replace(default)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return replace(default)
        merged = asdict(replace(default))
        for key, value in data.items():
            if key in merged:
                merged[key] = value
        return cls(**merged)


COMMAND_FLAG = {
    "spam": "cmd_spam",
    "mute": "cmd_mute",
    "unmute": "cmd_mute",
    "typing": "cmd_typing",
    "mock": "cmd_mock",
    "reverse": "cmd_reverse",
    "tr": "cmd_tr",
    "qr": "cmd_qr",
    "short": "cmd_short",
    "id": "cmd_id",
    "ping": "cmd_ping",
    "say": "cmd_say",
    "view": "cmd_view",
    "watch": "cmd_watch",
    "unwatch": "cmd_watch",
}


@dataclass(frozen=True, slots=True)
class SettingField:
    key: str
    label: str
    kind: str  # "bool" | "cycle" | "float" | "int" | "text"
    options: list[str] | None = None
    labels: dict[str, str] | None = None


NOTIFICATIONS_FIELDS: list[SettingField] = [
    SettingField("notify_delete_mode", "🗑 Удаления", "cycle", NOTIFY_MODES, NOTIFY_MODE_LABELS),
    SettingField("notify_edit_mode", "✏️ Правки", "cycle", NOTIFY_MODES, NOTIFY_MODE_LABELS),
    SettingField("notify_own_deletions", "🧹 Абсолютно все удаленки (даже от самого бота)", "bool"),
]

EXTRA_FIELDS: list[SettingField] = [
    SettingField("afk_enabled", "💤 Режим AFK (автоответ)", "bool"),
    SettingField("anon_stickers", "🎭 Анонимные стикеры", "bool"),
    SettingField("anti_search", "🕵️ Антипоиск (подмена букв)", "bool"),
]

COMMAND_FIELDS: list[SettingField] = [
    SettingField("cmd_spam", ".spam", "bool"),
    SettingField("cmd_mute", ".mute / .unmute", "bool"),
    SettingField("cmd_typing", ".typing", "bool"),
    SettingField("cmd_mock", ".mock", "bool"),
    SettingField("cmd_reverse", ".reverse", "bool"),
    SettingField("cmd_tr", ".tr", "bool"),
    SettingField("cmd_qr", ".qr", "bool"),
    SettingField("cmd_short", ".short", "bool"),
    SettingField("cmd_id", ".id", "bool"),
    SettingField("cmd_ping", ".ping", "bool"),
    SettingField("cmd_say", ".say", "bool"),
    SettingField("cmd_view", ".view", "bool"),
    SettingField("cmd_watch", ".watch / .unwatch", "bool"),
]

MISC_FIELDS: list[SettingField] = [
    SettingField("cache_ttl_hours", "⏱ TTL кэша (часы)", "float"),
    SettingField("mute_default_seconds", "🔇 Mute по умолчанию (сек)", "int"),
]

ALL_OWNER_FIELDS = NOTIFICATIONS_FIELDS + EXTRA_FIELDS + COMMAND_FIELDS + MISC_FIELDS


def get_owner_field(key: str) -> SettingField | None:
    for f in ALL_OWNER_FIELDS:
        if f.key == key:
            return f
    return None


# ------------------------------------------------------------ global settings
@dataclass(slots=True)
class GlobalSettings:
    """Настройки, доступные только администратору (весь бот целиком)."""

    backup_enabled: bool = True
    backup_interval_hours: float = 12
    backup_keep_local_hours: float = 6
    backup_compress: bool = True

    cache_max_entries: int = 800
    cache_cleanup_interval_min: int = 10

    media_max_total_mb: int = 2048

    min_free_disk_gb: float = 5.0  # если свободного места на диске меньше — аварийная очистка (с бэкапом перед этим)
    profile_watch_interval_min: int = 30  # как часто опрашивать watch-листы (имя/фото/username)

    # ------------------------------------------------------------- подписка
    trial_days: int = 3
    price_stars_per_month: int = 50

    free_reveal_limit_month: int = 3        # сколько раз в месяц можно раскрыть скрытое уведомление
    free_presets_max: int = 1               # максимум пресетов .say
    free_spam_messages_month: int = 20      # лимит .spam на месяц (суммарно сообщений)
    free_mute_seconds_month: int = 180      # лимит суммарной длительности .mute на месяц

    free_view_enabled: bool = False
    free_afk_enabled: bool = False
    free_antisearch_enabled: bool = False
    free_stt_enabled: bool = False
    free_ghost_enabled: bool = False
    free_extra_features_enabled: bool = False  # анонимные стикеры, .watch, экспорт, последние сообщения

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None, default: "GlobalSettings") -> "GlobalSettings":
        if not raw:
            return replace(default)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return replace(default)
        merged = asdict(replace(default))
        for key, value in data.items():
            if key in merged:
                merged[key] = value
        return cls(**merged)


ADMIN_BACKUP_FIELDS: list[SettingField] = [
    SettingField("backup_enabled", "📦 Автобэкап включён", "bool"),
    SettingField("backup_interval_hours", "⏲ Интервал бэкапа (часы)", "float"),
    SettingField("backup_keep_local_hours", "🗄 Хранить локально (часы)", "float"),
    SettingField("backup_compress", "🗜 Сжимать бэкап (gzip)", "bool"),
]

ADMIN_CACHE_FIELDS: list[SettingField] = [
    SettingField("cache_max_entries", "📥 Макс. записей в RAM-кэше", "int"),
    SettingField("cache_cleanup_interval_min", "🧹 Интервал очистки кэша (мин)", "int"),
    SettingField("media_max_total_mb", "🖼 Квота на медиа (МБ)", "int"),
    SettingField("profile_watch_interval_min", "👁 Интервал опроса watch-листа (мин)", "int"),
]

ADMIN_DATA_FIELDS: list[SettingField] = [
    SettingField("min_free_disk_gb", "💾 Мин. свободно на диске (ГБ) до аварийной очистки", "float"),
]

ADMIN_BILLING_FIELDS: list[SettingField] = [
    SettingField("trial_days", "🎁 Длительность пробного периода (дней)", "int"),
    SettingField("price_stars_per_month", "⭐ Цена подписки (звёзд/месяц)", "int"),
]

ADMIN_LIMITS_FIELDS: list[SettingField] = [
    SettingField("free_reveal_limit_month", "🔓 Раскрытий скрытых уведомлений/мес", "int"),
    SettingField("free_presets_max", "🗂 Макс. пресетов .say", "int"),
    SettingField("free_spam_messages_month", "💬 Лимит .spam сообщений/мес", "int"),
    SettingField("free_mute_seconds_month", "🔇 Лимит .mute секунд/мес", "int"),
    SettingField("free_view_enabled", "🕶 .view доступен на бесплатном", "bool"),
    SettingField("free_afk_enabled", "💤 AFK доступен на бесплатном", "bool"),
    SettingField("free_antisearch_enabled", "🕵️ Антипоиск доступен на бесплатном", "bool"),
    SettingField("free_stt_enabled", "🎙 Расшифровка гс доступна на бесплатном", "bool"),
    SettingField("free_ghost_enabled", "👻 Режим призрака доступен на бесплатном", "bool"),
    SettingField(
        "free_extra_features_enabled",
        "🧩 Остальное (анон. стикеры, .watch, экспорт, последние сообщения) доступно на бесплатном",
        "bool",
    ),
]

ALL_ADMIN_FIELDS = (
    ADMIN_BACKUP_FIELDS + ADMIN_CACHE_FIELDS + ADMIN_DATA_FIELDS + ADMIN_BILLING_FIELDS + ADMIN_LIMITS_FIELDS
)


def get_admin_field(key: str) -> SettingField | None:
    for f in ALL_ADMIN_FIELDS:
        if f.key == key:
            return f
    return None


_BOOL_TRUE = {"1", "true", "on", "yes", "да", "вкл"}
_BOOL_FALSE = {"0", "false", "off", "no", "нет", "выкл"}


def parse_value(kind: str, raw: str) -> Any:
    raw = raw.strip()
    if kind == "bool":
        lowered = raw.lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        raise ValueError("ожидалось on/off")
    if kind == "float":
        return float(raw.replace(",", "."))
    if kind == "int":
        return int(raw)
    return raw


def next_cycle_value(field_: SettingField, current: str) -> str:
    options = field_.options or []
    if current not in options:
        return options[0]
    idx = (options.index(current) + 1) % len(options)
    return options[idx]
