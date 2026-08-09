"""
Open-Meteo weather engine backing the weather-prediction MCP server.

This module is the broker/adapter for the weather-prediction MCP server
(weather_mcp_server.py): it owns every HTTP call and all JSON parsing so the
MCP tool functions stay thin. All data comes from Open-Meteo
(https://open-meteo.com/) - a free weather API that requires no API key and no
signup - plus its companion Geocoding API to resolve place names to coordinates.

Endpoints used (all GET, all key-less):
    Geocoding:  https://geocoding-api.open-meteo.com/v1/search?name=...
    Forecast:   https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..
    Archive:    https://archive-api.open-meteo.com/v1/archive?latitude=..&longitude=..

Design notes:
    - geocode() results are cached in-process for CACHE_TTL_SECONDS to avoid
      re-hitting the geocoding API for repeated lookups of the same place.
    - Every function raises WeatherLookupError (a clean, message-bearing
      exception) instead of leaking raw HTTP/parse errors; the MCP server
      catches it and returns {"error": ...} to the agent.
    - Units are configurable per call ("celsius" or "fahrenheit"). The
      recommendation thresholds are always evaluated in metric internally and
      reported in the caller's chosen unit.

Swap-in note: if you ever want to swap Open-Meteo for another provider (e.g.
the National Weather Service API for US alerts), keep the same function
signatures here and change the endpoints/parsing inside - the MCP surface in
weather_mcp_server.py does not need to change.
"""

import logging
import time
from datetime import date, timedelta
from typing import Any

import requests

logger = logging.getLogger("weather-broker")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

REQUEST_TIMEOUT_SECONDS = 15
MAX_FORECAST_DAYS = 16

# In-process geocoding cache: {lowercased query: (expires_at, result)}
_GEOCODE_CACHE: dict[str, tuple[float, dict]] = {}
GEOCODE_CACHE_TTL_SECONDS = 15 * 60


class WeatherLookupError(Exception):
    """Raised when a location can't be resolved or the weather API fails."""


# Recommendation thresholds (always evaluated in metric internally).
# Precipitation probability (percent).
RAIN_MAYBE_PERCENT = 40
RAIN_YES_PERCENT = 70
# Temperature in Celsius.
JACKET_MAX_TEMP_C = 12.0   # if daily max is below this, a jacket is a good idea
COLD_MIN_TEMP_C = 5.0      # if daily min is below this, dress warm
HEAT_MAX_TEMP_C = 32.0     # if daily max is above this, heat precautions apply
# Wind in km/h.
WINDY_KMH = 40.0

# WMO weather codes -> human-readable description.
WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# Codes that indicate any form of precipitation (rain/snow/drizzle/storm).
_PRECIP_CODES = {
    51, 53, 55, 56, 57,
    61, 63, 65, 66, 67,
    71, 73, 75, 77,
    80, 81, 82, 85, 86,
    95, 96, 99,
}


# --------------------------------------------------------------------------- #
# Low-level HTTP + unit helpers
# --------------------------------------------------------------------------- #
def _get_json(url: str, params: dict | None = None) -> dict:
    """GET a URL (with optional query params) and return parsed JSON."""
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Open-Meteo request failed: %s", exc)
        raise WeatherLookupError(
            "The weather API is unreachable right now. Please try again in a "
            f"moment. (request failed: {exc})"
        ) from exc


