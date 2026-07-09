from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from bot.media import MediaRef
from bot.models import CachedMessage
from bot.stats import ChatStats

logger = logging.getLogger(__name__)


class Database:
    """Единая SQLite БД для всего бота (см. README про архитектуру для слабого сервера)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA temp_store=MEMORY;

            CREATE TABLE IF NOT EXISTS owners (
                owner_id      INTEGER PRIMARY KEY,
                settings      TEXT NOT NULL DEFAULT '{}',
                is_admin      INTEGER NOT NULL DEFAULT 0,
                created_at    REAL NOT NULL,
                last_seen     REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connections (
                connection_id TEXT PRIMARY KEY,
                owner_id      INTEGER NOT NULL,
                user_chat_id  INTEGER NOT NULL,
                is_enabled    INTEGER NOT NULL DEFAULT 1,
                updated_at    REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                connection_id TEXT NOT NULL,
                chat_id       INTEGER NOT NULL,
                message_id    INTEGER NOT NULL,
                owner_id      INTEGER,
                chat_title    TEXT NOT NULL DEFAULT '',
                from_user_id  INTEGER,
                from_user_name TEXT NOT NULL DEFAULT '',
                content       TEXT NOT NULL DEFAULT '',
                kind          TEXT NOT NULL DEFAULT 'text',
                flags         TEXT,
                media_kind    TEXT,
                media_file_id TEXT,
                media_path    TEXT,
                cached_at     REAL NOT NULL,
                edited_at     REAL,
                deleted_at    REAL,
                bot_caused    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (connection_id, chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS chat_stats (
                connection_id TEXT NOT NULL,
                chat_id       INTEGER NOT NULL,
                chat_title    TEXT NOT NULL DEFAULT '',
                kind          TEXT NOT NULL,
                count         INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (connection_id, chat_id, kind)
            );

            CREATE TABLE IF NOT EXISTS say_presets (
                owner_id   INTEGER NOT NULL,
                name       TEXT NOT NULL,
                items      TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY (owner_id, name)
            );

            CREATE TABLE IF NOT EXISTS notifications_queue (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id      INTEGER NOT NULL,
                kind          TEXT NOT NULL,
                payload       TEXT NOT NULL,
                media_kind    TEXT,
                media_file_id TEXT,
                media_path    TEXT,
                created_at    REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS global_settings (
                id   INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_messages_cached_at ON messages(cached_at);
            CREATE INDEX IF NOT EXISTS idx_messages_deleted_at ON messages(deleted_at);
            CREATE INDEX IF NOT EXISTS idx_notifications_owner ON notifications_queue(owner_id);
            """
        )
        self._conn.commit()

    def _migrate(self) -> None:
        """Докатывает схему для БД, оставшихся от предыдущих версий бота."""
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "owner_id" not in columns:
            logger.info("Миграция БД: добавляю колонку messages.owner_id")
            self._conn.execute("ALTER TABLE messages ADD COLUMN owner_id INTEGER")
            self._conn.commit()
        if "bot_caused" not in columns:
            logger.info("Миграция БД: добавляю колонку messages.bot_caused")
            self._conn.execute("ALTER TABLE messages ADD COLUMN bot_caused INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_owner ON messages(owner_id)")
        self._conn.commit()

    # ------------------------------------------------------------------ owners
    def ensure_owner(self, owner_id: int, *, is_admin: bool = False) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO owners (owner_id, settings, is_admin, created_at, last_seen)
            VALUES (?, '{}', ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                is_admin = MAX(owners.is_admin, excluded.is_admin)
            """,
            (owner_id, int(is_admin), now, now),
        )
        self._conn.commit()

    def get_owner_settings_raw(self, owner_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT settings FROM owners WHERE owner_id=?", (owner_id,)
        ).fetchone()
        return row["settings"] if row else None

    def save_owner_settings(self, owner_id: int, settings_json: str) -> None:
        self._conn.execute(
            "UPDATE owners SET settings=? WHERE owner_id=?",
            (settings_json, owner_id),
        )
        self._conn.commit()

    def owners_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM owners").fetchone()
        return int(row[0]) if row else 0

    def all_owners(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM owners ORDER BY last_seen DESC").fetchall()

    # -------------------------------------------------------------- connections
    def upsert_connection(self, connection_id: str, owner_id: int, user_chat_id: int, is_enabled: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO connections (connection_id, owner_id, user_chat_id, is_enabled, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                owner_id=excluded.owner_id,
                user_chat_id=excluded.user_chat_id,
                is_enabled=excluded.is_enabled,
                updated_at=excluded.updated_at
            """,
            (connection_id, owner_id, user_chat_id, int(is_enabled), time.time()),
        )
        self._conn.commit()

    def owner_for_connection(self, connection_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT owner_id FROM connections WHERE connection_id=?", (connection_id,)
        ).fetchone()
        return int(row["owner_id"]) if row else None

    def all_connections(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM connections").fetchall()

    def connections_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM connections WHERE is_enabled=1").fetchone()
        return int(row[0]) if row else 0

    # ---------------------------------------------------------------- messages
    def upsert_message(self, cached: CachedMessage, chat_title: str, owner_id: int | None, *, bot_caused: bool = False) -> None:
        is_new = self._conn.execute(
            "SELECT 1 FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?",
            (cached.connection_id, cached.chat_id, cached.message_id),
        ).fetchone() is None

        flags_json = json.dumps(cached.flags, ensure_ascii=False) if cached.flags else None
        media_kind = cached.media.kind if cached.media else None
        media_file_id = cached.media.file_id if cached.media else None
        media_path = str(cached.media.local_path) if cached.media and cached.media.local_path else None

        self._conn.execute(
            """
            INSERT INTO messages (
                connection_id, chat_id, message_id, owner_id, chat_title,
                from_user_id, from_user_name, content, kind, flags,
                media_kind, media_file_id, media_path, cached_at, edited_at, deleted_at, bot_caused
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            ON CONFLICT(connection_id, chat_id, message_id) DO UPDATE SET
                from_user_name=excluded.from_user_name,
                content=excluded.content,
                kind=excluded.kind,
                flags=excluded.flags,
                media_kind=excluded.media_kind,
                media_file_id=excluded.media_file_id,
                media_path=excluded.media_path,
                edited_at=?
            """,
            (
                cached.connection_id, cached.chat_id, cached.message_id, owner_id, chat_title,
                cached.from_user_id, cached.from_user_name, cached.content, cached.kind, flags_json,
                media_kind, media_file_id, media_path, cached.cached_at, int(bot_caused),
                time.time(),
            ),
        )

        if is_new:
            self._conn.execute(
                """
                INSERT INTO chat_stats (connection_id, chat_id, chat_title, kind, count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(connection_id, chat_id, kind) DO UPDATE SET
                    count = count + 1,
                    chat_title = excluded.chat_title
                """,
                (cached.connection_id, cached.chat_id, chat_title, cached.kind),
            )

        self._conn.commit()

    def get_message(self, connection_id: str, chat_id: int, message_id: int) -> CachedMessage | None:
        for cid in (chat_id, -chat_id if chat_id > 0 else abs(chat_id)):
            row = self._conn.execute(
                "SELECT * FROM messages WHERE connection_id=? AND chat_id=? AND message_id=? AND deleted_at IS NULL",
                (connection_id, cid, message_id),
            ).fetchone()
            if row is not None:
                return self._row_to_cached(row)

        row = self._conn.execute(
            "SELECT * FROM messages WHERE connection_id=? AND message_id=? AND deleted_at IS NULL LIMIT 1",
            (connection_id, message_id),
        ).fetchone()
        return self._row_to_cached(row) if row else None

    def get_message_any(self, connection_id: str, chat_id: int, message_id: int) -> CachedMessage | None:
        """Как get_message, но не фильтрует уже помеченные удалёнными (для digest-очереди)."""
        row = self._conn.execute(
            "SELECT * FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?",
            (connection_id, chat_id, message_id),
        ).fetchone()
        if row is not None:
            return self._row_to_cached(row)
        row = self._conn.execute(
            "SELECT * FROM messages WHERE connection_id=? AND message_id=? LIMIT 1",
            (connection_id, message_id),
        ).fetchone()
        return self._row_to_cached(row) if row else None

    def mark_deleted(self, connection_id: str, chat_id: int, message_id: int) -> CachedMessage | None:
        cached = self.get_message(connection_id, chat_id, message_id)
        if cached is None:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE connection_id=? AND message_id=? ORDER BY cached_at DESC LIMIT 1",
                (connection_id, message_id),
            ).fetchone()
            cached = self._row_to_cached(row) if row else None

        self._conn.execute(
            "UPDATE messages SET deleted_at=? WHERE connection_id=? AND message_id=? AND deleted_at IS NULL",
            (time.time(), connection_id, message_id),
        )
        self._conn.commit()
        return cached

    def is_bot_caused(self, connection_id: str, chat_id: int, message_id: int) -> bool:
        row = self._conn.execute(
            "SELECT bot_caused FROM messages WHERE connection_id=? AND message_id=? LIMIT 1",
            (connection_id, message_id),
        ).fetchone()
        return bool(row["bot_caused"]) if row else False

    def count_messages(self, connection_id: str | None = None) -> int:
        if connection_id is None:
            row = self._conn.execute("SELECT COUNT(*) FROM messages WHERE deleted_at IS NULL").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE connection_id=? AND deleted_at IS NULL",
                (connection_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def count_all_messages(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return int(row[0]) if row else 0

    def chat_stats(self, connection_id: str) -> list[tuple[int, ChatStats]]:
        rows = self._conn.execute(
            "SELECT chat_id, chat_title, kind, count FROM chat_stats WHERE connection_id=?",
            (connection_id,),
        ).fetchall()

        grouped: dict[int, ChatStats] = {}
        for row in rows:
            chat_id = int(row["chat_id"])
            stats = grouped.get(chat_id)
            if stats is None:
                stats = ChatStats(title=row["chat_title"] or str(chat_id))
                grouped[chat_id] = stats
            stats.add(row["kind"], int(row["count"]))

        result = [(chat_id, stats) for chat_id, stats in grouped.items()]
        result.sort(key=lambda item: item[1].total, reverse=True)
        return result

    def purge_older_than(self, hours: float) -> int:
        if hours <= 0:
            return 0
        cutoff = time.time() - hours * 3600
        rows = self._conn.execute(
            "SELECT media_path FROM messages WHERE cached_at < ? AND media_path IS NOT NULL",
            (cutoff,),
        ).fetchall()
        for row in rows:
            _unlink_path(row["media_path"])

        cur = self._conn.execute("DELETE FROM messages WHERE cached_at < ?", (cutoff,))
        self._conn.execute(
            """
            DELETE FROM chat_stats
            WHERE (connection_id, chat_id) NOT IN (
                SELECT DISTINCT connection_id, chat_id FROM messages
            )
            """
        )
        self._conn.commit()
        return cur.rowcount

    def _row_to_cached(self, row: sqlite3.Row) -> CachedMessage:
        flags = json.loads(row["flags"]) if row["flags"] else None
        media = None
        if row["media_kind"] and row["media_file_id"]:
            local_path = Path(row["media_path"]) if row["media_path"] else None
            media = MediaRef(
                kind=row["media_kind"],
                file_id=row["media_file_id"],
                local_path=local_path if local_path and local_path.exists() else None,
            )
        return CachedMessage(
            connection_id=row["connection_id"],
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            from_user_id=row["from_user_id"],
            from_user_name=row["from_user_name"],
            content=row["content"],
            cached_at=float(row["cached_at"]),
            media=media,
            flags=flags,
            kind=row["kind"],
        )

    # ------------------------------------------------------------- say presets
    def preset_set(self, owner_id: int, name: str, items: list[dict]) -> None:
        self._conn.execute(
            """
            INSERT INTO say_presets (owner_id, name, items, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, name) DO UPDATE SET items=excluded.items, updated_at=excluded.updated_at
            """,
            (owner_id, name.lower(), json.dumps(items, ensure_ascii=False), time.time()),
        )
        self._conn.commit()

    def preset_get(self, owner_id: int, name: str) -> list[dict] | None:
        row = self._conn.execute(
            "SELECT items FROM say_presets WHERE owner_id=? AND name=?", (owner_id, name.lower())
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["items"])
        except (json.JSONDecodeError, TypeError):
            return []

    def preset_delete(self, owner_id: int, name: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM say_presets WHERE owner_id=? AND name=?", (owner_id, name.lower())
        )
        self._conn.commit()
        return cur.rowcount > 0

    def preset_list(self, owner_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM say_presets WHERE owner_id=? ORDER BY name", (owner_id,)
        ).fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------------- notifications queue
    def queue_add(
        self,
        owner_id: int,
        kind: str,
        payload: str,
        *,
        media_kind: str | None = None,
        media_file_id: str | None = None,
        media_path: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO notifications_queue
                (owner_id, kind, payload, media_kind, media_file_id, media_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (owner_id, kind, payload, media_kind, media_file_id, media_path, time.time()),
        )
        self._conn.commit()

    def queue_count(self, owner_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM notifications_queue WHERE owner_id=?", (owner_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def queue_list(self, owner_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM notifications_queue WHERE owner_id=? ORDER BY created_at", (owner_id,)
        ).fetchall()

    def queue_clear(self, owner_id: int) -> None:
        self._conn.execute("DELETE FROM notifications_queue WHERE owner_id=?", (owner_id,))
        self._conn.commit()

    # ------------------------------------------------------------- global settings
    def get_global_settings_raw(self) -> str | None:
        row = self._conn.execute("SELECT data FROM global_settings WHERE id=1").fetchone()
        return row["data"] if row else None

    def save_global_settings(self, data_json: str) -> None:
        self._conn.execute(
            """
            INSERT INTO global_settings (id, data) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data
            """,
            (data_json,),
        )
        self._conn.commit()

    # -------------------------------------------------------------- backup/ops
    def snapshot(self, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_conn = sqlite3.connect(dest_path)
        try:
            self._conn.backup(dest_conn)
        finally:
            dest_conn.close()
        return dest_path

    def trim_after_backup(self, keep_hours: float) -> int:
        removed = self.purge_older_than(keep_hours)
        try:
            self._conn.execute("VACUUM")
        except sqlite3.OperationalError:
            logger.warning("VACUUM пропущен (БД занята)")
        return removed

    def file_size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


def _unlink_path(path_str: str) -> None:
    try:
        path = Path(path_str)
        if path.exists():
            path.unlink()
    except OSError:
        pass
