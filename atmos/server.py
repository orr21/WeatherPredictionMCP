"""FastMCP tool surface for the atmos service.

Thin @app.tool functions that delegate to the data and logic layers - the
tools never talk HTTP or parse JSON. The server is served over streamable
HTTP, the transport Databricks' MCP gateway expects when a custom MCP server
is hosted as a Databricks App.

Exposed tools:
  required
    - get_current_weather(location)          live conditions
    - get_forecast(location, days)           multi-day forecast
    - predict_umbrella_needed(location, when) umbrella decision (explained)
  optional
    - get_travel_recommendation(location, when) travel judgment + packing list
    - compare_cities(locations)              cross-city comparison
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from . import config, logic
from . import datasource as source

app = FastMCP("atmos")


@app.tool
def get_current_weather(location: str) -> dict:
    """Live conditions for a place.

    Args:
        location: A city name ("Madrid", "Austin, TX", "Tokyo") or a raw
            "lat,lon" pair ("40.41,-3.70").

    Returns:
        Dict with location, observed_at, temperature, feels_like, humidity,
        precipitation, wind_speed, wind_direction, conditions, is_day and
        units. On failure returns {"error": "..."} so callers can react.
    """
    try:
        return source.current_conditions(location)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}


@app.tool
def get_forecast(location: str, days: int = config.DEFAULT_HORIZON_DAYS) -> dict:
    """Multi-day daily forecast for a place.

    Args:
        location: City name or "lat,lon".
        days: Number of days to forecast, 1-16 (default 3). Values outside the
            range are clamped.

    Returns:
        Dict with location, days, units and an `outlook` list; each entry has
        date, high, low, precipitation_probability (%), precipitation_mm,
        wind_speed_max and conditions (text). On failure returns {"error": "..."}.
    """
    try:
        return source.daily_outlook(location, horizon=days)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}


@app.tool
def predict_umbrella_needed(location: str, when: str = "today") -> dict:
    """Decide whether an umbrella is needed for a specific day.

    Applies the rain rule from atmos.config (chance >= 40% or amount >= 1.0 mm)
    to the target day and returns the boolean plus an explained reason, so the
    agent (and the user) can see why.

    Args:
        location: City name or "lat,lon".
        when: "today", "tomorrow", or an ISO date "YYYY-MM-DD".

    Returns:
        Dict with location, date, umbrella_needed, precipitation_probability,
        precipitation_mm, conditions and a human-readable `reason`. On failure
        returns {"error": "..."}.
    """
    try:
        outlook = source.daily_outlook(location, horizon=config.MAX_HORIZON_DAYS)
        index = source.day_for(when, outlook["outlook"])
        day = outlook["outlook"][index]
        prob = day.get("precipitation_probability") or 0
        mm = day.get("precipitation_mm") or 0.0
        needed, reason = logic.umbrella_verdict(prob, mm)
        return {
            "location": outlook["location"],
            "date": day["date"],
            "umbrella_needed": needed,
            "precipitation_probability": prob,
            "precipitation_mm": mm,
            "conditions": day["conditions"],
            "reason": reason,
        }
    except (ValueError, RuntimeError, IndexError) as exc:
        return {"error": str(exc)}


@app.tool
def get_travel_recommendation(location: str, when: str = "today") -> dict:
    """Plain-language travel advice for a day, derived from explicit thresholds.

    Combines precipitation, temperature and wind into a headline plus a short
    packing list (not a passthrough of the raw API).

    Args:
        location: City name or "lat,lon".
        when: "today", "tomorrow", or "YYYY-MM-DD".

    Returns:
        Dict with location, date, recommendation, `factors` (the numbers used)
        and `bring` (a short packing list). On failure returns {"error": "..."}.
    """
    try:
        outlook = source.daily_outlook(location, horizon=config.MAX_HORIZON_DAYS)
        index = source.day_for(when, outlook["outlook"])
        day = outlook["outlook"][index]
        prob = day.get("precipitation_probability") or 0
        mm = day.get("precipitation_mm") or 0.0
        headline, bring = logic.travel_verdict(
            prob, mm, day.get("high"), day.get("low"), day.get("wind_speed_max")
        )
        return {
            "location": outlook["location"],
            "date": day["date"],
            "recommendation": headline,
            "factors": {
                "high": day.get("high"),
                "low": day.get("low"),
                "precipitation_probability": prob,
                "precipitation_mm": mm,
                "wind_speed_max": day.get("wind_speed_max"),
                "conditions": day["conditions"],
            },
            "bring": bring or ["nothing special"],
        }
    except (ValueError, RuntimeError, IndexError) as exc:
        return {"error": str(exc)}


@app.tool
def compare_cities(locations: list[str]) -> dict:
    """Compare live conditions across several places and pick the warmest and driest.

    Args:
        locations: 2-8 city names or "lat,lon" pairs.

    Returns:
        Dict with a `cities` list (each: location, temperature, conditions,
        precipitation) plus `warmest` and `driest` picks. Places that fail to
        resolve appear in `errors` instead of aborting the whole call.
    """
    cities, errors = [], []
    for place in locations or []:
        try:
            now = source.current_conditions(place)
            cities.append({
                "location": now["location"],
                "temperature": now["temperature"],
                "conditions": now["conditions"],
                "precipitation": now["precipitation"],
            })
        except (ValueError, RuntimeError) as exc:
            errors.append({"location": place, "error": str(exc)})

    result = {"cities": cities, "errors": errors}
    if cities:
        result["warmest"] = max(
            cities, key=lambda c: c["temperature"] if c["temperature"] is not None else -999
        )["location"]
        result["driest"] = min(
            cities, key=lambda c: c["precipitation"] if c["precipitation"] is not None else 1e9
        )["location"]
    return result


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the server over streamable HTTP.

    Port resolution: explicit arg first, then DATABRICKS_APP_PORT / MCP_PORT
    environment variables (Databricks injects these), then the 8000 default.
    """
    selected = int(
        os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("MCP_PORT") or str(port)
    )
    app.run(transport="http", host=host, port=selected)


if __name__ == "__main__":
    serve()