def _unit_query_params(units: str) -> dict:
    """Map a caller-friendly unit string to Open-Meteo query parameters."""
    units = (units or "celsius").strip().lower()
    if units in ("c", "celsius", "metric"):
        return {
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
    if units in ("f", "fahrenheit", "imperial"):
        return {
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
        }
    raise WeatherLookupError(
        f"units must be 'celsius' or 'fahrenheit', got {units!r}"
    )


def _describe_condition(code: Any) -> str:
    """Map a WMO weather code to a human-readable condition string."""
    try:
        return WMO_CODES.get(int(code), f"Weather code {code}")
    except (TypeError, ValueError):
        return f"Unknown condition ({code})"


def _is_precip_code(code: Any) -> bool:
    """True if a WMO code indicates any precipitation."""
    try:
        return int(code) in _PRECIP_CODES
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Location resolution
# --------------------------------------------------------------------------- #
def geocode(location: str) -> dict:
    """
    Resolve a place name to coordinates and metadata via the Open-Meteo
    Geocoding API.

    Args:
        location: A place name, e.g. "Chicago", "Austin", "Berlin", "Paris".

    Returns:
        A dict with name, admin1 (region/state), country, latitude, longitude,
        timezone, and population.

    Raises:
        WeatherLookupError: if the location can't be resolved or the API fails.
    """
    location = (location or "").strip()
    if not location:
        raise WeatherLookupError("A location is required, e.g. 'Chicago'.")

    cache_key = location.lower()
    cached = _GEOCODE_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    params = {
        "name": location,
        "count": 1,
        "language": "en",
        "format": "json",
    }
    data = _get_json(GEOCODE_URL, params)  # type: ignore[arg-type]
    results = data.get("results") or []
    if not results:
        raise WeatherLookupError(
            f"Could not resolve location {location!r}. Try a city name or "
            "town, e.g. 'Chicago' or 'Austin, Texas'."
        )

    first = results[0]
    resolved = {
        "name": first.get("name"),
        "admin1": first.get("admin1"),
        "country": first.get("country"),
        "latitude": first.get("latitude"),
        "longitude": first.get("longitude"),
        "timezone": first.get("timezone"),
        "population": first.get("population"),
        "query": location,
    }
    _GEOCODE_CACHE[cache_key] = (time.time() + GEOCODE_CACHE_TTL_SECONDS, resolved)
    return resolved


# --------------------------------------------------------------------------- #
# Forecast fetch + parsing
# --------------------------------------------------------------------------- #
def _fetch_forecast(
    latitude: float,
    longitude: float,
    forecast_days: int,
    units: str,
) -> dict:
    """Fetch daily forecast data for a coordinate, returning the raw JSON."""
    forecast_days = max(1, min(int(forecast_days), MAX_FORECAST_DAYS))
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "weather_code",
                "wind_speed_10m_max",
            ]
        ),
        "timezone": "auto",
        "forecast_days": forecast_days,
        **_unit_query_params(units),
    }
    return _get_json(FORECAST_URL, params)  # type: ignore[arg-type]


def _daily_entries(daily: dict) -> list[dict]:
    """Turn Open-Meteo's columnar daily payload into a list of day dicts."""
    times = daily.get("time") or []
    temps_max = daily.get("temperature_2m_max") or []
    temps_min = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_probability_max") or []
    codes = daily.get("weather_code") or []
    winds = daily.get("wind_speed_10m_max") or []

    entries = []
    for i, day in enumerate(times):
        code = codes[i] if i < len(codes) else None
        entries.append(
            {
                "date": day,
                "temp_max": temps_max[i] if i < len(temps_max) else None,
                "temp_min": temps_min[i] if i < len(temps_min) else None,
                "precip_chance_percent": precip[i] if i < len(precip) else None,
                "condition": _describe_condition(code),
                "condition_code": code,
                "has_precipitation": _is_precip_code(code),
                "wind_max": winds[i] if i < len(winds) else None,
            }
        )
    return entries


