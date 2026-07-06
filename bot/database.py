from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path

from bot.media import MediaRef
from bot.stats import ChatStats
from bot.models import CachedMessage

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path, *, save_media: bool, media_dir: Path) -> None:
        self.path = path
        self.save_media = save_media
        self.media_dir = media_dir
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.save_media:
            self.media_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS messages (
                connection_id TEXT NOT NULL,
                chat_id       INTEGER NOT NULL,
                message_id    INTEGER NOT NULL,
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

            CREATE INDEX IF NOT EXISTS idx_messages_cached_at
                ON messages(cached_at);
            CREATE INDEX IF NOT EXISTS idx_messages_deleted_at
                ON messages(deleted_at);
            """
        )
        self._conn.commit()

    def upsert_message(self, cached: CachedMessage, chat_title: str) -> None:
        is_new = self._conn.execute(
            """
            SELECT 1 FROM messages
            WHERE connection_id=? AND chat_id=? AND message_id=?
            """,
            (cached.connection_id, cached.chat_id, cached.message_id),
        ).fetchone() is None

        flags_json = json.dumps(cached.flags, ensure_ascii=False) if cached.flags else None
        media_kind = cached.media.kind if cached.media else None
        media_file_id = cached.media.file_id if cached.media else None
        media_path = str(cached.media.local_path) if cached.media and cached.media.local_path else None

        self._conn.execute(
            """
            INSERT INTO messages (
                connection_id, chat_id, message_id, chat_title,
                from_user_id, from_user_name, content, kind, flags,
                media_kind, media_file_id, media_path, cached_at, edited_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
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
                cached.connection_id,
                cached.chat_id,
                cached.message_id,
                chat_title,
                cached.from_user_id,
                cached.from_user_name,
                cached.content,
                cached.kind,
                flags_json,
                media_kind,
                media_file_id,
                media_path,
                cached.cached_at,
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

    def get_message(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> CachedMessage | None:
        for cid in (chat_id, -chat_id if chat_id > 0 else abs(chat_id)):
            row = self._conn.execute(
                """
                SELECT * FROM messages
                WHERE connection_id=? AND chat_id=? AND message_id=? AND deleted_at IS NULL
                """,
                (connection_id, cid, message_id),
            ).fetchone()
            if row is not None:
                return self._row_to_cached(row)

        row = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE connection_id=? AND message_id=? AND deleted_at IS NULL
            LIMIT 1
            """,
            (connection_id, message_id),
        ).fetchone()
        return self._row_to_cached(row) if row else None

    def mark_deleted(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> CachedMessage | None:
        cached = self.get_message(connection_id, chat_id, message_id)
        if cached is None:
            row = self._conn.execute(
                """
                SELECT * FROM messages
                WHERE connection_id=? AND message_id=?
                ORDER BY cached_at DESC LIMIT 1
                """,
                (connection_id, message_id),
            ).fetchone()
            cached = self._row_to_cached(row) if row else None

        self._conn.execute(
            """
            UPDATE messages SET deleted_at=?
            WHERE connection_id=? AND message_id=? AND deleted_at IS NULL
            """,
            (time.time(), connection_id, message_id),
        )
        self._conn.commit()
        return cached

    def count_messages(self, connection_id: str | None = None) -> int:
        if connection_id is None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE deleted_at IS NULL"
            ).fetchone()
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
            """
            SELECT chat_id, chat_title, kind, count
            FROM chat_stats
            WHERE connection_id=?
            """,
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

    def load_stats_into(self, target: dict[tuple[str, int], ChatStats], connection_id: str) -> None:
        for chat_id, stats in self.chat_stats(connection_id):
            target[(connection_id, chat_id)] = stats

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
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

    def persist_media_copy(self, source: Path, message_id: int, kind: str) -> Path | None:
        if not self.save_media or not source.exists():
            return None
        ext = source.suffix or ".bin"
        dest = self.media_dir / f"{message_id}_{kind}{ext}"
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


def _unlink_path(path_str: str) -> None:
    try:
        path = Path(path_str)
        if path.exists():
            path.unlink()
    except OSError:
        pass
