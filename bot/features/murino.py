"""bot/features/murino.py — Муринский язык (Python-порт плагина GhostGram).

Преобразует текст на «муринский» мемный сленг:
  - Точечный словарь (я→ч, дед→дод, кот→котость …)
  - Генеративный суффикс -ость с настраиваемой вероятностью
  - Без Бурмалды (убрана по запросу)
"""
from __future__ import annotations

import random
import re

DICTIONARY: dict[str, list[str]] = {
    "я": ["ч"],
    "ты": ["ьы"],
    "он": ["рн"],
    "она": ["рна"],
    "оно": ["рно"],
    "сын": ["сыр"],
    "батя": ["батч"],
    "мама": ["мома", "мамч"],
    "дед": ["дод"],
    "брат": ["брата"],
    "жена": ["жинка"],
    "друг": ["друн"],
    "подруга": ["подруня"],
    "мальчик": ["мач"],
    "мужик": ["муж"],
    "девушка": ["дев"],
    "поезд": ["поез"],
    "туман": ["фог", "туманность"],
    "кот": ["котость"],
    "школа": ["школьность"],
    "мозг": ["мозгость"],
    "помощь": ["помощность"],
    "мем": ["мемность"],
    "работа": ["работасть"],
    "день": ["деньность"],
    "человек": ["человекость"],
}

PRONOUN_KEYS = frozenset(["я", "ты", "он", "она", "оно"])

STOP_WORDS = frozenset([
    "и", "а", "но", "или", "в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "обо",
    "от", "до", "из", "изо", "за", "по", "же", "ли", "бы", "не", "ни", "что", "как",
    "это", "то", "та", "те", "тот", "эти", "эта", "этот", "для", "при", "под", "над",
    "без", "через", "между", "чтобы", "если", "когда", "где", "куда", "откуда", "уже",
    "ещё", "еще", "вот", "да", "нет", "так", "все", "всё", "тут", "там", "быть", "был",
    "была", "было", "были", "есть", "мы", "вы", "нас", "вас", "им", "их", "его", "её",
    "ему", "ей", "меня", "мне", "мной", "тебя", "тебе", "тобой",
])

# Вероятность применения суффикса — низкая, «сильно пореже»
SUFFIX_PROB = 0.18

_WORD_RE = re.compile(r"([A-Za-zА-Яа-яЁё]+)")
_VOWELS_END = set("аяыиьоеёую")


def _apply_suffix(word: str) -> str:
    if not word:
        return word
    last = word[-1].lower()
    if last in _VOWELS_END or last == "ь":
        return word[:-1] + "ость"
    return word + "ость"


def _match_case(sample: str, replacement: str) -> str:
    if sample and sample[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def transform(text: str) -> str:
    """Переводит текст на муринский язык."""
    if not text or not text.strip():
        return text
    if text.strip().startswith("."):
        return text
    if len(text) >= 3500:
        return text

    def replace_word(match: re.Match) -> str:
        raw = match.group(0)
        lower = raw.lower()
        if lower in DICTIONARY:
            return _match_case(raw, random.choice(DICTIONARY[lower]))
        if lower in STOP_WORDS:
            return raw
        if len(raw) >= 3 and random.random() < SUFFIX_PROB:
            return _match_case(raw, _apply_suffix(lower))
        return raw

    result = _WORD_RE.sub(replace_word, text)
    if len(result) > len(text) * 1.6 + 10:
        return text
    return result
