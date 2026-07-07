from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path

from bot.media import MediaRef
from bot.models import CachedMessage
from bot.stats import ChatStats

logger = logging.getLogger(__name__)


class Database:
    """Единая SQLite БД для всего бота.

    Рассчитана на слабый сервер (1 ядро / 1GB RAM / 10GB NVMe):
    - WAL + NORMAL synchronous — минимум лишних fsync;
    - все данные (сообщения, настройки, заметки, напоминания) в одном файле,
      чтобы не плодить дескрипторы и упростить бэкап;
    - есть snapshot() для консистентного бэкапа "на лету" через sqlite backup API;
    - есть trim_after_backup() чтобы после отправки бэкапа админу схлопнуть
      локальную историю и освободить место на диске (VACUUM).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

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

            CREATE TABLE IF NOT EXISTS notes (
                owner_id  INTEGER NOT NULL,
                name      TEXT NOT NULL,
                content   TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (owner_id, name)
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id      INTEGER NOT NULL,
                connection_id TEXT NOT NULL,
                chat_id       INTEGER NOT NULL,
                fire_at       REAL NOT NULL,
                text          TEXT NOT NULL,
                created_at    REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_cached_at ON messages(cached_at);
            CREATE INDEX IF NOT EXISTS idx_messages_deleted_at ON messages(deleted_at);
            CREATE INDEX IF NOT EXISTS idx_messages_owner ON messages(owner_id);
            """
        )
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

    def all_owner_ids(self) -> list[int]:
        rows = self._conn.execute("SELECT owner_id FROM owners").fetchall()
        return [int(r["owner_id"]) for r in rows]

    def owners_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM owners").fetchone()
        return int(row[0]) if row else 0

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
    def upsert_message(self, cached: CachedMessage, chat_title: str, owner_id: int | None) -> None:
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
                media_kind, media_file_id, media_path, cached_at, edited_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
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
                media_kind, media_file_id, media_path, cached.cached_at,
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

    def media_files_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE media_path IS NOT NULL AND deleted_at IS NULL"
        ).fetchone()
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

    def persist_media_copy(self, source: Path, media_dir: Path, message_id: int, kind: str) -> Path | None:
        if not source.exists():
            return None
        media_dir.mkdir(parents=True, exist_ok=True)
        ext = source.suffix or ".bin"
        dest = media_dir / f"{message_id}_{kind}{ext}"
        try:
            shutil.copy2(source, dest)
            return dest
        except OSError:
            logger.exception("Не удалось сохранить медиа в БД")
            return None

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

    # ------------------------------------------------------------------- notes
    def note_set(self, owner_id: int, name: str, content: str) -> None:
        self._conn.execute(
            """
            INSERT INTO notes (owner_id, name, content, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, name) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at
            """,
            (owner_id, name.lower(), content, time.time()),
        )
        self._conn.commit()

    def note_get(self, owner_id: int, name: str) -> str | None:
        row = self._conn.execute(
            "SELECT content FROM notes WHERE owner_id=? AND name=?", (owner_id, name.lower())
        ).fetchone()
        return row["content"] if row else None

    def note_delete(self, owner_id: int, name: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM notes WHERE owner_id=? AND name=?", (owner_id, name.lower())
        )
        self._conn.commit()
        return cur.rowcount > 0

    def note_list(self, owner_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM notes WHERE owner_id=? ORDER BY name", (owner_id,)
        ).fetchall()
        return [r["name"] for r in rows]

    # --------------------------------------------------------------- reminders
    def reminder_add(self, owner_id: int, connection_id: str, chat_id: int, fire_at: float, text: str) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO reminders (owner_id, connection_id, chat_id, fire_at, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (owner_id, connection_id, chat_id, fire_at, text, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def reminder_delete(self, reminder_id: int) -> None:
        self._conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
        self._conn.commit()

    def reminders_pending(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM reminders ORDER BY fire_at").fetchall()

    # -------------------------------------------------------------- backup/ops
    def snapshot(self, dest_path: Path) -> Path:
        """Консистентный снимок БД через встроенный SQLite backup API.

        Работает без остановки бота (WAL позволяет читать во время записи).
        """
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_conn = sqlite3.connect(dest_path)
        try:
            self._conn.backup(dest_conn)
        finally:
            dest_conn.close()
        return dest_path

    def trim_after_backup(self, keep_hours: float) -> int:
        """Схлопывает локальную историю после успешной отправки бэкапа админу.

        Полная история уже уехала админу в ЛС файлом — на диске оставляем
        только "горячий" хвост (keep_hours), остальное удаляем и делаем VACUUM,
        чтобы реально освободить место на NVMe.
        """
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
