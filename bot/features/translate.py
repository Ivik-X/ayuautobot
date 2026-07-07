from __future__ import annotations

import aiohttp

API_URL = "https://api.mymemory.translated.net/get"


class TranslateError(Exception):
    pass


async def translate(session: aiohttp.ClientSession, text: str, target_lang: str) -> str:
    if len(text) > 500:
        raise TranslateError("слишком длинный текст (максимум 500 символов)")

    params = {"q": text, "langpair": f"auto|{target_lang}"}
    try:
        async with session.get(API_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)
    except Exception as exc:
        raise TranslateError("сервис перевода недоступен") from exc

    try:
        translated = data["responseData"]["translatedText"]
    except (KeyError, TypeError) as exc:
        raise TranslateError("не удалось получить перевод") from exc

    if not translated:
        raise TranslateError("пустой перевод")
    return translated
