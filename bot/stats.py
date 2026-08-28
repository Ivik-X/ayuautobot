from __future__ import annotations

from dataclasses import dataclass, field

KIND_LABELS = {
    "text": "📝 текст",
    "voice": "🎤 гс",
    "video_note": "⭕ кружки",
    "photo": "📷 фото",
    "video": "🎬 видео",
    "audio": "🎵 аудио",
    "document": "📎 файлы",
    "sticker": "🙂 стикеры",
    "animation": "GIF",
    "other": "📦 прочее",
}


@dataclass(slots=True)
class ChatStats:
    title: str
    total: int = 0
    kinds: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str, count: int = 1) -> None:
        self.total += count
        self.kinds[kind] = self.kinds.get(kind, 0) + count

    def top_kinds(self, limit: int = 4) -> list[tuple[str, int]]:
        return sorted(self.kinds.items(), key=lambda item: item[1], reverse=True)[:limit]


def format_kind_line(kinds: list[tuple[str, int]]) -> str:
    if not kinds:
        return ""
    parts = [f"{KIND_LABELS.get(kind, kind)}: {count}" for kind, count in kinds]
    return " · ".join(parts)


def format_chats_report(
    rows: list[tuple[int, ChatStats]],
    *,
    cache_size: int,
    media_files: int,
    ttl_hours: float,
    max_entries: int,
    db_active: int | None = None,
    db_total: int | None = None,
) -> str:
    lines = [
        "<b>📊 Статистика чатов</b>",
        f"Кэш RAM: <b>{cache_size}</b> / {max_entries} · медиа: <b>{media_files}</b>",
        f"Автоочистка кэша: <b>{ttl_hours:g}</b> ч",
    ]
    if db_active is not None:
        lines.append(f"БД: <b>{db_active}</b> активных · <b>{db_total or 0}</b> всего (с удалёнными)")
    lines.append("")

    if not rows:
        lines.append("Пока нет данных — дождитесь сообщений от собеседников.")
        return "\n".join(lines)

    for index, (chat_id, stats) in enumerate(rows[:10], start=1):
        lines.append(f"<b>{index}. {stats.title}</b> — {stats.total} сообщ.")
        kind_line = format_kind_line(stats.top_kinds())
        if kind_line:
            lines.append(f"   {kind_line}")
        lines.append(f"   <code>id:{chat_id}</code>")

    if len(rows) > 10:
        lines.append(f"\n<i>…и ещё {len(rows) - 10} чатов</i>")

    return "\n".join(lines)


def format_admin_overview(
    *,
    owners_count: int,
    connections_count: int,
    db_size_mb: float,
    db_messages: int,
    db_messages_total: int,
    media_mb: float,
    backup_enabled: bool,
    backup_interval_hours: float,
    last_backup_ts: float | None,
    total_stars: int = 0,
    db_max_size_gb: float = 20.0,
    media_max_total_mb: int = 7168,
    media_max_age_hours: float = 168.0,

    media_max_file_mb: int = 50,
    active_users_48h: int = 0,
    msgs_48h: int = 0,
    media_msgs_48h: int = 0,
    edits_48h: int = 0,
    deletes_48h: int = 0,
) -> str:
    import time as _time

    last_backup = "ещё не выполнялся"
    if last_backup_ts:
        ago_min = int((_time.time() - last_backup_ts) // 60)
        last_backup = f"{ago_min} мин. назад"

    db_usage_pct = (db_size_mb / 1024 / db_max_size_gb * 100) if db_max_size_gb > 0 else 0
    media_usage_pct = (media_mb / media_max_total_mb * 100) if media_max_total_mb > 0 else 0


    return (
        "<b>🛠 Панель администратора</b>\n\n"
        f"📊 <b>Статистика системы за 48 часов:</b>\n"
        f"👥 Активных пользователей: <b>{active_users_48h}</b> / {owners_count}\n"
        f"💬 Сообщений обработано: <b>{msgs_48h}</b>\n"
        f"🖼 Сообщений с медиа: <b>{media_msgs_48h}</b>\n"
        f"✏️ Отредактировано: <b>{edits_48h}</b> · 🗑 Удалено: <b>{deletes_48h}</b>\n\n"
        f"💾 <b>БД:</b> {db_size_mb:.1f} МБ / {db_max_size_gb:.0f} ГБ ({db_usage_pct:.0f}%)\n"
        f"🗂 Сообщений в БД: <b>{db_messages}</b> активных (всего <b>{db_messages_total}</b>)\n\n"
        f"🖼 <b>Медиа-хранилище:</b> {media_mb:.1f} МБ / {media_max_total_mb // 1024} ГБ ({media_usage_pct:.0f}%)\n"
        f"⏰ TTL медиа: <b>{media_max_age_hours:g} ч</b> · Макс. файл: <b>{media_max_file_mb} МБ</b>\n\n"
        f"📦 Автобэкап: <b>{'вкл' if backup_enabled else 'выкл'}</b> (каждые {backup_interval_hours:g} ч)\n"
        f"🕓 Последний бэкап: {last_backup}"
    )

