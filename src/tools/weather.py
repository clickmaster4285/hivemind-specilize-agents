"""weather tool — current weather and a 3-day forecast from Open-Meteo (free, no API key, open data).

Picked automatically by the router ("what's the weather", "kal barish hogi?"). Exact numbers in about
a second, no LLM: a model cannot know today's weather, and a web search is slow and vague for it.
Place: the city named in the message, else the city of the user's browser time zone
(Asia/Karachi → Karachi). Only the place name and coordinates are sent to open-meteo.com.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import date

import requests

from src.core.config import Config

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8

# WMO weather interpretation codes (open-meteo.com/en/docs)
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}
# "weather in Lahore", "Lahore weather", "Lahore ka mausam", "temperature at Dubai tomorrow"
_PLACE_RE = re.compile(
    r"\b(?:in|at|for|of)\s+([A-Za-z][A-Za-z .'-]{1,40}?)(?=\s+(?:today|now|tomorrow|this|right|ka|ki|ke|mein)\b|[?.!,]|$)"
    r"|([A-Za-z][A-Za-z .'-]{1,40}?)\s+(?:ka|ki|ke)?\s*(?:weather|mausam|temperature|forecast)\b",
    re.I,
)
_NOT_PLACES = {"the", "current", "today", "now", "what", "whats", "what's", "tell", "me", "is", "aaj", "kal",
               "todays", "today's", "my", "our", "here", "this", "city", "how", "hows", "how's", "will", "it",
               "be", "kya", "kaisa", "hai", "check", "show", "give"}


class WeatherError(RuntimeError):
    """Place not found or the weather service did not answer."""


def place_from_text(text: str) -> str | None:
    for m in _PLACE_RE.finditer(text or ""):
        place = (m.group(1) or m.group(2) or "").strip(" .'-")
        words = [w for w in place.split() if w.lower() not in _NOT_PLACES]
        if words:
            return " ".join(words)
    return None


def place_from_time_zone(zone: str | None) -> str | None:
    """'Asia/Karachi' → 'Karachi', 'America/New_York' → 'New York'."""
    if not zone or "/" not in zone:
        return None
    return zone.rsplit("/", 1)[-1].replace("_", " ")


def geocode(place: str) -> dict:
    resp = requests.get(GEOCODE_URL, params={"name": place, "count": 1, "language": "en", "format": "json"},
                        timeout=TIMEOUT)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        raise WeatherError(f"could not find a place called {place!r}")
    return results[0]


def forecast(lat: float, lon: float) -> dict:
    resp = requests.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def describe(place: dict, data: dict) -> str:
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    where = ", ".join(x for x in (place.get("name"), place.get("admin1"), place.get("country")) if x)
    local_time = str(cur.get("time", "")).replace("T", " ")
    lines = [
        f"**Weather in {where}** — now ({local_time} local time): **{cur.get('temperature_2m')} °C**, "
        f"{WMO.get(cur.get('weather_code'), 'unknown conditions')}, feels like {cur.get('apparent_temperature')} °C, "
        f"humidity {cur.get('relative_humidity_2m')} %, wind {cur.get('wind_speed_10m')} km/h"
        + (f", precipitation {cur.get('precipitation')} mm" if cur.get("precipitation") else "") + ".",
        "",
        "| Day | Conditions | Min | Max | Chance of rain |",
        "|---|---|---|---|---|",
    ]
    for i, day in enumerate(daily.get("time") or []):
        label = "Today" if i == 0 else "Tomorrow" if i == 1 else date.fromisoformat(day).strftime("%A")
        lines.append(
            f"| {label} ({day}) | {WMO.get(daily['weather_code'][i], '—')} | {daily['temperature_2m_min'][i]} °C "
            f"| {daily['temperature_2m_max'][i]} °C | {daily['precipitation_probability_max'][i]} % |"
        )
    lines += ["", "_Source: [Open-Meteo](https://open-meteo.com) (weather data CC BY 4.0)._"]
    return "\n".join(lines)


def run_weather(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    place: str | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    from src.core.clock import current_time_zone

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Weather", "stage": "chat",
             "accent": "#3a8fc4", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "open-meteo"})
    named = (place or '').strip() or place_from_text(query)
    place_name = named or place_from_time_zone(current_time_zone())
    try:
        if not place_name:
            raise WeatherError("no place in the question and no time zone from the browser — name a city")
        emit({"type": "status", "agent_id": "assistant", "message": f"Getting the weather for {place_name}…"})
        try:
            place = geocode(place_name)
        except WeatherError:
            fallback = place_from_time_zone(current_time_zone())
            if not named or not fallback or fallback == place_name:
                raise
            place = geocode(fallback)              # "weather in the city" etc.: use the user's own city
        output = describe(place, forecast(place["latitude"], place["longitude"]))
        if not named:
            output += f"\n\n_No city in your question, so I used yours from your time zone ({place['name']}). Ask e.g. \"weather in Lahore\" for another place._"
    except (WeatherError, requests.RequestException, KeyError, ValueError) as exc:
        errors.append(str(exc))
        output = f"I could not get the weather: {exc}."
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
