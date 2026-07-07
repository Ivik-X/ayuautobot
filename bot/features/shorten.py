from __future__ import annotations

import aiohttp

API_URL = "https://is.gd/create.php"


class ShortenError(Exception):
    pass


async def shorten(session: aiohttp.ClientSession, url: str) -> str:
    if not (url.startswith("http://") or url.startswith("https://")):
        url = f"https://{url}"

    params = {"format": "simple", "url": url}
    try:
        async with session.get(API_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            text = await resp.text()
    except Exception as exc:
        raise ShortenError("сервис сокращения недоступен") from exc

    text = text.strip()
    if not text.startswith("http"):
        raise ShortenError(text or "не удалось сократить ссылку")
    return text
