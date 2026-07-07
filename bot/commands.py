from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpamCommand:
    count: int
    text: str


@dataclass(frozen=True, slots=True)
class MuteCommand:
    seconds: int | None  # None => использовать значение по умолчанию из настроек


@dataclass(frozen=True, slots=True)
class UnmuteCommand:
    pass


@dataclass(frozen=True, slots=True)
class TypingCommand:
    seconds: int


@dataclass(frozen=True, slots=True)
class EmojiSpamCommand:
    emoji: str
    count: int


@dataclass(frozen=True, slots=True)
class TextTransformCommand:
    mode: str
    text: str


@dataclass(frozen=True, slots=True)
class RandCommand:
    low: int
    high: int


@dataclass(frozen=True, slots=True)
class PurgeCommand:
    minutes: int | None = None


@dataclass(frozen=True, slots=True)
class SimpleCommand:
    name: str


@dataclass(frozen=True, slots=True)
class EightBallCommand:
    question: str


@dataclass(frozen=True, slots=True)
class CalcCommand:
    expression: str


@dataclass(frozen=True, slots=True)
class TranslateCommand:
    lang: str
    text: str


@dataclass(frozen=True, slots=True)
class WeatherCommand:
    city: str


@dataclass(frozen=True, slots=True)
class CurrencyCommand:
    amount: float
    frm: str
    to: str


@dataclass(frozen=True, slots=True)
class QrCommand:
    text: str


@dataclass(frozen=True, slots=True)
class ShortCommand:
    url: str


@dataclass(frozen=True, slots=True)
class NoteAddCommand:
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class NoteDelCommand:
    name: str


@dataclass(frozen=True, slots=True)
class NoteListCommand:
    pass


@dataclass(frozen=True, slots=True)
class SayCommand:
    name: str


@dataclass(frozen=True, slots=True)
class RemindCommand:
    minutes: float
    text: str


@dataclass(frozen=True, slots=True)
class AfkCommand:
    enable: bool
    text: str | None = None


@dataclass(frozen=True, slots=True)
class SetCommand:
    key: str
    value: str


Command = (
    SpamCommand
    | MuteCommand
    | UnmuteCommand
    | TypingCommand
    | EmojiSpamCommand
    | TextTransformCommand
    | RandCommand
    | PurgeCommand
    | SimpleCommand
    | EightBallCommand
    | CalcCommand
    | TranslateCommand
    | WeatherCommand
    | CurrencyCommand
    | QrCommand
    | ShortCommand
    | NoteAddCommand
    | NoteDelCommand
    | NoteListCommand
    | SayCommand
    | RemindCommand
    | AfkCommand
    | SetCommand
)

_SPAM_RE = re.compile(r"^\.spam\s+(\d+)\s+(.+)$", re.DOTALL | re.IGNORECASE)
_MUTE_RE = re.compile(r"^\.mute(?:\s+(\d+))?$", re.IGNORECASE)
_UNMUTE_RE = re.compile(r"^\.unmute$", re.IGNORECASE)
_TYPING_RE = re.compile(r"^\.typing\s+(\d+)$", re.IGNORECASE)
_BOMB_RE = re.compile(r"^\.bomb\s+(\d+)$", re.IGNORECASE)
_LOVE_RE = re.compile(r"^\.(?:love|heart)\s+(\d+)$", re.IGNORECASE)
_MOCK_RE = re.compile(r"^\.mock\s+(.+)$", re.DOTALL | re.IGNORECASE)
_CLOWN_RE = re.compile(r"^\.clown\s+(.+)$", re.DOTALL | re.IGNORECASE)
_OWO_RE = re.compile(r"^\.owo\s+(.+)$", re.DOTALL | re.IGNORECASE)
_REVERSE_RE = re.compile(r"^\.reverse\s+(.+)$", re.DOTALL | re.IGNORECASE)
_ZALGO_RE = re.compile(r"^\.zalgo\s+(.+)$", re.DOTALL | re.IGNORECASE)
_RAND_RE = re.compile(r"^\.rand(?:\s+(\d+)\s+(\d+))?$", re.IGNORECASE)
_PURGE_RE = re.compile(r"^\.purge(?:cache)?(?:\s+(\d+))?$", re.IGNORECASE)
_EIGHTBALL_RE = re.compile(r"^\.8ball(?:\s+(.+))?$", re.DOTALL | re.IGNORECASE)
_CALC_RE = re.compile(r"^\.calc\s+(.+)$", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"^\.tr\s+([a-zA-Z]{2})\s+(.+)$", re.DOTALL | re.IGNORECASE)
_TR_SHORT_RE = re.compile(r"^\.tr\s+(.+)$", re.DOTALL | re.IGNORECASE)
_WEATHER_RE = re.compile(r"^\.weather\s+(.+)$", re.DOTALL | re.IGNORECASE)
_CURRENCY_RE = re.compile(
    r"^\.currency\s+([\d.,]+)\s+([a-zA-Z]{3})\s+([a-zA-Z]{3})$", re.IGNORECASE
)
_QR_RE = re.compile(r"^\.qr\s+(.+)$", re.DOTALL | re.IGNORECASE)
_SHORT_RE = re.compile(r"^\.short\s+(\S+)$", re.IGNORECASE)
_NOTE_ADD_RE = re.compile(r"^\.note\s+add\s+(\S+)\s+(.+)$", re.DOTALL | re.IGNORECASE)
_NOTE_DEL_RE = re.compile(r"^\.note\s+del\s+(\S+)$", re.IGNORECASE)
_NOTE_LIST_RE = re.compile(r"^\.note\s+list$", re.IGNORECASE)
_SAY_RE = re.compile(r"^\.say\s+(\S+)$", re.IGNORECASE)
_REMIND_RE = re.compile(r"^\.remind\s+([\d.]+)\s+(.+)$", re.DOTALL | re.IGNORECASE)
_AFK_ON_RE = re.compile(r"^\.afk\s+on(?:\s+(.+))?$", re.DOTALL | re.IGNORECASE)
_AFK_OFF_RE = re.compile(r"^\.afk\s+off$", re.IGNORECASE)
_SET_RE = re.compile(r"^\.set\s+(\S+)\s+(.+)$", re.DOTALL | re.IGNORECASE)
_SIMPLE = {
    "help": "help",
    "dice": "dice",
    "flip": "flip",
    "stats": "stats",
    "chats": "chats",
    "ping": "ping",
    "info": "info",
    "id": "id",
    "settings": "settings",
}


