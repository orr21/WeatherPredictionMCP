"""
Weather-prediction MCP server.

Exposes weather tools over MCP (Model Context Protocol) so a Databricks Agent
Bricks agent can call them like any other tool:
    - get_current_weather(location)
    - get_forecast(location, days)
    - predict_umbrella_needed(location, date)
    - get_travel_recommendation(location, date)
    - get_historical_weather(location, date)   (stretch)
    - compare_weather(locations, date)         (stretch)

These tools are backed by Open-Meteo (https://open-meteo.com/) - a free
weather API that requires NO API key and no signup - via the broker adapter in
weather_broker.py. There are no secrets to configure: the same code runs
locally and on Databricks Apps unchanged. All HTTP calls and JSON parsing live
in weather_broker.py; the tool functions below are thin wrappers that add
error handling so a bad location or API outage returns a clean {"error": ...}
dict instead of a stack trace, and the agent can react sensibly.

The "prediction" tools (predict_umbrella_needed, get_travel_recommendation)
do not echo the raw API - they apply documented thresholds (e.g. "bring an
umbrella when precipitation probability >= 40%") to turn raw forecast data
into a derived judgment call.

Swap-in note: to switch to another weather provider (e.g. the National Weather
Service API for US-only alerts), keep the same tool signatures and replace the
weather_broker.* calls inside each tool - the MCP surface for the agent does
not change.

Deploy this as its own Databricks App (same app.yaml + FastMCP entrypoint
pattern documented at
https://docs.databricks.com/aws/en/agents/mcp-tools/custom-mcp), separate from
the weather_dashboard app, so an Agent Bricks agent (or any MCP client) can
register its URL as an external MCP server.

Run locally:
    python weather_mcp_server.py
"""

import logging
import os

import weather_broker
from fastmcp import FastMCP
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp-server")

mcp = FastMCP("weather-prediction")


@mcp.custom_route("/", methods=["GET"])
async def index(request):
    """Root endpoint: handy for sanity-checking the app is alive."""
    return JSONResponse({"name": "weather-prediction-mcp", "status": "ok"})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):
    """Health check endpoint for the Databricks App / load balancers."""
    return JSONResponse({"status": "ok"})


@mcp.tool
def get_current_weather(location: str, units: str = "celsius") -> dict:
    """
    Get the current weather conditions for a location (city name or town).

    Args:
        location: A place name, e.g. "Chicago", "Austin", "Berlin", "Paris".
        units: Temperature/wind unit preference - "celsius" (default) or
            "fahrenheit".

    Returns:
        A dict with the resolved location, observation time, temperature,
        feels-like temperature, condition, humidity, wind speed and gusts,
        pressure, and precipitation, plus the unit labels used. On a bad
        location or API outage, returns an {"error": ...} dict instead.
    """
    try:
        return weather_broker.get_current_weather(location, units)
    except weather_broker.WeatherLookupError as exc:
        logger.warning("get_current_weather(%r) failed: %s", location, exc)
        return {"error": str(exc), "location": location}


@mcp.tool
def get_forecast(location: str, days: int = 7, units: str = "celsius") -> dict:
    """
    Get a multi-day weather forecast for a location.

    Args:
        location: A place name, e.g. "Chicago".
        days: Number of days to forecast, between 1 and 16 (default 7).
        units: Temperature/wind unit preference - "celsius" (default) or
            "fahrenheit".

    Returns:
        A dict with the resolved location, unit labels, and a list of daily
        entries (date, temp_max, temp_min, precip_chance_percent, condition,
        wind_max). On a bad location or API outage, returns an {"error": ...}
        dict instead.
    """
    try:
        return weather_broker.get_forecast(location, days, units)
    except weather_broker.WeatherLookupError as exc:
        logger.warning("get_forecast(%r) failed: %s", location, exc)
        return {"error": str(exc), "location": location}


