from __future__ import annotations

from dataclasses import dataclass

from bot.media import MediaRef


@dataclass(slots=True)
class CachedMessage:
    connection_id: str
    chat_id: int
    message_id: int
    from_user_id: int | None
    from_user_name: str
    content: str
    cached_at: float
    media: MediaRef | None = None
    flags: list[str] | None = None
    kind: str = "text"


@dataclass
class MuteSession:
    expires_at: float | None
    remaining: int | None
