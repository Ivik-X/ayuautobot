from __future__ import annotations

import time

from aiogram.types import BusinessConnection, Message

from bot.database import Database
from bot.media import media_flags, unlink_media
from bot.models import CachedMessage, MuteSession
from bot.stats import ChatStats


class Storage:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db
        self._connections: dict[str, BusinessConnection] = {}
        self._cache: dict[tuple[str, int, int], CachedMessage] = {}
        self._stats: dict[tuple[str, int], ChatStats] = {}
        self._mute: dict[tuple[str, int], MuteSession] = {}
        self._bot_deleted: set[tuple[str, int, int]] = set()

    @property
    def db(self) -> Database | None:
        return self._db

    def set_connection(self, connection: BusinessConnection) -> None:
        self._connections[connection.id] = connection

    def get_connection(self, connection_id: str) -> BusinessConnection | None:
        return self._connections.get(connection_id)

    def owner_chat_id(self, connection_id: str) -> int | None:
        connection = self._connections.get(connection_id)
        return connection.user_chat_id if connection else None

    def owner_user_id(self, connection_id: str) -> int | None:
        connection = self._connections.get(connection_id)
        return connection.user.id if connection else None

    def is_bot_message(self, message: Message) -> bool:
        return message.sender_business_bot is not None

    def is_owner_message(self, connection_id: str, message: Message) -> bool:
        if self.is_bot_message(message) or message.from_user is None:
            return False
        if message.chat.type == "private":
            return message.from_user.id != message.chat.id
        owner_id = self.owner_user_id(connection_id)
        return owner_id is not None and message.from_user.id == owner_id

    def is_partner_message(self, connection_id: str, message: Message) -> bool:
        if self.is_bot_message(message):
            return False
        if message.from_user is None:
            return True
        if message.chat.type == "private":
            return message.from_user.id == message.chat.id
        owner_id = self.owner_user_id(connection_id)
        if owner_id is None:
            return True
        return message.from_user.id != owner_id

    def cache_message(
        self,
        connection_id: str,
        message: Message,
        *,
        media: MediaRef | None = None,
    ) -> CachedMessage:
        kind = media.kind if media else _message_kind(message)
        title = message.chat.full_name or message.chat.username or str(message.chat.id)
        cached = CachedMessage(
            connection_id=connection_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            from_user_id=message.from_user.id if message.from_user else None,
            from_user_name=_user_label(message),
            content=describe_message(message),
            cached_at=time.time(),
            media=media,
            flags=media_flags(message) or None,
            kind=kind,
        )

        key = (connection_id, message.chat.id, message.message_id)
        old = self._cache.get(key)
        if old is not None:
            unlink_media(old.media)
        elif not self._db:
            self._touch_stats(connection_id, message.chat.id, title, kind)

        self._cache[key] = cached

        if self._db is not None:
            self._db.upsert_message(cached, title)

        return cached

    def find_cached(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> CachedMessage | None:
        for key in _lookup_keys(connection_id, chat_id, message_id):
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        if self._db is not None:
            cached = self._db.get_message(connection_id, chat_id, message_id)
            if cached is not None:
                self._cache[(connection_id, cached.chat_id, message_id)] = cached
                return cached

        return None

    def remove_cached(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> CachedMessage | None:
        cached = self.find_cached(connection_id, chat_id, message_id)

        for key in _lookup_keys(connection_id, chat_id, message_id):
            item = self._cache.pop(key, None)
            if item is not None:
                unlink_media(item.media)

        if self._db is not None:
            db_cached = self._db.mark_deleted(connection_id, chat_id, message_id)
            return cached or db_cached

        return cached

    def purge_expired(self, ttl_seconds: float) -> int:
        if ttl_seconds <= 0:
            return 0
        cutoff = time.time() - ttl_seconds
        stale = [key for key, item in self._cache.items() if item.cached_at < cutoff]
        for key in stale:
            self._drop_key(key)
        return len(stale)

    def purge_all(self) -> int:
        count = len(self._cache)
        for key in list(self._cache):
            self._drop_key(key)
        self._stats.clear()
        return count

    def purge_older_than(self, minutes: int) -> int:
        return self.purge_expired(minutes * 60)

    def enforce_max_entries(self, max_entries: int) -> int:
        if max_entries <= 0 or len(self._cache) <= max_entries:
            return 0

        overflow = len(self._cache) - max_entries
        oldest = sorted(self._cache.items(), key=lambda item: item[1].cached_at)
        removed = 0
        for key, _ in oldest[:overflow]:
            self._drop_key(key)
            removed += 1
        return removed

    def cached_count(self, connection_id: str | None = None) -> int:
        if connection_id is None:
            return len(self._cache)
        return sum(1 for key in self._cache if key[0] == connection_id)

    def media_files_count(self) -> int:
        return sum(1 for item in self._cache.values() if item.media and item.media.local_path)

    def chat_stats(self, connection_id: str) -> list[tuple[int, ChatStats]]:
        if self._db is not None:
            return self._db.chat_stats(connection_id)

        rows = [
            (chat_id, stats)
            for (cid, chat_id), stats in self._stats.items()
            if cid == connection_id
        ]
        rows.sort(key=lambda row: row[1].total, reverse=True)
        return rows

    def db_count(self, connection_id: str | None = None) -> int | None:
        if self._db is None:
            return None
        return self._db.count_messages(connection_id)

    def db_total_count(self) -> int | None:
        if self._db is None:
            return None
        return self._db.count_all_messages()

    def mark_bot_deleted(self, connection_id: str, chat_id: int, message_id: int) -> None:
        self._bot_deleted.add((connection_id, chat_id, message_id))

    def was_bot_deleted(self, connection_id: str, chat_id: int, message_id: int) -> bool:
        if (connection_id, chat_id, message_id) in self._bot_deleted:
            self._bot_deleted.discard((connection_id, chat_id, message_id))
            return True
        return False

    def start_mute(
        self,
        connection_id: str,
        chat_id: int,
        *,
        seconds: int | None = None,
        count: int | None = None,
    ) -> None:
        expires_at = time.time() + seconds if seconds is not None else None
        self._mute[(connection_id, chat_id)] = MuteSession(
            expires_at=expires_at,
            remaining=count,
        )

    def stop_mute(self, connection_id: str, chat_id: int) -> None:
        self._mute.pop((connection_id, chat_id), None)

    def mute_active(self, connection_id: str, chat_id: int) -> bool:
        session = self._mute.get((connection_id, chat_id))
        if session is None:
            return False
        if session.expires_at is not None and time.time() >= session.expires_at:
            self.stop_mute(connection_id, chat_id)
            return False
        if session.remaining is not None and session.remaining <= 0:
            self.stop_mute(connection_id, chat_id)
            return False
        return True

    def register_mute_deletion(self, connection_id: str, chat_id: int) -> None:
        session = self._mute.get((connection_id, chat_id))
        if session is None or session.remaining is None:
            return
        session.remaining -= 1
        if session.remaining <= 0:
            self.stop_mute(connection_id, chat_id)

    def _touch_stats(self, connection_id: str, chat_id: int, title: str, kind: str) -> None:
        key = (connection_id, chat_id)
        stats = self._stats.get(key)
        if stats is None:
            stats = ChatStats(title=title)
            self._stats[key] = stats
        elif title and stats.title in ("", str(chat_id)):
            stats.title = title
        stats.add(kind)

    def _drop_key(self, key: tuple[str, int, int]) -> None:
        cached = self._cache.pop(key, None)
        if cached is not None:
            unlink_media(cached.media)


def _lookup_keys(connection_id: str, chat_id: int, message_id: int) -> list[tuple[str, int, int]]:
    keys = [(connection_id, chat_id, message_id)]
    alt = -chat_id if chat_id > 0 else abs(chat_id)
    if alt != chat_id:
        keys.append((connection_id, alt, message_id))
    return keys


def _message_kind(message: Message) -> str:
    if message.text or message.caption:
        return "text"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "animation"
    return "other"


def _user_label(message: Message) -> str:
    user = message.from_user
    if user is None:
        return message.chat.full_name or message.chat.username or "неизвестный"
    if message.chat.type == "private" and user.id != message.chat.id:
        return message.chat.full_name or message.chat.username or str(message.chat.id)
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def describe_message(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption

    parts: list[str] = []
    if message.photo:
        parts.append("📷 фото")
    if message.video:
        parts.append("🎬 видео")
    if message.voice:
        parts.append("🎤 голосовое")
    if message.video_note:
        parts.append("⭕ кружок")
    if message.audio:
        parts.append("🎵 аудио")
    if message.document:
        name = message.document.file_name or "файл"
        parts.append(f"📎 {name}")
    if message.sticker:
        emoji = message.sticker.emoji or "стикер"
        parts.append(f"🙂 {emoji}")
    if message.animation:
        parts.append("GIF")
    if message.location:
        parts.append("📍 геолокация")
    if message.contact:
        parts.append("👤 контакт")
    if message.poll:
        parts.append(f"📊 опрос: {message.poll.question}")
    if not parts:
        return "[сообщение без текста]"
    return " ".join(parts)
