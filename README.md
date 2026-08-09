# Weather-Prediction MCP Server + Agent (Homework)

A from-scratch weather assistant built on the **Day 3 pattern** (Agent Bricks +
an MCP server deployed as Databricks Apps), but with a completely different
domain: instead of Alpaca paper-trading, this repo ships a **weather-prediction
MCP server** backed by **Open-Meteo** (free, no API key), a **Databricks Agent
Bricks agent** that answers natural-language weather questions through it, and
an optional **dashboard** that mirrors the agent's tools.

> The original Day 3 Alpaca repo is preserved as
> [`REFERENCE_DAY3_ALPACA.md`](REFERENCE_DAY3_ALPACA.md) and the Alpaca app
> code is still in `mcp_server/` + `dashboard/` for comparison. Everything new
> for this submission lives in `weather_mcp_server/` and `weather_dashboard/`.

## Architecture

```
Agent Bricks agent  --(MCP tool calls)-->  weather_mcp_server/weather_mcp_server.py  --(REST)-->  Open-Meteo
        ^                                      |   FastMCP, streamable HTTP                    + Geocoding API
        | (system prompt: see agent_system_prompt.md)                                          (no key, no signup)
        |
        +--------------------- weather_dashboard/app.py <-- (reuses weather_broker.py) -------------+
```