@mcp.tool
def predict_umbrella_needed(location: str, date: str = "today") -> dict:
    """
    Decide whether an umbrella is needed for a location on a given date.

    This is a derived judgment call built from the forecast, not a passthrough
    of the raw API. Rule: if the day's max precipitation probability is >= 70%
    the answer is "yes"; 40-69% is "maybe"; below 40% is "no" (with any
    precipitation weather code also flagging "maybe").

    Args:
        location: A place name, e.g. "Chicago".
        date: Which day to evaluate - "today" (default), "tomorrow", an ISO
            date like "2026-08-15", or a day offset like "1".

    Returns:
        A dict with the resolved location, the date evaluated, the day's
        precipitation probability and condition, an umbrella_needed verdict of
        "yes"/"maybe"/"no", and a human-readable reason. On a bad location,
        out-of-range date, or API outage, returns an {"error": ...} dict.
    """
    try:
        return weather_broker.predict_umbrella_needed(location, date)
    except weather_broker.WeatherLookupError as exc:
        logger.warning("predict_umbrella_needed(%r) failed: %s", location, exc)
        return {"error": str(exc), "location": location}


@mcp.tool
def get_travel_recommendation(
    location: str,
    date: str = "today",
    units: str = "celsius",
) -> dict:
    """
    Derive a travel/outdoor recommendation for a location and date.

    Applies thresholds to the day's forecast to produce concrete advice:
        - precipitation probability >= 40%  -> bring an umbrella
        - precipitation probability >= 70%  -> heavy rain, pack rain gear
        - daily max temperature >= 32C      -> heat: hydrate + sunscreen
        - daily min temperature <= 5C       -> cold: warm layers + coat
        - daily max temperature <= 12C      -> cool: jacket or light sweater
        - wind >= 40 km/h                   -> windy: secure loose gear
    If none apply it reports the weather is pleasant. Thresholds are always
    evaluated in metric internally and reported in your chosen unit.

    Args:
        location: A place name, e.g. "Chicago".
        date: Which day to evaluate - "today" (default), "tomorrow", an ISO
            date like "2026-08-15", or a day offset like "1".
        units: Temperature/wind unit preference - "celsius" (default) or
            "fahrenheit".

    Returns:
        A dict with the resolved location, the date evaluated, the day's
        forecast snapshot, an umbrella_needed verdict, a list of concrete
        advice strings, and an overall recommendation summary. On a bad
        location, out-of-range date, or API outage, returns an {"error": ...}
        dict.
    """
    try:
        return weather_broker.get_travel_recommendation(location, date, units)
    except weather_broker.WeatherLookupError as exc:
        logger.warning(
            "get_travel_recommendation(%r) failed: %s", location, exc
        )
        return {"error": str(exc), "location": location}


@mcp.tool
def get_historical_weather(
    location: str,
    date: str,
    units: str = "celsius",
) -> dict:
    """
    Get observed (historical) weather for a specific past date.

    Args:
        location: A place name, e.g. "Chicago".
        date: An ISO date in the past, e.g. "2026-07-20".
        units: Temperature/wind unit preference - "celsius" (default) or
            "fahrenheit".

    Returns:
        A dict with the resolved location, the requested date, observed
        temp_max, temp_min, total precipitation, condition, and wind. On a bad
        location, non-past date, or API outage, returns an {"error": ...}
        dict.
    """
    try:
        return weather_broker.get_historical_weather(location, date, units)
    except weather_broker.WeatherLookupError as exc:
        logger.warning("get_historical_weather(%r) failed: %s", location, exc)
        return {"error": str(exc), "location": location}


@mcp.tool
def compare_weather(
    locations: list[str],
    date: str = "today",
    units: str = "celsius",
) -> dict:
    """
    Compare the weather outlook for several locations on a given date.

    Args:
        locations: A list of place names, e.g. ["Chicago", "Austin", "Denver"].
        date: Which day to evaluate - "today" (default), "tomorrow", an ISO
            date like "2026-08-15", or a day offset like "1".
        units: Temperature/wind unit preference - "celsius" (default) or
            "fahrenheit".

    Returns:
        A dict with the date evaluated and a list of per-location entries
        (temp max/min, precipitation chance, condition, wind, and a short
        recommendation). Each location is resolved independently, so a bad
        location appears as an {"error": ...} entry rather than failing the
        whole call. Returns an {"error": ...} dict if no location resolves.
    """
    try:
        return weather_broker.compare_weather(locations, date, units)
    except weather_broker.WeatherLookupError as exc:
        logger.warning("compare_weather failed: %s", exc)
        return {"error": str(exc), "locations": locations}


if __name__ == "__main__":
    # Databricks Apps route external HTTP traffic to this port via app.yaml;
    # streamable-http is the transport Databricks' MCP client/gateway expects
    # (see the "Host your own MCP" doc linked in the module docstring above).
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    mcp.run(transport="http", host="0.0.0.0", port=port)
