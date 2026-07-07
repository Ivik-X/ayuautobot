from __future__ import annotations

import time
from dataclasses import replace

from aiogram.types import BusinessConnection, Message

from bot.cache import LRUCache
from bot.config import Config
from bot.database import Database
from bot.media import MediaRef, media_flags, unlink_media
from bot.models import CachedMessage, MuteSession
from bot.settings import OwnerSettings
from bot.stats import ChatStats

AFK_REPLY_COOLDOWN_SECONDS = 300


class Storage:
    def __init__(self, db: Database, config: Config) -> None:
        self._db = db
        self._config = config
        self._connections: dict[str, BusinessConnection] = {}
        self._owner_of_connection: dict[str, int] = {}
        self._settings_cache: dict[int, OwnerSettings] = {}
        self._cache: LRUCache[tuple[str, int, int], CachedMessage] = LRUCache(config.cache.max_entries)
        self._mute: dict[tuple[str, int], MuteSession] = {}
        self._bot_deleted: set[tuple[str, int, int]] = set()
        self._afk_last_reply: dict[tuple[str, int], float] = {}

    @property
    def db(self) -> Database:
        return self._db

    # ------------------------------------------------------------ connections
    def set_connection(self, connection: BusinessConnection) -> None:
        self._connections[connection.id] = connection
        owner_id = connection.user.id
        self._owner_of_connection[connection.id] = owner_id
        self._db.ensure_owner(owner_id, is_admin=owner_id in self._config.admin_ids)
        self._db.upsert_connection(
            connection.id, owner_id, connection.user_chat_id, connection.is_enabled
        )

    def get_connection(self, connection_id: str) -> BusinessConnection | None:
        return self._connections.get(connection_id)

    def owner_chat_id(self, connection_id: str) -> int | None:
        connection = self._connections.get(connection_id)
        return connection.user_chat_id if connection else None

    def owner_user_id(self, connection_id: str) -> int | None:
        connection = self._connections.get(connection_id)
        if connection:
            return connection.user.id
        if connection_id in self._owner_of_connection:
            return self._owner_of_connection[connection_id]
        owner = self._db.owner_for_connection(connection_id)
        if owner is not None:
            self._owner_of_connection[connection_id] = owner
        return owner

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

    # -------------------------------------------------------------- settings
    def get_settings(self, owner_id: int) -> OwnerSettings:
        cached = self._settings_cache.get(owner_id)
        if cached is not None:
            return cached
        raw = self._db.get_owner_settings_raw(owner_id)
        settings = OwnerSettings.from_json(raw, self._config.default_settings)
        self._settings_cache[owner_id] = settings
        return settings

    def get_settings_for_connection(self, connection_id: str) -> OwnerSettings:
        owner_id = self.owner_user_id(connection_id)
        if owner_id is None:
            return self._config.default_settings
        return self.get_settings(owner_id)

    def save_settings(self, owner_id: int, settings: OwnerSettings) -> None:
        self._db.ensure_owner(owner_id, is_admin=owner_id in self._config.admin_ids)
        self._db.save_owner_settings(owner_id, settings.to_json())
        self._settings_cache[owner_id] = settings

    def update_setting(self, owner_id: int, key: str, value) -> OwnerSettings:
        current = self.get_settings(owner_id)
        updated = replace(current, **{key: value})
        self.save_settings(owner_id, updated)
        return updated

    def toggle_setting(self, owner_id: int, key: str) -> OwnerSettings:
        current = self.get_settings(owner_id)
        value = getattr(current, key)
        return self.update_setting(owner_id, key, not value)

    # ---------------------------------------------------------------- caching
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
        self._cache.set(key, cached)

        owner_id = self.owner_user_id(connection_id)
        settings = self.get_settings(owner_id) if owner_id is not None else self._config.default_settings
        if settings.store_history:
            self._db.upsert_message(cached, title, owner_id)

        return cached

    def find_cached(self, connection_id: str, chat_id: int, message_id: int) -> CachedMessage | None:
        for key in _lookup_keys(connection_id, chat_id, message_id):
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        cached = self._db.get_message(connection_id, chat_id, message_id)
        if cached is not None:
            self._cache.set((connection_id, cached.chat_id, message_id), cached)
            return cached
        return None

    def remove_cached(self, connection_id: str, chat_id: int, message_id: int) -> CachedMessage | None:
        cached = self.find_cached(connection_id, chat_id, message_id)

        for key in _lookup_keys(connection_id, chat_id, message_id):
            item = self._cache.pop(key)
            if item is not None:
                unlink_media(item.media)

        db_cached = self._db.mark_deleted(connection_id, chat_id, message_id)
        return cached or db_cached

    def purge_expired_all(self) -> int:
        """Чистит RAM-кэш с учётом персонального TTL каждого владельца."""
        now = time.time()
        stale: list[tuple[str, int, int]] = []
        for key, item in self._cache.items():
            owner_id = self.owner_user_id(key[0])
            settings = self.get_settings(owner_id) if owner_id is not None else self._config.default_settings
            ttl_seconds = max(settings.cache_ttl_hours, 0) * 3600
            if ttl_seconds and now - item.cached_at > ttl_seconds:
                stale.append(key)
        for key in stale:
            item = self._cache.pop(key)
            if item is not None:
                unlink_media(item.media)
        return len(stale)

    def purge_all(self) -> int:
        count = len(self._cache)
        for key, item in list(self._cache.items()):
            unlink_media(item.media)
            self._cache.pop(key)
        return count

    def purge_older_than(self, minutes: int) -> int:
        cutoff = time.time() - minutes * 60
        stale = [key for key, item in self._cache.items() if item.cached_at < cutoff]
        for key in stale:
            item = self._cache.pop(key)
            if item is not None:
                unlink_media(item.media)
        return len(stale)

    def cached_count(self, connection_id: str | None = None) -> int:
        if connection_id is None:
            return len(self._cache)
        return sum(1 for key, _ in self._cache.items() if key[0] == connection_id)

    def media_files_count(self) -> int:
        return sum(1 for _key, item in self._cache.items() if item.media and item.media.local_path)

    def chat_stats(self, connection_id: str) -> list[tuple[int, ChatStats]]:
        return self._db.chat_stats(connection_id)

    def db_count(self, connection_id: str | None = None) -> int:
        return self._db.count_messages(connection_id)

    def db_total_count(self) -> int:
        return self._db.count_all_messages()

    # -------------------------------------------------------------- deletion
    def mark_bot_deleted(self, connection_id: str, chat_id: int, message_id: int) -> None:
        self._bot_deleted.add((connection_id, chat_id, message_id))

    def was_bot_deleted(self, connection_id: str, chat_id: int, message_id: int) -> bool:
        key = (connection_id, chat_id, message_id)
        if key in self._bot_deleted:
            self._bot_deleted.discard(key)
            return True
        return False

    # ------------------------------------------------------------------ mute
    def start_mute(self, connection_id: str, chat_id: int, *, seconds: int | None = None, count: int | None = None) -> None:
        expires_at = time.time() + seconds if seconds is not None else None
        self._mute[(connection_id, chat_id)] = MuteSession(expires_at=expires_at, remaining=count)

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

    # ------------------------------------------------------------------- afk
    def should_send_afk_reply(self, connection_id: str, chat_id: int) -> bool:
        key = (connection_id, chat_id)
        now = time.time()
        last = self._afk_last_reply.get(key, 0)
        if now - last < AFK_REPLY_COOLDOWN_SECONDS:
            return False
        self._afk_last_reply[key] = now
        return True

    # ----------------------------------------------------------------- notes
    def note_add(self, owner_id: int, name: str, text: str) -> None:
        self._db.note_set(owner_id, name, text)

    def note_get(self, owner_id: int, name: str) -> str | None:
        return self._db.note_get(owner_id, name)

    def note_delete(self, owner_id: int, name: str) -> bool:
        return self._db.note_delete(owner_id, name)

    def note_list(self, owner_id: int) -> list[str]:
        return self._db.note_list(owner_id)

    # ------------------------------------------------------------- reminders
    def reminder_add(self, owner_id: int, connection_id: str, chat_id: int, minutes: float, text: str) -> int:
        fire_at = time.time() + minutes * 60
        return self._db.reminder_add(owner_id, connection_id, chat_id, fire_at, text)

    def reminder_delete(self, reminder_id: int) -> None:
        self._db.reminder_delete(reminder_id)

    def reminders_pending(self):
        return self._db.reminders_pending()


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