def _find_day(entries: list[dict], target: date) -> dict:
    """Find a daily entry whose date matches target, else raise."""
    iso = target.isoformat()
    for entry in entries:
        if entry["date"] == iso:
            return entry
    raise WeatherLookupError(
        f"No forecast available for {iso} (forecasts cover ~today through the "
        "next couple of weeks; try a date between today and "
        f"{(date.today() + timedelta(days=MAX_FORECAST_DAYS)).isoformat()})."
    )


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #
def resolve_date(date_arg: str, today: date | None = None) -> date:
    """
    Parse a flexible date argument into a concrete date.

    Accepts "today", "tomorrow", an ISO date ("2026-08-15"), or a day offset
    as an integer string ("1" = tomorrow). Raises WeatherLookupError otherwise.
    """
    today = today or date.today()
    raw = (date_arg or "today").strip().lower()

    if raw in ("today", "now"):
        return today
    if raw == "tomorrow":
        return today + timedelta(days=1)

    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass

    try:
        return today + timedelta(days=int(raw))
    except ValueError:
        raise WeatherLookupError(
            f"Invalid date {date_arg!r}. Use 'today', 'tomorrow', an ISO date "
            "like '2026-08-15', or a day offset like '1'."
        ) from None


def _target_offset(target: date) -> int:
    """Number of days from today until target (may be negative for the past)."""
    return (target - date.today()).days


# --------------------------------------------------------------------------- #
# Derived judgment (the "prediction" logic)
# --------------------------------------------------------------------------- #
def _temp_to_c(value: float | None, units: str) -> float | None:
    """Convert a temperature in the caller's unit to Celsius for thresholds."""
    if value is None:
        return None
    if units in ("f", "fahrenheit", "imperial"):
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def _wind_to_kmh(value: float | None, units: str) -> float | None:
    """Convert a wind speed in the caller's unit to km/h for thresholds."""
    if value is None:
        return None
    if units in ("f", "fahrenheit", "imperial"):
        return float(value) * 1.609344
    return float(value)


def _derive_umbrella(day: dict, units: str) -> tuple[str, str]:
    """
    Decide umbrella-needed based on the day's precipitation probability and
    weather code. Returns (verdict, reason) where verdict is yes/maybe/no.
    """
    precip = day.get("precip_chance_percent")
    condition = day.get("condition")
    precip_code = day.get("condition_code")

    if precip is not None and precip >= RAIN_YES_PERCENT:
        return (
            "yes",
            f"{precip}% precipitation chance and {condition} - definitely "
            "bring an umbrella.",
        )
    if precip is not None and precip >= RAIN_MAYBE_PERCENT:
        return (
            "maybe",
            f"{precip}% precipitation chance with {condition} - a small "
            "umbrella is a safe bet.",
        )
    if _is_precip_code(precip_code):
        return (
            "maybe",
            f"Precipitation ({condition}) is expected even though the "
            "probability is low - a compact umbrella helps.",
        )
    return (
        "no",
        f"{condition} with only {precip or 0}% precipitation chance - no "
        "umbrella needed.",
    )


def _derive_advice(day: dict, units: str) -> list[str]:
    """
    Build human-readable travel/outdoor advice from a single day's forecast.

    Thresholds (documented in the MCP tool docstring):
        - precipitation probability >= 40% -> umbrella
        - precipitation probability >= 70% -> heavy rain warning
        - daily max >= 32C -> heat precautions
        - daily min <= 5C  -> dress warm
        - daily max <= 12C -> jacket
        - wind >= 40 km/h  -> windy
    """
    advice: list[str] = []

    temp_max_c = _temp_to_c(day.get("temp_max"), units)
    temp_min_c = _temp_to_c(day.get("temp_min"), units)
    wind_kmh = _wind_to_kmh(day.get("wind_max"), units)
    precip = day.get("precip_chance_percent")

    if precip is not None and precip >= RAIN_YES_PERCENT:
        advice.append(f"heavy rain risk ({precip}% chance) - pack rain gear")
    elif precip is not None and precip >= RAIN_MAYBE_PERCENT:
        advice.append(f"{precip}% chance of rain - bring an umbrella")
    elif _is_precip_code(day.get("condition_code")):
        advice.append(f"{day.get('condition')} expected - a compact umbrella helps")

    if temp_max_c is not None and temp_max_c >= HEAT_MAX_TEMP_C:
        advice.append("hot day - stay hydrated, wear sunscreen and a hat")
    if temp_min_c is not None and temp_min_c <= COLD_MIN_TEMP_C:
        advice.append("cold - dress in warm layers and a coat")
    elif temp_max_c is not None and temp_max_c <= JACKET_MAX_TEMP_C:
        advice.append("cool - bring a jacket or light sweater")

    if wind_kmh is not None and wind_kmh >= WINDY_KMH:
        advice.append("windy - secure loose items and dress in layers")

    if not advice:
        advice.append("pleasant weather - no special gear needed")

    return advice