- `weather_mcp_server/` and `weather_dashboard/` are **two separate Databricks
  Apps** - one serves MCP tool calls to the agent, the other serves a
  human-facing UI. Both use their own copy of `weather_broker.py` (each
  Databricks App deploys from its own folder, so shared code is duplicated,
  exactly like Day 3's `alpaca_broker.py`).
- `weather_broker.py` is the **broker adapter**: it owns every HTTP call and
  all JSON parsing against Open-Meteo. The `@mcp.tool` functions in
  `weather_mcp_server.py` are thin wrappers that just add error handling.
- **No secrets, no API key.** Open-Meteo is key-less, so there is nothing to
  store or configure - the same code runs locally and on Databricks Apps
  unchanged. (If you later switch to a keyed provider like WeatherAPI.com,
  follow the Day 3 `_secret()` / `WorkspaceClient().secrets.get_secret()`
  pattern and never commit the key.)

## Files

- `weather_mcp_server/weather_broker.py` - broker/adapter: geocoding, current
  conditions, forecasts, historical data, and the recommendation thresholds
- `weather_mcp_server/weather_mcp_server.py` - FastMCP server (streamable
  HTTP) exposing the 6 tools below
- `weather_mcp_server/app.yaml` / `requirements.txt` - Databricks App config
- `weather_mcp_server/agent_system_prompt.md` - **agent config**: tool list,
  system prompt to paste into Agent Bricks, and guardrails
- `weather_dashboard/app.py` + `templates/index.html` - Flask dashboard
  (stretch): current weather, 7-day forecast, umbrella verdict, travel
  recommendation, recent lookups
- `weather_dashboard/weather_broker.py` - copy of the same broker adapter
- `weather_dashboard/app.yaml` / `requirements.txt` - dashboard Databricks App
  config

## Tools

All tools return JSON dicts. On a bad location, out-of-range date, or API
outage they return `{"error": "...", ...}` instead of raising, so the agent
can relay a sensible message to the user.

| Tool | Description |
| ---- | ----------- |
| `get_current_weather(location, units="celsius")` | Current temp, feels-like, condition, humidity, wind, gusts, pressure |
| `get_forecast(location, days=7, units="celsius")` | Multi-day forecast: high/low, precipitation chance, condition, wind (1-16 days) |
| `predict_umbrella_needed(location, date="today")` | Derived verdict **yes/maybe/no** + reason |
| `get_travel_recommendation(location, date="today", units="celsius")` | Derived travel advice (jacket / heat / wind / rain) |
| `get_historical_weather(location, date, units="celsius")` | Observed weather for a past date *(stretch)* |
| `compare_weather(locations, date="today", units="celsius")` | Side-by-side outlook for several cities *(stretch)* |

**Prediction logic** (implemented in `weather_broker.py`, documented in each
tool's docstring - this is where reasoning happens, not a passthrough):

- Umbrella: precipitation probability **>= 70%** -> `yes`; **40-69%** ->
  `maybe`; `< 40%` -> `no` (any precip weather code also flags `maybe`).
- Travel advice: rain >= 40% umbrella, >= 70% heavy rain; max temp >= 32C
  heat; min temp <= 5C cold; max temp <= 12C jacket; wind >= 40 km/h windy.
- Dates are flexible: `today`, `tomorrow`, ISO `YYYY-MM-DD`, or a day offset.

## Setup

### 1. Run the MCP server locally

```bash
cd weather_mcp_server
pip install -r requirements.txt
python weather_mcp_server.py        # serves MCP on :8000 (set PORT to change)
```

Sanity-check with an MCP Inspector or `curl`:

```bash
curl -s http://localhost:8000/healthz            # {"status":"ok"}
curl -s http://localhost:8000/mcp                 # MCP streamable-http endpoint
```

### 2. Run the dashboard locally (optional)

```bash
cd weather_dashboard
pip install -r requirements.txt
python app.py                       # serves UI on :8001
```

Open `http://localhost:8001`, type a city, and you'll see the same data and
recommendations the agent produces.

### 3. Deploy both apps to Databricks Apps

Follow the same Git-folder + Apps UI flow as Day 3 (no CLI required):

1. Create a Git folder for this repo in your workspace.
2. **Deploy the MCP server app**: Apps > Create app > Custom, name it e.g.
   `weather-prediction-mcp`, point it at the repo's
   `weather_mcp_server/` subfolder (picks up `app.yaml`). Deploy, then copy
   its app URL.
3. **Deploy the dashboard app**: repeat, name it e.g. `weather-dashboard`,
   pointing at `weather_dashboard/`.

### 4. Register the MCP server as an external MCP

1. Workspace > **AI Gateway** > **MCPs** > **Add MCP**.
2. Paste the `weather-prediction-mcp` app URL as the server endpoint
   (streamable HTTP). Databricks introspects it and lists the 6 tools.
3. Name it (e.g. `weather-prediction`) and grant your agent access via Unity
   Catalog permissions if prompted.

### 5. Build the Agent Bricks agent

1. Workspace > **Agents** > **Agent Bricks** > **Create agent** (Custom LLM).
2. Under **Tools**, add the `weather-prediction` MCP server (all 6 tools).
3. Paste the system prompt from
   [`weather_mcp_server/agent_system_prompt.md`](weather_mcp_server/agent_system_prompt.md)
   (Section 2). It tells the agent which tool to call in which order, to
   resolve fuzzy dates to ISO before calling, and the guardrails that stop it
   from hallucinating weather (never report a number that didn't come from a
   tool; relay tool errors verbatim).
4. Evaluate + iterate with Agent Bricks' auto-eval sample prompts, then deploy.

## Demonstration

Verified against the live Open-Meteo API on 2026-08-09 (dates below are the
real forecast dates). The MCP tool calls and their JSON responses are exactly
what the agent sees; the "Answer" is what the agent writes from them.

### Q1: "Will it rain in Chicago tomorrow?"

> tool call: `predict_umbrella_needed(location="Chicago", date="tomorrow")`
>
> ```json
> {"location": {"name": "Chicago", "region": "Illinois", "country": "United States"},
>  "date": "2026-08-10", "precip_chance_percent": 38,
>  "condition": "Slight rain showers",
>  "umbrella_needed": "maybe",
>  "reason": "Precipitation (Slight rain showers) is expected even though the probability is low - a compact umbrella helps."}
> ```

**Answer:** "Light showers are possible in Chicago tomorrow (38% chance), so
there's a small chance of rain - a compact umbrella is a safe bet, but it won't
be a washout."

### Q2: "Should I bring a jacket to Austin this weekend?"

> tool call: `get_travel_recommendation(location="Austin", date="2026-08-15")`
>
> ```json
> {"location": {"name": "Austin", "region": "Texas", "country": "United States"},
>  "date": "2026-08-15",
>  "day_forecast": {"temp_max": 40.2, "temp_min": 26.2, "temperature_unit": "°C",
>                   "precip_chance_percent": 1, "condition": "Overcast", "wind_max": 25.2},
>  "umbrella_needed": "no",
>  "advice": ["hot day - stay hydrated, wear sunscreen and a hat"]}
> ```

**Answer:** "No jacket needed for Austin this weekend - Saturday hits 40°C with
a low of 26°C and essentially no rain. Skip the jacket, pack water and
sunscreen, and stay hydrated."

### Q3: "Which city is better for a picnic next Friday: Paris or London?"

> tool call: `compare_weather(locations=["Paris", "London"], date="2026-08-14")`
>
> ```json
> {"date": "2026-08-14", "count": 2, "results": [
>   {"location": {"name": "Paris", "region": "Île-de-France Region", "country": "France"},
>    "temp_max": 34.5, "temp_min": 22.0, "precip_chance_percent": 12,
>    "condition": "Overcast", "wind_max": 15.8,
>    "recommendation": "- hot day - stay hydrated, wear sunscreen and a hat"},
>   {"location": {"name": "London", "region": "England", "country": "United Kingdom"},
>    "temp_max": 30.1, "temp_min": 22.2, "precip_chance_percent": 21,
>    "condition": "Mainly clear", "wind_max": 13.7,
>    "recommendation": "- pleasant weather - no special gear needed"}]}
> ```

**Answer:** "London wins for a picnic: mainly clear skies, 21% rain chance, and
a comfortable 30°C high - just pleasant. Paris will be a hot 34.5°C with
overcast skies, so bring shade and water if you go there."

### Q4 (error handling): "What's the weather in Blahville?"

> tool call: `get_current_weather(location="Blahville")`
>
> ```json
> {"error": "Could not resolve location 'Blahville'. Try a city name or town, e.g. 'Chicago' or 'Austin, Texas'.",
>  "location": "Blahville"}
> ```

**Answer:** "I couldn't find a place called 'Blahville' - could you give me a
city name or town instead?"

### Paste your Databricks App URLs here

- Weather MCP server app: `https://<your-workspace>.cloud.databricks.com/apps/weather-prediction-mcp`
- Weather dashboard app: `https://<your-workspace>.cloud.databricks.com/apps/weather-dashboard`
- (Paste screenshots of the Agent Bricks chat with the tool-call trace if you
  can't share workspace access.)

## Notes

- Open-Meteo is free for non-commercial use (~10k calls/day). No account, no
  key, no secret management - the whole pipeline was built and tested with zero
  credentials. See [open-meteo.com](https://open-meteo.com/) for limits.
- Weather codes are mapped from the official WMO code table in
  `weather_broker.py` (that's the "parsing" the adapter owns).
- `weather_broker.py` is duplicated into `weather_dashboard/` because each
  Databricks App deploys independently from its own folder - same tradeoff
  documented in Day 3's README.
- Stretch tools included: `get_historical_weather` (Open-Meteo Archive API)
  and `compare_weather`. A NWS alerts tool is a natural follow-up if you want
  US-only severe-weather alerts.
