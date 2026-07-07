from __future__ import annotations

import aiohttp

API_URL = "https://api.frankfurter.app/latest"


class CurrencyError(Exception):
    pass


async def convert(session: aiohttp.ClientSession, amount: float, frm: str, to: str) -> tuple[float, float]:
    params = {"amount": amount, "from": frm, "to": to}
    try:
        async with session.get(API_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise CurrencyError("не удалось получить курс (проверьте коды валют)")
            data = await resp.json(content_type=None)
    except CurrencyError:
        raise
    except Exception as exc:
        raise CurrencyError("сервис конвертации недоступен") from exc

    rates = data.get("rates") or {}
    if to not in rates:
        raise CurrencyError(f"нет курса для {to}")
    return amount, float(rates[to])