# --------------------------------------------------------------------------- #
# Public broker API (mirrors the MCP tools)
# --------------------------------------------------------------------------- #
def get_current_weather(location: str, units: str = "celsius") -> dict:
    """
    Get current conditions for a location.

    Args:
        location: A place name, e.g. "Chicago".
        units: "celsius" (default) or "fahrenheit".

    Returns:
        A dict with resolved location info, observation time, temperature,
        feels-like, condition, humidity, wind, wind gusts, pressure, and
        precipitation, plus the unit labels.

    Raises:
        WeatherLookupError: bad location or API failure.
    """
    resolved = geocode(location)
    params = {
        "latitude": resolved["latitude"],
        "longitude": resolved["longitude"],
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "weather_code",
                "wind_speed_10m",
                "wind_gusts_10m",
                "pressure_msl",
                "precipitation",
            ]
        ),
        "timezone": "auto",
        **_unit_query_params(units),
    }
    data = _get_json(FORECAST_URL, params)  # type: ignore[arg-type]
    current = data.get("current") or {}
    units_out = data.get("current_units") or {}

    return {
        "location": {
            "name": resolved["name"],
            "region": resolved.get("admin1"),
            "country": resolved.get("country"),
        },
        "as_of": current.get("time"),
        "timezone": data.get("timezone"),
        "units": {
            "temperature": units_out.get("temperature_2m", "°C"),
            "wind": units_out.get("wind_speed_10m", "km/h"),
            "pressure": units_out.get("pressure_msl", "hPa"),
            "precipitation": units_out.get("precipitation", "mm"),
        },
        "temperature": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "condition": _describe_condition(current.get("weather_code")),
        "condition_code": current.get("weather_code"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_gust": current.get("wind_gusts_10m"),
        "pressure_hpa": current.get("pressure_msl"),
        "precipitation": current.get("precipitation"),
    }


def get_forecast(location: str, days: int = 7, units: str = "celsius") -> dict:
    """
    Get a multi-day forecast for a location.

    Args:
        location: A place name, e.g. "Chicago".
        days: Number of days to forecast, 1-16 (default 7).
        units: "celsius" (default) or "fahrenheit".

    Returns:
        A dict with resolved location info, unit labels, and a list of daily
        entries (date, temp_max, temp_min, precip_chance_percent, condition,
        condition_code, has_precipitation, wind_max).

    Raises:
        WeatherLookupError: bad location or API failure.
    """
    resolved = geocode(location)
    data = _fetch_forecast(resolved["latitude"], resolved["longitude"], days, units)
    daily_units = data.get("daily_units") or {}

    return {
        "location": {
            "name": resolved["name"],
            "region": resolved.get("admin1"),
            "country": resolved.get("country"),
        },
        "timezone": data.get("timezone"),
        "units": {
            "temperature": daily_units.get("temperature_2m_max", "°C"),
            "wind": daily_units.get("wind_speed_10m_max", "km/h"),
            "precipitation_chance": daily_units.get("precipitation_probability_max", "%"),
        },
        "days": _daily_entries(data.get("daily") or {}),
    }


def get_historical_weather(location: str, date_arg: str, units: str = "celsius") -> dict:
    """
    Get observed (historical) weather for a specific past date.

    Args:
        location: A place name, e.g. "Chicago".
        date_arg: An ISO date in the past, e.g. "2026-07-20".
        units: "celsius" (default) or "fahrenheit".

    Returns:
        A dict with resolved location info, the requested date, unit labels,
        and observed temp_max, temp_min, precipitation (total), condition, and
        wind.

    Raises:
        WeatherLookupError: bad location, non-past date, or API failure.
    """
    resolved = geocode(location)
    target = resolve_date(date_arg)

    if target >= date.today():
        raise WeatherLookupError(
            f"Historical data is only available for past dates; {target.isoformat()} "
            "is today or in the future. Use get_forecast instead."
        )

    params = {
        "latitude": resolved["latitude"],
        "longitude": resolved["longitude"],
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "weather_code",
                "wind_speed_10m_max",
            ]
        ),
        "timezone": "auto",
        **_unit_query_params(units),
    }
    data = _get_json(ARCHIVE_URL, params)  # type: ignore[arg-type]
    daily = data.get("daily") or {}
    daily_units = data.get("daily_units") or {}

    codes = daily.get("weather_code") or []
    code = codes[0] if codes else None
    return {
        "location": {
            "name": resolved["name"],
            "region": resolved.get("admin1"),
            "country": resolved.get("country"),
        },
        "date": target.isoformat(),
        "units": {
            "temperature": daily_units.get("temperature_2m_max", "°C"),
            "precipitation": daily_units.get("precipitation_sum", "mm"),
            "wind": daily_units.get("wind_speed_10m_max", "km/h"),
        },
        "temp_max": (daily.get("temperature_2m_max") or [None])[0],
        "temp_min": (daily.get("temperature_2m_min") or [None])[0],
        "precipitation_total": (daily.get("precipitation_sum") or [None])[0],
        "condition": _describe_condition(code),
        "condition_code": code,
        "wind_max": (daily.get("wind_speed_10m_max") or [None])[0],
    }


