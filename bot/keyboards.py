from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def persistent_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Меню")]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напишите сообщение или нажмите Меню",
    )



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


# --------------------------------------------------------------- user /menu
def menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="us:open:notif"),
            InlineKeyboardButton(text="🧩 Доп. функции", callback_data="us:open:extra"),
        ],
        [
            InlineKeyboardButton(text="🛠 Команды", callback_data="us:open:cmds"),
            InlineKeyboardButton(text="⚙️ Параметры", callback_data="us:open:misc"),
        ],
        [
            InlineKeyboardButton(text="🗂 Пресеты .say", callback_data="us:open:presets"),
            InlineKeyboardButton(text="👻 Режим призрака", callback_data="us:open:ghost"),
        ],
        [
            InlineKeyboardButton(text="💬 Последние сообщения", callback_data="us:recent"),
            InlineKeyboardButton(text="🔍 Поиск по базе", callback_data="us:search"),
        ],
        [
            InlineKeyboardButton(text="🟢 Онлайн-режим", callback_data="us:open:online"),
            InlineKeyboardButton(text="⚡️ Действия над чатом", callback_data="us:open:actions"),
        ],
        [
            InlineKeyboardButton(text="📤 Экспорт истории", callback_data="us:export"),
            InlineKeyboardButton(text="🗑 Очистка по слову/рег.", callback_data="us:open:delword"),
        ],
        [InlineKeyboardButton(text="✖️ Закрыть", callback_data="us:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)



def notifications_keyboard(settings: OwnerSettings, digest_count: int) -> InlineKeyboardMarkup:
    """Экран /settings — только уведомления."""
    rows: list[list[InlineKeyboardButton]] = []
    for f in NOTIFICATIONS_FIELDS:
        value = getattr(settings, f.key)
        if f.kind == "bool":
            state = "🟢" if value else "🔴"
            rows.append([InlineKeyboardButton(text=f"{state} {f.label}", callback_data=f"us:toggle:notif:{f.key}")])
        elif f.kind == "cycle":
            label = (f.labels or {}).get(value, str(value))
            rows.append(
                [InlineKeyboardButton(text=f"{f.label}: {label}", callback_data=f"us:cycle:notif:{f.key}")]
            )
    if digest_count:
        rows.append(
            [InlineKeyboardButton(text=f"📬 Показать очередь уведомлений ({digest_count})", callback_data="us:digest")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
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
        rows.append([InlineKeyboardButton(text="✏️ Текст автоответа AFK", callback_data="us:afktext")])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def presets_keyboard(preset_names: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for name in preset_names:
        rows.append(
            [
                InlineKeyboardButton(text=f"📂 {name}", callback_data=f"ps:view:{name}"),
                InlineKeyboardButton(text="🗑", callback_data=f"ps:del:{name}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Создать пресет", callback_data="ps:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preset_view_keyboard(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить фразу", callback_data=f"ps:item:add:{name}")],
            [InlineKeyboardButton(text="🗑 Удалить пресет", callback_data=f"ps:del:{name}")],
            [InlineKeyboardButton(text="⬅️ Назад к пресетам", callback_data="us:open:presets")],
        ]
    )


def chats_export_keyboard(chats: list[tuple[int, str, int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title, count in chats[:20]:
        label = f"{title} ({count})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"us:export:chat:{chat_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chats_recent_keyboard(chats: list[dict] | list[tuple]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in chats[:20]:
        if isinstance(item, dict):
            chat_id = item["chat_id"]
            title = item["title"]
        else:
            chat_id = item[0]
            title = item[1]
        label = f"💬 {title}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"us:recent:chat:{chat_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recent_count_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    counts = [10, 20, 50, 100]
    row = [InlineKeyboardButton(text=str(n), callback_data=f"us:recent:show:{chat_id}:{n}") for n in counts]
    return InlineKeyboardMarkup(inline_keyboard=[row, [InlineKeyboardButton(text="⬅️ Назад", callback_data="us:recent")]])


def ghost_settings_keyboard(enabled: bool, operators: list) -> InlineKeyboardMarkup:
    state = "✅" if enabled else "⬜️"
    btn_text = f"{state} Режим призрака {'включён' if enabled else 'выключен'}"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=btn_text, callback_data="gs:toggle")],
    ]
    if enabled:
        rows.append([InlineKeyboardButton(text="📂 Открыть чаты", callback_data="gh:list")])
        rows.append([InlineKeyboardButton(text="🔗 Привязать второй аккаунт", callback_data="gs:gencode")])
        for op in operators:
            label = f"👤 id{op['operator_user_id']} — отвязать"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"gs:unlink:{op['operator_user_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ghost_picker_keyboard(chats: list[tuple[int, str, int, int, bool]]) -> InlineKeyboardMarkup:
    """chats: (chat_id, title, total, unread, pinned) — уже отсортированы (закреплённые первыми)."""
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title, _total, _unread, pinned in chats[:25]:
        prefix = "📌 " if pinned else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"gh:open:{chat_id}")])
    rows.append([InlineKeyboardButton(text="🔍 Поиск чата", callback_data="gh:search")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ghost_session_keyboard(chat_id: int, *, pinned: bool, has_unread: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_unread:
        rows.append([InlineKeyboardButton(text="✅ Прочитать", callback_data=f"gh:read:{chat_id}")])
    pin_label = "📍 Открепить" if pinned else "📌 Закрепить"
    pin_action = f"gh:unpin:{chat_id}" if pinned else f"gh:pin:{chat_id}"
    rows.append(
        [
            InlineKeyboardButton(text="💬 Открыть", callback_data=f"gh:msgs:{chat_id}"),
            InlineKeyboardButton(text=pin_label, callback_data=pin_action),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад к чатам", callback_data="gh:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ghost_reply_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⌨️ Ответить", callback_data=f"gh:reply:{chat_id}"),
                InlineKeyboardButton(text="✅ Прочитано", callback_data=f"gh:read:{chat_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад к чатам", callback_data="gh:list")],
        ]
    )


def ghost_message_actions_keyboard(chat_id: int, message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"gh:edit:{chat_id}:{message_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"gh:del:{chat_id}:{message_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"gh:msgs:{chat_id}")],
        ]
    )


def close_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="us:close")]])


