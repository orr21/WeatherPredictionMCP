"""
Weather dashboard: a small Flask app that mirrors the tools the Agent Bricks
agent calls through the weather-prediction MCP server (weather_mcp_server.py).
It never talks to the MCP server - it reuses the same weather_broker.py adapter
directly, matching the Day 3 pattern where the MCP server and dashboard each
carry their own copy of the shared broker module. Every recommendation shown
here is produced by the exact same threshold logic the agent's tools use, so
you can sanity-check the agent's answers side by side with this UI.

Deploy this as its OWN Databricks App (separate from weather_mcp_server.py) -
one app serves MCP tool calls, the other serves the human-facing UI.

Run locally:
    python app.py
"""

import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request

import weather_broker

app = Flask(__name__)

DEFAULT_CITY = os.environ.get("WEATHER_DASHBOARD_CITY", "Chicago")

# Recent lookups surfaced on the dashboard (in-memory; resets on restart).
RECENT = []


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page)."""
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Dashboard UI: current weather, forecast, and recommendation."""
    return render_template("index.html", default_city=DEFAULT_CITY)


@app.route("/api/current")
def api_current():
    """Current conditions for a location (mirrors get_current_weather)."""
    location = request.args.get("location", DEFAULT_CITY)
    units = request.args.get("units", "celsius")
    try:
        data = weather_broker.get_current_weather(location, units)
    except weather_broker.WeatherLookupError as exc:
        return jsonify({"error": str(exc)}), 400
    _record(location, "current")
    return jsonify(data)


@app.route("/api/forecast")
def api_forecast():
    """Multi-day forecast for a location (mirrors get_forecast)."""
    location = request.args.get("location", DEFAULT_CITY)
    units = request.args.get("units", "celsius")
    try:
        days = int(request.args.get("days", 7))
    except ValueError:
        return jsonify({"error": "days must be an integer"}), 400
    try:
        data = weather_broker.get_forecast(location, days, units)
    except weather_broker.WeatherLookupError as exc:
        return jsonify({"error": str(exc)}), 400
    _record(location, "forecast")
    return jsonify(data)


@app.route("/api/recommendation")
def api_recommendation():
    """Travel/outdoor recommendation for a location + date (mirrors
    get_travel_recommendation)."""
    location = request.args.get("location", DEFAULT_CITY)
    units = request.args.get("units", "celsius")
    date_arg = request.args.get("date", "today")
    try:
        data = weather_broker.get_travel_recommendation(location, date_arg, units)
    except weather_broker.WeatherLookupError as exc:
        return jsonify({"error": str(exc)}), 400
    _record(location, "recommendation")
    return jsonify(data)


@app.route("/api/umbrella")
def api_umbrella():
    """Umbrella verdict for a location + date (mirrors predict_umbrella_needed)."""
    location = request.args.get("location", DEFAULT_CITY)
    date_arg = request.args.get("date", "today")
    try:
        data = weather_broker.predict_umbrella_needed(location, date_arg)
    except weather_broker.WeatherLookupError as exc:
        return jsonify({"error": str(exc)}), 400
    _record(location, "umbrella")
    return jsonify(data)


@app.route("/api/recent")
def api_recent():
    """Recent lookups made through this dashboard (newest first)."""
    return jsonify(RECENT)


def _record(location: str, kind: str):
    """Append a recent-lookup entry (cap the list)."""
    RECENT.append(
        {
            "when": datetime.utcnow().isoformat() + "Z",
            "location": location,
            "kind": kind,
        }
    )
    del RECENT[:-20]


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", 8001))
    app.run(debug=True, host=host, port=port)
