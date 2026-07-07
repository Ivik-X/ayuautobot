from __future__ import annotations

import aiohttp

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_WEATHER_CODES = {
    0: "☀️ ясно",
    1: "🌤 малооблачно",
    2: "⛅️ переменная облачность",
    3: "☁️ пасмурно",
    45: "🌫 туман",
    48: "🌫 изморозь",
    51: "🌦 морось слабая",
    53: "🌦 морось",
    55: "🌦 морось сильная",
    61: "🌧 дождь слабый",
    63: "🌧 дождь",
    65: "🌧 дождь сильный",
    71: "🌨 снег слабый",
    73: "🌨 снег",
    75: "🌨 снег сильный",
    80: "🌦 ливень слабый",
    81: "🌦 ливень",
    82: "⛈ ливень сильный",
    95: "⛈ гроза",
    96: "⛈ гроза с градом",
    99: "⛈ гроза с сильным градом",
}


class WeatherError(Exception):
    pass


async def get_weather(session: aiohttp.ClientSession, city: str) -> str:
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with session.get(
            GEOCODE_URL, params={"name": city, "count": 1, "language": "ru"}, timeout=timeout
        ) as resp:
            geo = await resp.json(content_type=None)
    except Exception as exc:
        raise WeatherError("сервис геокодирования недоступен") from exc

    results = geo.get("results") or []
    if not results:
        raise WeatherError(f"город «{city}» не найден")

    place = results[0]
    lat, lon = place["latitude"], place["longitude"]
    name = place.get("name", city)
    country = place.get("country", "")

    try:
        async with session.get(
            FORECAST_URL,
            params={"latitude": lat, "longitude": lon, "current_weather": "true"},
            timeout=timeout,
        ) as resp:
            forecast = await resp.json(content_type=None)
    except Exception as exc:
        raise WeatherError("сервис погоды недоступен") from exc

    current = forecast.get("current_weather")
    if not current:
        raise WeatherError("нет данных о погоде")

    temp = current.get("temperature")
    wind = current.get("windspeed")
    code = current.get("weathercode")
    condition = _WEATHER_CODES.get(code, "погода")

    location = f"{name}, {country}" if country else name
    return (
        f"📍 <b>{location}</b>\n"
        f"{condition}\n"
        f"🌡 Температура: <b>{temp}°C</b>\n"
        f"💨 Ветер: <b>{wind} км/ч</b>"
    )