def _single_day(location: str, date_arg: str, units: str) -> tuple[dict, dict, date]:
    """Resolve location + date and pull that single day's forecast entry."""
    resolved = geocode(location)
    target = resolve_date(date_arg)
    offset = _target_offset(target)
    if offset < 0:
        raise WeatherLookupError(
            f"{target.isoformat()} is in the past and cannot be forecast. Use "
            "get_historical_weather for past dates."
        )
    data = _fetch_forecast(
        resolved["latitude"],
        resolved["longitude"],
        offset + 1,
        units,
    )
    day = _find_day(_daily_entries(data.get("daily") or {}), target)
    return resolved, day, target


def predict_umbrella_needed(location: str, date_arg: str = "today") -> dict:
    """
    Decide whether an umbrella is needed on a given date.

    Judgment rule: precipitation probability >= 70% -> "yes",
    40-69% -> "maybe", below 40% -> "no" (also flags any precip weather code).

    Args:
        location: A place name, e.g. "Chicago".
        date_arg: "today" (default), "tomorrow", an ISO date like
            "2026-08-15", or a day offset like "1".

    Returns:
        A dict with the resolved location, the date evaluated, the day's
        precipitation probability and condition, and an umbrella_needed verdict
        of "yes"/"maybe"/"no" with a human-readable reason.

    Raises:
        WeatherLookupError: bad location, bad/in-past date, or API failure.
    """
    resolved, day, target = _single_day(location, date_arg, "celsius")
    verdict, reason = _derive_umbrella(day, "celsius")
    return {
        "location": {
            "name": resolved["name"],
            "region": resolved.get("admin1"),
            "country": resolved.get("country"),
        },
        "date": target.isoformat(),
        "precip_chance_percent": day.get("precip_chance_percent"),
        "condition": day.get("condition"),
        "temp_max_c": day.get("temp_max"),
        "temp_min_c": day.get("temp_min"),
        "umbrella_needed": verdict,
        "reason": reason,
    }


