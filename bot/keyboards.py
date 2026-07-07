from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.settings import SETTINGS_FIELDS, OwnerSettings

CB_TOGGLE = "set:toggle:"
CB_EDIT = "set:edit:"
CB_CLOSE = "set:close"


def settings_keyboard(settings: OwnerSettings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for field_ in SETTINGS_FIELDS:
        value = getattr(settings, field_.key)
        if field_.kind == "bool":
            state = "✅" if value else "⬜️"
            text = f"{state} {field_.label}"
            rows.append(
                [InlineKeyboardButton(text=text, callback_data=f"{CB_TOGGLE}{field_.key}")]
            )
        else:
            text = f"{field_.label}: {value}"
            rows.append(
                [InlineKeyboardButton(text=text, callback_data=f"{CB_EDIT}{field_.key}")]
            )
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
