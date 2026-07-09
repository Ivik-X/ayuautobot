from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    """Простой LRU-кэш ограниченного размера.

    Используется как быстрый горячий слой перед SQLite: большинство правок и
    удалений прилетают от Telegram в течение секунд-минут после отправки
    сообщения, поэтому попадание в RAM-кэш почти всегда экономит чтение с диска.
    При превышении max_size вытесняются самые старые (по времени доступа) записи.
    """

    def __init__(self, max_size: int) -> None:
        self.max_size = max(1, max_size)
        self._data: "OrderedDict[K, V]" = OrderedDict()

    def get(self, key: K) -> V | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: K, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)

    def pop(self, key: K) -> V | None:
        return self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: K) -> bool:
        return key in self._data

    def items(self):
        return self._data.items()

    def set_max_size(self, max_size: int) -> None:
        self.max_size = max(1, max_size)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
