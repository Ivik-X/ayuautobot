from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpamCommand:
    count: int
    text: str


@dataclass(frozen=True, slots=True)
class MuteCommand:
    seconds: int


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
)

_SPAM_RE = re.compile(r"^\.spam\s+(\d+)\s+(.+)$", re.DOTALL | re.IGNORECASE)
_MUTE_RE = re.compile(r"^\.mute\s+(\d+)$", re.IGNORECASE)
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
_SIMPLE = {
    "help": "help",
    "dice": "dice",
    "flip": "flip",
    "stats": "stats",
    "chats": "chats",
    "ping": "ping",
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

    if match := _UNMUTE_RE.match(text):
        return UnmuteCommand()

    if match := _PURGE_RE.match(text):
        if match.group(1):
            minutes = int(match.group(1))
            if minutes <= 0:
                return None
            return PurgeCommand(minutes=minutes)
        return PurgeCommand()

    if match := _MUTE_RE.match(text):
        seconds = int(match.group(1))
        if seconds <= 0:
            return None
        return MuteCommand(seconds=seconds)

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

    if match := _SPAM_RE.match(text):
        count = int(match.group(1))
        payload = match.group(2).strip()
        if count <= 0 or not payload:
            return None
        return SpamCommand(count=min(count, 100), text=payload)

    return None