# -------------------------------------------------------------------- /admin
def admin_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📦 Бэкапы", callback_data="ad:open:backup"),
            InlineKeyboardButton(text="🖼 Кэш и медиа", callback_data="ad:open:cache"),
        ],
        [
            InlineKeyboardButton(text="💾 Данные", callback_data="ad:open:data"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="ad:open:users"),
        ],
        [InlineKeyboardButton(text="⚪️ Белый список (Whitelist)", callback_data="ad:open:whitelist")],
        [InlineKeyboardButton(text="📊 Популярность функций", callback_data="ad:feature_stats")],
        [
            InlineKeyboardButton(text="📤 Сделать бэкап сейчас", callback_data="ad:backupnow"),
            InlineKeyboardButton(text="📥 Загрузить бэкап", callback_data="ad:restore"),
        ],
        [InlineKeyboardButton(text="📢 Рассылка всем", callback_data="ad:broadcast")],
        [InlineKeyboardButton(text="✖️ Закрыть", callback_data="ad:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_users_keyboard(owners: list[dict], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    """Keyboard for admin users list with pagination, user details, and ban/unban buttons."""
    total_owners = len(owners)
    total_pages = max(1, (total_owners + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    page_owners = owners[start_idx : start_idx + per_page]

    rows: list[list[InlineKeyboardButton]] = []
    for o in page_owners:
        uid = o["owner_id"]
        is_banned = o.get("is_banned", False)
        is_admin = o.get("is_admin", False)
        conns = o.get("connections", 0)
        msgs_48h = o.get("msgs_48h", 0) or 0
        media_48h = o.get("media_48h", 0) or 0
        media_mb = o.get("media_mb", 0.0) or 0.0

        status_icon = "🔴" if is_banned else ("⭐" if is_admin else ("🟢" if conns else "⚪"))
        name = o.get("full_name") or f"Пользователь"
        uname = f" (@{o['username']})" if o.get("username") else ""

        # Сначала ник, юз и уже потом id
        title_btn = InlineKeyboardButton(
            text=f"{status_icon} {name}{uname} [ID: {uid}]",
            callback_data=f"ad:user:view:{uid}",
        )
        stats_text = f"📊 48ч: {msgs_48h}💬 {media_48h}🖼 ({media_mb:.1f}MB)"
        action_btn = (
            InlineKeyboardButton(text="✅ Разбанить", callback_data=f"ad:user:unban:{uid}:{page}")
            if is_banned
            else InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ad:user:ban:{uid}:{page}")
        )
        rows.append([title_btn])
        rows.append([
            InlineKeyboardButton(text=stats_text, callback_data=f"ad:user:view:{uid}"),
            action_btn,
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"ad:users:page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="ad:noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"ad:users:page:{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="🔍 Найти пользователя по ID", callback_data="ad:user:find")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_detail_keyboard(uid: int, is_banned: bool, is_admin: bool) -> InlineKeyboardMarkup:
    rows = []
    if not is_admin:
        if is_banned:
            rows.append([InlineKeyboardButton(text="✅ Разбанить", callback_data=f"ad:user:unban:{uid}:1")])
        else:
            rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ad:user:ban:{uid}:1")])
    rows.append([InlineKeyboardButton(text="⬅️ К списку пользователей", callback_data="ad:open:users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def online_menu_keyboard(is_active: bool, remaining_sec: int | None, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if is_active:
        rows.append([InlineKeyboardButton(text="⏹ Выключить онлайн-режим", callback_data="us:online:stop")])
    else:
        rows.append([
            InlineKeyboardButton(text="⏱ 15 мин", callback_data="us:online:start:900"),
            InlineKeyboardButton(text="⏱ 30 мин", callback_data="us:online:start:1800"),
            InlineKeyboardButton(text="⏱ 1 час", callback_data="us:online:start:3600"),
        ])
        rows.append([
            InlineKeyboardButton(text="✏️ Указать в минутах", callback_data="us:online:custom"),
        ])
        if is_admin:
            rows.append([
                InlineKeyboardButton(text="⏱ 3 часа ⭐", callback_data="us:online:start:10800"),
                InlineKeyboardButton(text="⏱ 12 часов ⭐", callback_data="us:online:start:43200"),
                InlineKeyboardButton(text="♾ Бессрочно ⭐", callback_data="us:online:start:0"),
            ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_actions_menu_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for c in chats[:10]:
        cid = c["chat_id"]
        title = c["title"]
        rows.append([InlineKeyboardButton(text=f"💬 {title}", callback_data=f"act:chat:{cid}")])
    rows.append([
        InlineKeyboardButton(text="🗑 Очистка по слову / регулярке", callback_data="us:open:delword"),
    ])
    rows.append([InlineKeyboardButton(text="➕ Ввести Chat ID вручную", callback_data="act:manual")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_action_picker_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔇 Mute навсегда", callback_data=f"act:do:mute_perm:{chat_id}"),
            InlineKeyboardButton(text="🔊 Снять Mute", callback_data=f"act:do:unmute:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="⏱ Mute 1 час", callback_data=f"act:do:mute_1h:{chat_id}"),
            InlineKeyboardButton(text="⌨️ Typing (10с)", callback_data=f"act:do:typing:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="👤 Клонировать", callback_data=f"act:do:clone:{chat_id}"),
            InlineKeyboardButton(text="🗑 Удалить N сообщ.", callback_data=f"act:do:del:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="💣 Спам в чат", callback_data=f"act:do:spam:{chat_id}"),
            InlineKeyboardButton(text="🔍 Удалить по слову", callback_data=f"act:do:delword:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="🧩 Удалить по регулярке", callback_data=f"act:do:delregex:{chat_id}"),
        ],
        [InlineKeyboardButton(text="⬅️ К выбору чата", callback_data="us:open:actions")],
    ])


def delword_scope_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 Только в одном чате", callback_data="delword:scope:single"),
            ],
            [
                InlineKeyboardButton(text="🌐 Во ВСЕХ чатах сразу", callback_data="delword:scope:all"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="us:back")],
        ]
    )


def delword_pick_chat_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for c in chats[:10]:
        cid = c["chat_id"]
        title = c["title"]
        rows.append([InlineKeyboardButton(text=f"💬 {title}", callback_data=f"delword:pick:{cid}")])
    rows.append([InlineKeyboardButton(text="➕ Ввести Chat ID вручную", callback_data="delword:pick:manual")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="us:open:delword")])
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

    if section == "cache":
        rows.append([InlineKeyboardButton(text="🧹 Очистить старые записи БД", callback_data="ad:clean:db")])
        rows.append([InlineKeyboardButton(text="🗑 Очистить старые медиафайлы", callback_data="ad:clean:media")])
        rows.append([InlineKeyboardButton(text="📥 Сбросить RAM-кэш", callback_data="ad:clean:cache")])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")]])


def admin_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="ad:cancel")]])


def admin_whitelist_keyboard(whitelist_rows: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for r in whitelist_rows:
        uid = r["user_id"]
        note = f" ({r['note']})" if r["note"] else ""
        label = f"⚪️ {uid}{note}"
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data="ad:noop"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"ad:wl:del:{uid}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить ID", callback_data="ad:wl:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ad:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



# -------------------------------------------------------------------- /help
HELP_TOPICS: list[tuple[str, str]] = [
    ("cmd_spam", "💬 .spam"),
    ("cmd_troll", "🤪 .troll"),
    ("cmd_del", "🗑 .del"),
    ("cmd_clone", "🎭 .clone"),
    ("cmd_tonote", "⭕️ .tonote"),
    ("cmd_tovoice", "🎤 .tovoice"),
    ("cmd_chatstat", "📊 .chatstat"),
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
    ("feat_search", "🕵️ Антипоиск"),
    ("feat_notify", "🔔 Уведомления"),
]


def help_topics_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, callback_data=f"help:topic:{key}")] for key, title in HELP_TOPICS]
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="help:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К списку тем", callback_data="help:back")]])
