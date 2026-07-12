from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.settings import (
    ADMIN_BACKUP_FIELDS,
    ADMIN_CACHE_FIELDS,
    ADMIN_DATA_FIELDS,
    COMMAND_FIELDS,
    EXTRA_FIELDS,
    MISC_FIELDS,
    NOTIFICATIONS_FIELDS,
    GlobalSettings,
    OwnerSettings,
)

SECTION_FIELDS = {
    "notif": NOTIFICATIONS_FIELDS,
    "extra": EXTRA_FIELDS,
    "cmds": COMMAND_FIELDS,
    "misc": MISC_FIELDS,
}
ADMIN_SECTION_FIELDS = {
    "backup": ADMIN_BACKUP_FIELDS,
    "cache": ADMIN_CACHE_FIELDS,
    "data": ADMIN_DATA_FIELDS,
}


# --------------------------------------------------------------- user /settings
def main_settings_keyboard(digest_count: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="us:open:notif")],
        [InlineKeyboardButton(text="🧩 Доп. функции", callback_data="us:open:extra")],
        [InlineKeyboardButton(text="🛠 Команды", callback_data="us:open:cmds")],
        [InlineKeyboardButton(text="🗂 Пресеты .say", callback_data="us:open:presets")],
        [InlineKeyboardButton(text="📤 Экспорт переписки", callback_data="us:export")],
        [InlineKeyboardButton(text="👁‍🗨 Последние сообщения", callback_data="us:recent")],
        [InlineKeyboardButton(text="👻 Режим призрака", callback_data="us:open:ghost")],
        [InlineKeyboardButton(text="⚙️ Прочее", callback_data="us:open:misc")],
    ]
    if digest_count:
        rows.append(
            [InlineKeyboardButton(text=f"📬 Показать уведомления ({digest_count})", callback_data="us:digest")]
        )
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="us:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def section_keyboard(section: str, settings: OwnerSettings) -> InlineKeyboardMarkup:
    fields = SECTION_FIELDS[section]
    rows: list[list[InlineKeyboardButton]] = []
    for f in fields:
        value = getattr(settings, f.key)
        if f.kind == "bool":
            state = "✅" if value else "⬜️"
            text = f"{state} {f.label}"
            cb = f"us:toggle:{section}:{f.key}"
        elif f.kind == "cycle":
            label = (f.labels or {}).get(value, str(value))
            text = f"{f.label}: {label}"
            cb = f"us:cycle:{section}:{f.key}"
        else:
            text = f"{f.label}: {value}"
            cb = f"us:edit:{section}:{f.key}"
        rows.append([InlineKeyboardButton(text=text, callback_data=cb)])

    if section == "extra":
        rows.append([InlineKeyboardButton(text="✏️ Текст AFK-ответа", callback_data="us:afktext")])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def presets_keyboard(names: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for name in names:
        rows.append(
            [
                InlineKeyboardButton(text=f"🗂 {name}", callback_data="us:noop"),
                InlineKeyboardButton(text="🗑 удалить", callback_data=f"us:preset:del:{name}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Новый пресет", callback_data="us:preset:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chats_export_keyboard(chats: list[tuple[int, str, int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title, count in chats[:20]:
        label = f"{title} ({count})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"us:export:chat:{chat_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chats_recent_keyboard(chats: list[tuple[int, str, int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title, count in chats[:20]:
        label = f"{title} ({count})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"us:recent:chat:{chat_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recent_count_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    counts = [10, 20, 50, 100]
    row = [InlineKeyboardButton(text=str(n), callback_data=f"us:recent:show:{chat_id}:{n}") for n in counts]
    return InlineKeyboardMarkup(inline_keyboard=[row, [InlineKeyboardButton(text="⬅️ Назад", callback_data="us:recent")]])


def ghost_settings_keyboard(enabled: bool, operators: list) -> InlineKeyboardMarkup:
    state = "✅" if enabled else "⬜️"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"{state} Режим призрака включён", callback_data="gs:toggle")],
    ]
    if enabled:
        rows.append([InlineKeyboardButton(text="🔗 Привязать второй аккаунт", callback_data="gs:gencode")])
        for op in operators:
            label = f"👤 id{op['operator_user_id']} — отвязать"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"gs:unlink:{op['operator_user_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ghost_picker_keyboard(chats: list[tuple[int, str, int, int, bool]]) -> InlineKeyboardMarkup:
    """chats: (chat_id, title, total, unread, pinned) — уже отсортированы (закреплённые первыми)."""
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title, _total, unread, pinned in chats[:25]:
        prefix = "📌 " if pinned else ""
        suffix = f" ({unread})" if unread else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{title}{suffix}", callback_data=f"gh:open:{chat_id}")])
    rows.append([InlineKeyboardButton(text="🔍 Поиск чата", callback_data="gh:search")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ghost_session_keyboard(chat_id: int, *, pinned: bool, has_unread: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_unread:
        rows.append([InlineKeyboardButton(text="✅ Прочитать", callback_data=f"gh:read:{chat_id}")])
    pin_label = "📌 Открепить" if pinned else "📌 Закрепить"
    rows.append(
        [
            InlineKeyboardButton(text=pin_label, callback_data=f"gh:pin:{chat_id}"),
            InlineKeyboardButton(text="⬅️ Список чатов", callback_data="gh:list"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preset_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="us:preset:done")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="us:preset:cancel")],
        ]
    )


def close_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="us:close")]])


# -------------------------------------------------------------------- /admin
def admin_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📦 Бэкапы", callback_data="ad:open:backup")],
        [InlineKeyboardButton(text="📥 Кэш и медиа", callback_data="ad:open:cache")],
        [InlineKeyboardButton(text="💾 Данные", callback_data="ad:open:data")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="ad:open:users")],
        [InlineKeyboardButton(text="📤 Сделать бэкап сейчас", callback_data="ad:backupnow")],
        [InlineKeyboardButton(text="📢 Рассылка всем", callback_data="ad:broadcast")],
        [InlineKeyboardButton(text="✖️ Закрыть", callback_data="ad:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_section_keyboard(section: str, settings: GlobalSettings) -> InlineKeyboardMarkup:
    fields = ADMIN_SECTION_FIELDS[section]
    rows: list[list[InlineKeyboardButton]] = []
    for f in fields:
        value = getattr(settings, f.key)
        if f.kind == "bool":
            state = "✅" if value else "⬜️"
            text = f"{state} {f.label}"
            cb = f"ad:toggle:{section}:{f.key}"
        else:
            text = f"{f.label}: {value}"
            cb = f"ad:edit:{section}:{f.key}"
        rows.append([InlineKeyboardButton(text=text, callback_data=cb)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")]])


def admin_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="ad:cancel")]])


# -------------------------------------------------------------------- /help
HELP_TOPICS: list[tuple[str, str]] = [
    ("cmd_spam", "💬 .spam"),
    ("cmd_mute", "🔇 .mute / .unmute"),
    ("cmd_typing", "⌨️ .typing"),
    ("cmd_mock", "🔤 .mock"),
    ("cmd_reverse", "🔁 .reverse"),
    ("cmd_tr", "🌐 .tr"),
    ("cmd_qr", "🔳 .qr"),
    ("cmd_short", "🔗 .short"),
    ("cmd_id", "🆔 .id"),
    ("cmd_ping", "🏓 .ping"),
    ("cmd_say", "🗣 .say"),
    ("cmd_view", "🕶 .view"),
    ("cmd_watch", "👁 .watch / .unwatch"),
    ("feat_afk", "💤 Режим AFK"),
    ("feat_anon", "🎭 Анонимные стикеры"),
    ("feat_spoiler", "🙈 Анти-спойлер"),
    ("feat_search", "🕵️ Антипоиск"),
    ("feat_notify", "🔔 Уведомления"),
]


def help_topics_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, callback_data=f"help:topic:{key}")] for key, title in HELP_TOPICS]
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="help:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К списку тем", callback_data="help:back")]])