def get_travel_recommendation(
    location: str,
    date_arg: str = "today",
    units: str = "celsius",
) -> dict:
    """
    Derive a travel/outdoor recommendation for a location and date.

    This is a derived judgment call, not a passthrough of the raw API. It
    applies the following thresholds to the day's forecast:
        - precipitation probability >= 40%  -> bring an umbrella
        - precipitation probability >= 70%  -> heavy rain, pack rain gear
        - daily max temperature >= 32C      -> heat: hydrate + sunscreen
        - daily min temperature <= 5C       -> cold: warm layers + coat
        - daily max temperature <= 12C      -> cool: jacket or light sweater
        - wind >= 40 km/h                   -> windy: secure loose gear
    If none apply, it says conditions are pleasant.

    Args:
        location: A place name, e.g. "Chicago".
        date_arg: "today" (default), "tomorrow", an ISO date like
            "2026-08-15", or a day offset like "1".
        units: "celsius" (default) or "fahrenheit".

    Returns:
        A dict with the resolved location, the date evaluated, the day's
        forecast snapshot, a list of concrete advice strings, and an overall
        "recommendation" summary string.

    Raises:
        WeatherLookupError: bad location, bad/in-past date, or API failure.
    """
    resolved, day, target = _single_day(location, date_arg, units)
    advice = _derive_advice(day, units)
    umbrella, _ = _derive_umbrella(day, units)

    temp_unit = "°C" if units not in ("f", "fahrenheit", "imperial") else "°F"

    return {
        "location": {
            "name": resolved["name"],
            "region": resolved.get("admin1"),
            "country": resolved.get("country"),
        },
        "date": target.isoformat(),
        "day_forecast": {
            "temp_max": day.get("temp_max"),
            "temp_min": day.get("temp_min"),
            "temperature_unit": temp_unit,
            "precip_chance_percent": day.get("precip_chance_percent"),
            "condition": day.get("condition"),
            "wind_max": day.get("wind_max"),
        },
        "umbrella_needed": umbrella,
        "advice": advice,
        "recommendation": " ".join(
            f"- {item}" for item in advice
        ).strip(),
    }


def compare_weather(
    locations: list[str],
    date_arg: str = "today",
    units: str = "celsius",
) -> dict:
    """
    Compare the weather outlook for several locations on a given date.

    Resolves each location independently, so one bad location does not fail the
    whole call - it becomes an error entry for that location.

    Args:
        locations: A list of place names, e.g. ["Chicago", "Austin", "Denver"].
        date_arg: "today" (default), "tomorrow", an ISO date, or a day offset.
        units: "celsius" (default) or "fahrenheit".

    Returns:
        A dict with the date evaluated and a list of per-location entries, each
        with the resolved location, that day's temp max/min, precipitation
        chance, condition, wind, and a short recommendation (or an error string
        for locations that could not be resolved).

    Raises:
        WeatherLookupError: empty/invalid locations list or API failure for
            every location.
    """
    if not locations:
        raise WeatherLookupError("Provide at least one location to compare.")

    target = resolve_date(date_arg)
    results: list[dict] = []
    for location in locations:
        try:
            resolved, day, _ = _single_day(location, target.isoformat(), units)
            advice = _derive_advice(day, units)
            results.append(
                {
                    "location": {
                        "name": resolved["name"],
                        "region": resolved.get("admin1"),
                        "country": resolved.get("country"),
                    },
                    "temp_max": day.get("temp_max"),
                    "temp_min": day.get("temp_min"),
                    "precip_chance_percent": day.get("precip_chance_percent"),
                    "condition": day.get("condition"),
                    "wind_max": day.get("wind_max"),
                    "recommendation": " ".join(f"- {a}" for a in advice),
                }
            )
        except WeatherLookupError as exc:
            logger.info("compare_weather: skipping %r: %s", location, exc)
            results.append({"location": location, "error": str(exc)})

    return {
        "date": target.isoformat(),
        "count": len(results),
        "results": results,
    }
