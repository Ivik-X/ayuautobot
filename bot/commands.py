from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpamCommand:
    count: int
    text: str | None  # None => спамим содержимым реплая (медиа/стикер/текст)


@dataclass(frozen=True, slots=True)
class MuteCommand:
    seconds: int | None  # None => значение по умолчанию из настроек


@dataclass(frozen=True, slots=True)
class UnmuteCommand:
    pass


@dataclass(frozen=True, slots=True)
class TypingCommand:
    seconds: int


@dataclass(frozen=True, slots=True)
class TextTransformCommand:
    mode: str  # mock | reverse
    text: str


@dataclass(frozen=True, slots=True)
class TranslateCommand:
    lang: str
    text: str


@dataclass(frozen=True, slots=True)
class QrCommand:
    text: str


@dataclass(frozen=True, slots=True)
class ShortCommand:
    url: str


@dataclass(frozen=True, slots=True)
class SayCommand:
    name: str


@dataclass(frozen=True, slots=True)
class ViewCommand:
    seconds: int
    caption: str


@dataclass(frozen=True, slots=True)
class SimpleCommand:
    name: str  # id | ping


Command = (
    SpamCommand
    | MuteCommand
    | UnmuteCommand
    | TypingCommand
    | TextTransformCommand
    | TranslateCommand
    | QrCommand
    | ShortCommand
    | SayCommand
    | ViewCommand
    | SimpleCommand
)

_SPAM_RE = re.compile(r"^\.spam\s+(\d+)(?:\s+(.+))?$", re.DOTALL | re.IGNORECASE)
_MUTE_RE = re.compile(r"^\.mute(?:\s+(\d+))?$", re.IGNORECASE)
_UNMUTE_RE = re.compile(r"^\.unmute$", re.IGNORECASE)
_TYPING_RE = re.compile(r"^\.typing\s+(\d+)$", re.IGNORECASE)
_MOCK_RE = re.compile(r"^\.mock\s+(.+)$", re.DOTALL | re.IGNORECASE)
_REVERSE_RE = re.compile(r"^\.reverse\s+(.+)$", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"^\.tr\s+([a-zA-Z]{2})\s+(.+)$", re.DOTALL | re.IGNORECASE)
_TR_SHORT_RE = re.compile(r"^\.tr\s+(.+)$", re.DOTALL | re.IGNORECASE)
_QR_RE = re.compile(r"^\.qr\s+(.+)$", re.DOTALL | re.IGNORECASE)
_SHORT_RE = re.compile(r"^\.short\s+(\S+)$", re.IGNORECASE)
_SAY_RE = re.compile(r"^\.say\s+(\S+)$", re.IGNORECASE)
_VIEW_RE = re.compile(r"^\.view\s+(\d+)(?:\s+(.*))?$", re.DOTALL | re.IGNORECASE)

_SIMPLE = {"id": "id", "ping": "ping"}


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

    if match := _MOCK_RE.match(text):
        return TextTransformCommand(mode="mock", text=match.group(1).strip())

    if match := _REVERSE_RE.match(text):
        return TextTransformCommand(mode="reverse", text=match.group(1).strip())

    if match := _TR_RE.match(text):
        return TranslateCommand(lang=match.group(1).lower(), text=match.group(2).strip())

    if match := _TR_SHORT_RE.match(text):
        return TranslateCommand(lang="ru", text=match.group(1).strip())

    if match := _QR_RE.match(text):
        return QrCommand(text=match.group(1).strip())

    if match := _SHORT_RE.match(text):
        return ShortCommand(url=match.group(1).strip())

    if match := _SAY_RE.match(text):
        return SayCommand(name=match.group(1).strip())

    if match := _VIEW_RE.match(text):
        seconds = int(match.group(1))
        if seconds <= 0:
            return None
        return ViewCommand(seconds=min(seconds, 3600), caption=(match.group(2) or "").strip())

    if match := _SPAM_RE.match(text):
        count = int(match.group(1))
        payload = match.group(2).strip() if match.group(2) else None
        if count <= 0:
            return None
        return SpamCommand(count=min(count, 100), text=payload)

    return None
