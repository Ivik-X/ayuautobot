from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable


@dataclass(slots=True)
class OwnerSettings:
    """Персональные настройки владельца бизнес-аккаунта.

    Хранятся как JSON в таблице owners и могут независимо переопределяться
    каждым пользователем бота (мульти-юзер: у каждого владельца свой профиль).
    """

    cache_ttl_hours: float = 24
    store_history: bool = True          # писать ли историю в SQLite (privacy toggle)
    save_media: bool = True             # скачивать ли медиа локально
    notify_edit: bool = True            # уведомлять о правках сообщений
    notify_delete: bool = True          # уведомлять об удалениях
    mute_default_seconds: int = 60
    afk_enabled: bool = False
    afk_text: str = "Сейчас недоступен(на), отвечу как только смогу 🙌"
    feature_fun: bool = True            # .mock/.clown/.owo/.zalgo/.bomb/.love/.dice/.flip
    feature_utility: bool = True        # .tr/.weather/.currency/.qr/.short/.calc

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


@dataclass(frozen=True, slots=True)
class SettingField:
    key: str
    label: str
    kind: str  # "bool" | "float" | "int" | "text"
    parser: Callable[[str], Any] | None = None


SETTINGS_FIELDS: list[SettingField] = [
    SettingField("store_history", "📚 Хранить историю в БД", "bool"),
    SettingField("save_media", "🖼 Сохранять медиа локально", "bool"),
    SettingField("notify_edit", "✏️ Уведомлять о правках", "bool"),
    SettingField("notify_delete", "🗑 Уведомлять об удалениях", "bool"),
    SettingField("feature_fun", "🎉 Развлекательные команды", "bool"),
    SettingField("feature_utility", "🛠 Утилитарные команды", "bool"),
    SettingField("afk_enabled", "💤 AFK автоответ", "bool"),
    SettingField("cache_ttl_hours", "⏱ TTL кэша (часы)", "float"),
    SettingField("mute_default_seconds", "🔇 Mute по умолчанию (сек)", "int"),
]

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


def get_field(key: str) -> SettingField | None:
    for field_ in SETTINGS_FIELDS:
        if field_.key == key:
            return field_
    return None
