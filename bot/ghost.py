from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from bot import subscription
from bot.storage import Storage

CODE_TTL_SECONDS = 300          # 5 минут на ввод кода привязки
MAX_FAILED_ATTEMPTS = 5         # после стольких неверных /link подряд — блокировка
LOCKOUT_SECONDS = 600           # длительность блокировки

# operator_user_id -> открытая сессия чата
_sessions: dict[int, "GhostSession"] = {}
# operator_user_id -> ждём текст для поиска чата
_search_pending: dict[int, dict] = {}
# operator_user_id -> (fail_count, locked_until)
_failed_attempts: dict[int, tuple[int, float]] = {}


@dataclass(slots=True)
class GhostSession:
    owner_id: int
    connection_id: str
    chat_id: int
    chat_title: str


def generate_link_code(storage: Storage, owner_id: int) -> str:
    code = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8].upper()
    storage.ghost_code_set(owner_id, code, time.time() + CODE_TTL_SECONDS)
    return code


def is_locked_out(operator_user_id: int) -> float | None:
    """Возвращает сколько секунд ещё ждать, если оператор временно заблокирован, иначе None."""
    entry = _failed_attempts.get(operator_user_id)
    if entry is None:
        return None
    _count, locked_until = entry
    remaining = locked_until - time.time()
    return remaining if remaining > 0 else None


def register_failed_attempt(operator_user_id: int) -> None:
    count, _ = _failed_attempts.get(operator_user_id, (0, 0.0))
    count += 1
    locked_until = time.time() + LOCKOUT_SECONDS if count >= MAX_FAILED_ATTEMPTS else 0.0
    _failed_attempts[operator_user_id] = (count, locked_until)


def clear_failed_attempts(operator_user_id: int) -> None:
    _failed_attempts.pop(operator_user_id, None)


def try_link(storage: Storage, operator_user_id: int, operator_chat_id: int, code: str) -> int | None:
    """Пытается привязать operator к владельцу по коду. Возвращает owner_id при успехе."""
    owner_id = storage.ghost_code_find_owner(code.strip().upper())
    if owner_id is None:
        register_failed_attempt(operator_user_id)
        return None
    storage.ghost_code_clear(owner_id)
    storage.ghost_link_add(owner_id, operator_user_id, operator_chat_id)
    clear_failed_attempts(operator_user_id)
    return owner_id


def resolve_operator_scope(storage: Storage, user_id: int) -> tuple[int, str] | None:
    """Определяет, чьими чатами может управлять user_id в /ghost: своими (если он
    владелец подключения с включённым режимом) или чужими (если он привязан как оператор).
    Возвращает (owner_id, connection_id) или None, если доступа нет.
    """
    own_connections = storage.connections_for_owner(user_id)
    if own_connections:
        settings = storage.get_settings(user_id)
        if settings.ghost_mode_enabled and subscription.feature_allowed(storage, user_id, "ghost"):
            return user_id, own_connections[0]

    link = storage.ghost_operator_owner(user_id)
    if link is not None:
        owner_id = int(link["owner_id"])
        settings = storage.get_settings(owner_id)
        if settings.ghost_mode_enabled and subscription.feature_allowed(storage, owner_id, "ghost"):
            connections = storage.connections_for_owner(owner_id)
            if connections:
                return owner_id, connections[0]

    return None


def open_session(operator_user_id: int, owner_id: int, connection_id: str, chat_id: int, chat_title: str) -> None:
    _sessions[operator_user_id] = GhostSession(owner_id, connection_id, chat_id, chat_title)


def get_session(operator_user_id: int) -> GhostSession | None:
    return _sessions.get(operator_user_id)


def close_session(operator_user_id: int) -> None:
    _sessions.pop(operator_user_id, None)


def sessions_watching(connection_id: str, chat_id: int) -> list[int]:
    """Операторы (и/или сам владелец), у которых сейчас открыт именно этот чат — для живой трансляции."""
    return [
        operator_id
        for operator_id, session in _sessions.items()
        if session.connection_id == connection_id and session.chat_id == chat_id
    ]


def set_search_pending(operator_user_id: int, owner_id: int, connection_id: str) -> None:
    _search_pending[operator_user_id] = {"owner_id": owner_id, "connection_id": connection_id}


def pop_search_pending(operator_user_id: int) -> dict | None:
    return _search_pending.pop(operator_user_id, None)


def is_search_pending(operator_user_id: int) -> bool:
    return operator_user_id in _search_pending