def parse_command(text: str | None) -> Command | None:
    if not text:
        return None

    text = text.strip()
    if not text.startswith("."):
        return None

    lowered = text.lower()
    for key, name in _SIMPLE.items():
        if lowered == f".{key}":
            return SimpleCommand(name=name)

    if _UNMUTE_RE.match(text):
        return UnmuteCommand()

    if match := _PURGE_RE.match(text):
        if match.group(1):
            minutes = int(match.group(1))
            if minutes <= 0:
                return None
            return PurgeCommand(minutes=minutes)
        return PurgeCommand()

    if match := _MUTE_RE.match(text):
        if match.group(1):
            seconds = int(match.group(1))
            if seconds <= 0:
                return None
            return MuteCommand(seconds=seconds)
        return MuteCommand(seconds=None)

    if match := _TYPING_RE.match(text):
        seconds = min(int(match.group(1)), 30)
        if seconds <= 0:
            return None
        return TypingCommand(seconds=seconds)

    if match := _BOMB_RE.match(text):
        count = min(int(match.group(1)), 50)
        if count <= 0:
            return None
        return EmojiSpamCommand(emoji="💣", count=count)

    if match := _LOVE_RE.match(text):
        count = min(int(match.group(1)), 50)
        if count <= 0:
            return None
        return EmojiSpamCommand(emoji="❤️", count=count)

    if match := _RAND_RE.match(text):
        if match.group(1):
            low, high = int(match.group(1)), int(match.group(2))
            if low > high:
                low, high = high, low
            return RandCommand(low=low, high=high)
        return RandCommand(low=1, high=100)

    if match := _EIGHTBALL_RE.match(text):
        return EightBallCommand(question=(match.group(1) or "").strip())

    if match := _MOCK_RE.match(text):
        return TextTransformCommand(mode="mock", text=match.group(1).strip())

    if match := _CLOWN_RE.match(text):
        return TextTransformCommand(mode="clown", text=match.group(1).strip())

    if match := _OWO_RE.match(text):
        return TextTransformCommand(mode="owo", text=match.group(1).strip())

    if match := _REVERSE_RE.match(text):
        return TextTransformCommand(mode="reverse", text=match.group(1).strip())

    if match := _ZALGO_RE.match(text):
        return TextTransformCommand(mode="zalgo", text=match.group(1).strip())

    if match := _CALC_RE.match(text):
        return CalcCommand(expression=match.group(1).strip())

    if match := _TR_RE.match(text):
        return TranslateCommand(lang=match.group(1).lower(), text=match.group(2).strip())

    if match := _TR_SHORT_RE.match(text):
        return TranslateCommand(lang="ru", text=match.group(1).strip())

    if match := _WEATHER_RE.match(text):
        return WeatherCommand(city=match.group(1).strip())

    if match := _CURRENCY_RE.match(text):
        amount = float(match.group(1).replace(",", "."))
        return CurrencyCommand(amount=amount, frm=match.group(2).upper(), to=match.group(3).upper())

    if match := _QR_RE.match(text):
        return QrCommand(text=match.group(1).strip())

    if match := _SHORT_RE.match(text):
        return ShortCommand(url=match.group(1).strip())

    if match := _NOTE_ADD_RE.match(text):
        return NoteAddCommand(name=match.group(1).strip(), text=match.group(2).strip())

    if match := _NOTE_DEL_RE.match(text):
        return NoteDelCommand(name=match.group(1).strip())

    if _NOTE_LIST_RE.match(text):
        return NoteListCommand()

    if match := _SAY_RE.match(text):
        return SayCommand(name=match.group(1).strip())

    if match := _REMIND_RE.match(text):
        minutes = float(match.group(1))
        if minutes <= 0:
            return None
        return RemindCommand(minutes=minutes, text=match.group(2).strip())

    if match := _AFK_ON_RE.match(text):
        return AfkCommand(enable=True, text=(match.group(1) or "").strip() or None)

    if _AFK_OFF_RE.match(text):
        return AfkCommand(enable=False)

    if match := _SET_RE.match(text):
        return SetCommand(key=match.group(1).strip().lower(), value=match.group(2).strip())

    if match := _SPAM_RE.match(text):
        count = int(match.group(1))
        payload = match.group(2).strip()
        if count <= 0 or not payload:
            return None
        return SpamCommand(count=min(count, 100), text=payload)

    return None
