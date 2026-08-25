from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from bot.storage import Storage

router = Router(name="billing")


async def handle_promo_input(message: Message, storage: Storage) -> bool:
    return False

