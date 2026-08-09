"""Thin HTTP/JSON layer around the keyless Open-Meteo API.

Design rule: this module knows nothing about MCP. It only knows how to turn a
place name into coordinates and coordinates into weather numbers. All network
traffic and JSON parsing lives here so the tool layer stays trivial.

The provider is Open-Meteo (https://open-meteo.com): free, no signup, no API
key, generous non-commercial limits, and geocoding + current + forecast all in
one service.

Endpoints used (both keyless):
  - geocoding -> https://geocoding-api.open-meteo.com/v1/search
  - forecast  -> https://api.open-meteo.com/v1/forecast
"""

from __future__ import annotations

import requests

from . import helpers
from .config import DEFAULT_HORIZON_DAYS, MAX_HORIZON_DAYS

GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_S = 15

CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,"
    "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m"
)

DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
)

# WMO weather-interpretation codes -> friendly text. Reference: the "Weather
# variable documentation" on the Open-Meteo site.
WEATHER_DESCRIPTIONS = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle (light)",
    57: "freezing drizzle (heavy)",
    61: "light rain",
    63: "steady rain",
    65: "heavy rain",
    66: "freezing rain (light)",
    67: "freezing rain (heavy)",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "moderate showers",
    82: "torrential showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm, slight hail",
    99: "thunderstorm, heavy hail",
}


def _interpret_code(code) -> str:
    """Map a numeric WMO code to a short human-readable phrase."""
    if code is None:
        return "not reported"
    try:
        return WEATHER_DESCRIPTIONS.get(int(code), f"code {code}")
    except (TypeError, ValueError):
        return "unknown"


def _fetch_json(url: str, params: dict) -> dict:
    """GET a JSON payload, raising RuntimeError (never a traceback) on failure."""
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Upstream weather request failed: {exc}") from exc


def geocode(place: str) -> dict:
    """Resolve a place into coordinates.

    Accepts a place name ("Madrid", "Austin, TX", "Tokyo") or a raw
    "lat,lon" pair. Returns a dict with latitude, longitude, a normalized
    display name and timezone. Raises ValueError when the place cannot be
    found.
    """
    raw = (place or "").strip()
    if not raw:
        raise ValueError("Please provide a location.")

    # Raw "lat,lon" pair?
    if "," in raw:
        first, second = (part.strip() for part in raw.split(",", 1))
        try:
            lat, lon = float(first), float(second)
            return {
                "latitude": lat,
                "longitude": lon,
                "name": f"{lat:.4f}, {lon:.4f}",
                "region": "",
                "timezone": "auto",
            }
        except ValueError:
            pass  # not numeric -> treat as a place name below

    city = raw.split(",")[0].strip()
    payload = _fetch_json(
        GEOCODING_ENDPOINT,
        {"name": city, "count": 1, "language": "en", "format": "json"},
    )
    hits = payload.get("results") or []
    if not hits:
        raise ValueError(f"No known place matches {place!r}.")

    top = hits[0]
    parts = [top.get("name"), top.get("admin1"), top.get("country")]
    label = ", ".join(part for part in parts if part)
    return {
        "latitude": float(top["latitude"]),
        "longitude": float(top["longitude"]),
        "name": label or city,
        "region": top.get("country", ""),
        "timezone": top.get("timezone", "auto"),
    }


def current_conditions(place: str) -> dict:
    """Live conditions for a place: temperature, apparent temp, humidity, wind, precip."""
    where = geocode(place)
    payload = _fetch_json(
        FORECAST_ENDPOINT,
        {
            "latitude": where["latitude"],
            "longitude": where["longitude"],
            "current": CURRENT_FIELDS,
            "timezone": "auto",
        },
    )
    current = payload.get("current", {})
    units = payload.get("current_units", {})
    return {
        "location": where["name"],
        "observed_at": current.get("time"),
        "temperature": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_direction": current.get("wind_direction_10m"),
        "conditions": _interpret_code(current.get("weather_code")),
        "is_day": bool(current.get("is_day")),
        "units": {
            "temperature": units.get("temperature_2m", "°C"),
            "wind_speed": units.get("wind_speed_10m", "km/h"),
            "precipitation": units.get("precipitation", "mm"),
        },
    }


def daily_outlook(place: str, horizon: int = DEFAULT_HORIZON_DAYS) -> dict:
    """Multi-day outlook: daily high/low, rain chance + amount, wind, conditions."""
    horizon = helpers.clamp(int(horizon), 1, MAX_HORIZON_DAYS)
    where = geocode(place)
    payload = _fetch_json(
        FORECAST_ENDPOINT,
        {
            "latitude": where["latitude"],
            "longitude": where["longitude"],
            "daily": DAILY_FIELDS,
            "forecast_days": horizon,
            "timezone": "auto",
        },
    )
    daily = payload.get("daily", {})
    units = payload.get("daily_units", {})
    dates = daily.get("time", [])
    entries = []
    for i, day in enumerate(dates):
        entries.append({
            "date": day,
            "high": helpers.at_index(daily.get("temperature_2m_max"), i),
            "low": helpers.at_index(daily.get("temperature_2m_min"), i),
            "precipitation_probability": helpers.at_index(daily.get("precipitation_probability_max"), i),
            "precipitation_mm": helpers.at_index(daily.get("precipitation_sum"), i),
            "wind_speed_max": helpers.at_index(daily.get("wind_speed_10m_max"), i),
            "conditions": _interpret_code(helpers.at_index(daily.get("weather_code"), i)),
        })
    return {
        "location": where["name"],
        "days": len(entries),
        "units": {
            "temperature": units.get("temperature_2m_max", "°C"),
            "wind_speed": units.get("wind_speed_10m_max", "km/h"),
            "precipitation": units.get("precipitation_sum", "mm"),
        },
        "outlook": entries,
    }


def day_for(when: str, entries: list) -> int:
    """Map a 'when' string to an index into an outlook list.

    Accepts 'today', 'tomorrow', or an ISO date 'YYYY-MM-DD'. Falls back to
    the first day when nothing matches.
    """
    requested = (when or "today").strip().lower()
    if requested in ("today", "now", ""):
        return 0
    if requested == "tomorrow":
        return 1
    for i, entry in enumerate(entries):
        if entry.get("date") == requested:
            return i
    return 0
