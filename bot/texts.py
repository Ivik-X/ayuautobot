from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_START = (
    "Бот для Telegram Business («Автоматизация чатов»).\n\n"
    "Подключите меня: Настройки → Telegram для бизнеса → Чат-боты.\n"
    "Выдайте права: отвечать, читать и удалять сообщения.\n\n"
    "{help}"
)

DEFAULT_HELP = """<b>Команды</b> (в чате с собеседником, от вашего имени):

<b>Основные</b>
• <code>.spam 5 текст</code> — отправить 5 сообщений
• <code>.mute 60</code> — удалять входящие 60 сек
• <code>.unmute</code> — выключить mute

<b>Развлечения</b>
• <code>.typing 5</code> — статус «печатает…»
• <code>.dice</code> — кинуть кубик
• <code>.flip</code> — монетка
• <code>.bomb 5</code> / <code>.love 5</code> — спам эмодзи
• <code>.mock текст</code> — sPoNgEbOb
• <code>.clown текст</code> — к🤡л🤡о🤡у🤡н
• <code>.owo текст</code> — owofication
• <code>.reverse текст</code> — задом наперёд
• <code>.zalgo текст</code> — залго-текст
• <code>.rand</code> / <code>.rand 1 50</code> — случайное число

<b>Кэш и данные</b>
• <code>.stats</code> — кэш и память
• <code>.chats</code> — топ чатов по активности
• <code>.purge</code> — очистить оперативный кэш
• <code>.purge 30</code> — удалить из кэша старше 30 мин
• <code>.ping</code> — проверка связи

Удаления, правки и медиа собеседника приходят в этот чат."""

DEFAULT_PONG = "🏓 pong — бот на связи"


@dataclass(frozen=True, slots=True)
class Texts:
    start: str
    help: str
    pong: str


def _read_text(env_key: str, file_key: str, default: str) -> str:
    file_path = os.getenv(file_key, "").strip()
    if file_path:
        path = Path(file_path)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raw = os.getenv(env_key, "").strip()
    if raw:
        return raw.replace("\\n", "\n")
    return default


def load_texts() -> Texts:
    help_text = _read_text("TEXT_HELP", "TEXT_HELP_FILE", DEFAULT_HELP)
    start_default = DEFAULT_START.replace("{help}", help_text)
    start_text = _read_text("TEXT_START", "TEXT_START_FILE", start_default)
    if "{help}" in start_text:
        start_text = start_text.replace("{help}", help_text)
    pong_text = _read_text("TEXT_PONG", "TEXT_PONG_FILE", DEFAULT_PONG)
    return Texts(start=start_text, help=help_text, pong=pong_text)
