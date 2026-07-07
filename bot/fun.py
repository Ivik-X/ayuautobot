from __future__ import annotations

import random
import re


def mock_text(text: str) -> str:
    result: list[str] = []
    upper = True
    for char in text:
        if char.isalpha():
            result.append(char.upper() if upper else char.lower())
            upper = not upper
        else:
            result.append(char)
    return "".join(result)


def clown_text(text: str) -> str:
    return "🤡".join(text)


def owo_text(text: str) -> str:
    text = re.sub(r"[rl]", "w", text, flags=re.IGNORECASE)
    text = re.sub(r"[RL]", "W", text)
    text = re.sub(r"n([aeiouAEIOU])", r"ny\1", text)
    text = re.sub(r"N([aeiouAEIOU])", r"Ny\1", text)
    return f"{text} owo"


def reverse_text(text: str) -> str:
    return text[::-1]


def zalgo_text(text: str, intensity: int = 3) -> str:
    marks = ["\u0300", "\u0301", "\u0302", "\u0303", "\u0304", "\u0306", "\u0307", "\u0308"]
    result: list[str] = []
    for char in text:
        result.append(char)
        if char.strip():
            result.extend(random.choice(marks) for _ in range(random.randint(1, intensity)))
    return "".join(result)


_EIGHTBALL_ANSWERS = [
    "Да 👍", "Нет 👎", "Скорее да", "Скорее нет", "Определённо!", "Сомнительно...",
    "Спроси позже", "100% да", "Даже не думай", "Возможно 🤔", "Звёзды говорят — да",
    "Мой ответ — нет",
]


def eightball() -> str:
    return random.choice(_EIGHTBALL_ANSWERS)
