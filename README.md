# atmos — keyless weather MCP server with agent-ready reasoning

`atmos` is a small [MCP](https://modelcontextprotocol.io) server that answers
everyday weather questions — *what's it like now*, *how's the week looking*,
*do I need an umbrella*, *is it a good day to travel* — using the free,
**keyless** [Open-Meteo](https://open-meteo.com) API. It is built to be hosted
as a Databricks App and attached to an Agent Bricks agent as an external MCP.

## Why keyless matters

Open-Meteo needs no signup and no API key (~10k calls/day for non-commercial
use), which means:

- **zero secrets** to create, rotate, or leak;
- geocoding + live conditions + multi-day forecast in a single API;
- global coverage (not US-only).

The whole stack can be run and tested locally with no credentials at all.

## Layout

```
atmos/
  server.py        FastMCP tool surface (thin wrappers)
  datasource.py    Open-Meteo adapter: all HTTP + JSON parsing
  logic.py         transparent decision rules (umbrella / travel)
  config.py        tunable thresholds — single source of truth
  helpers.py       small shared utilities
prompts/
  assistant_system.md   agent system prompt for Agent Bricks
app.yaml                Databricks App deployment config (at repo root)
tests/
  test_smoke.py         live-API sanity checks (no MCP needed)
examples/
  quick_check.py        minimal CLI demo of the data layer
```

**Design rule:** the MCP tools never talk HTTP and never parse JSON. They call
`datasource` for data and `logic` for judgments, so each layer stays tiny and
testable.

## Tools

| tool | purpose |
|---|---|
| `get_current_weather(location)` | live temperature, feels-like, humidity, wind, precipitation, conditions |
| `get_forecast(location, days)` | 1–16 day forecast: high/low, rain chance + amount, wind, conditions |
| `predict_umbrella_needed(location, when)` | umbrella yes/no **with an explained reason** |
| `get_travel_recommendation(location, when)` | travel headline + packing list |
| `compare_cities(locations)` | warmest/driest pick across cities |

`location` takes a place name (`"Madrid"`, `"Austin, TX"`, `"Tokyo"`) or a raw
`"lat,lon"`. `when` takes `"today"`, `"tomorrow"`, or `"YYYY-MM-DD"`.

### The judgment tools do real reasoning

`predict_umbrella_needed` and `get_travel_recommendation` don't pass through
the raw forecast — they apply explicit, auditable rules (see `atmos/config.py`
and `atmos/logic.py`):

> An umbrella is needed when rain chance ≥ **40%** OR expected amount ≥ **1.0 mm**.

The probability catches "maybe rain" days; the millimetre floor catches days
where the chance looks modest but meaningful rain is still coming. Every verdict
comes with a `reason` string so the agent can explain *why*. `trip_advice`
layers on temperature and wind (hot ≥ 35 °C, freezing ≤ 0 °C, windy ≥ 40 km/h,
wet ≥ 60% / 10 mm) for its headline and packing list.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) Data layer demo (hits Open-Meteo directly)
python -m examples.quick_check

# 2) Test harness
python -m tests.test_smoke

# 3) Start the MCP server over streamable HTTP
python -m atmos.server          # http://localhost:8000/mcp
```

## Deploying as a Databricks App

1. Push this folder to a Git repo and add it as a Git folder in Databricks.
2. **Compute → Apps → Create app → Custom**, name it starting with **`mcp-`**
   (e.g. `mcp-atmos`), and point it at this folder (it ships `app.yaml` at the
   root).
3. Databricks listens on port 8000 by default and exposes the endpoint at
   `https://<app-url>/mcp`. No secret configuration is needed.

## Registering with an agent

**AI Gateway → MCPs → Add MCP**, paste the app's `/mcp` URL (streamable HTTP),
name it `atmos-tools`, and save — Databricks introspects the 5 tools. Then in
**Agents → Agent Bricks → Create agent**, attach the `atmos-tools` MCP server
and paste the system prompt from `prompts/assistant_system.md`.

## Demo questions

Ask these to the deployed agent (tool-calling + final answer make good
screenshots):

**1. "Is it raining in Seattle — should I grab an umbrella?"**
→ `predict_umbrella_needed("Seattle", "today")` → answer yes/no plus the reason.

**2. "What's the outlook for Austin, and is tomorrow a good day to travel?"**
→ `get_forecast("Austin, TX", 3)`, then `get_travel_recommendation("Austin, TX", "tomorrow")`.

**3. "Which is warmer right now, Chicago, Miami, or Denver?"**
→ `compare_cities(["Chicago", "Miami", "Denver"])` → warmest/driest + conditions.

## Notes

- Forecasts are model output; the judgment tools use simple, transparent
  thresholds rather than a trained model — the value is explainable reasoning,
  tuned in exactly one place (`atmos/config.py`).
- Units are °C and km/h (Open-Meteo defaults); the datasource can pass
  `temperature_unit` / `wind_speed_unit` if you want °F or mph.
- Ideas: severe-weather alerts via the NWS `/alerts` API, historical lookups
  via the Open-Meteo archive API, or a small dashboard that logs agent queries.
