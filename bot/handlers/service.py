from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.texts import Texts

router = Router(name="service")


@router.message(CommandStart())
async def cmd_start(message: Message, texts: Texts) -> None:
    await message.answer(texts.start)
