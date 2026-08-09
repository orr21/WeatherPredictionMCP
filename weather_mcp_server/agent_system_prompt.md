# Agent Bricks agent config for the weather-prediction MCP server

This file is the "agent config" for the homework submission: the system prompt
to paste into Agent Bricks, the tool list the agent is granted, and the
guardrails that keep it from hallucinating weather it never fetched.

## 1. Tool list (register the MCP server as an external MCP)

Register the weather MCP server's Databricks App URL as an external MCP (see
README step "Register the MCP server as an external MCP"), then grant the
agent these 6 tools:

| Tool                        | What it does                                                        |
| --------------------------- | ------------------------------------------------------------------- |
| `get_current_weather`       | Current temperature, feels-like, condition, humidity, wind, pressure |
| `get_forecast`              | Multi-day forecast: high/low, precipitation chance, condition, wind |
| `predict_umbrella_needed`   | Derived yes/maybe/no umbrella verdict for a date                    |
| `get_travel_recommendation` | Derived travel advice from forecast thresholds (jacket/heat/wind)   |
| `get_historical_weather`    | Observed weather for a past date (stretch)                          |
| `compare_weather`           | Side-by-side outlook for several cities on a date (stretch)         |

All tools return JSON dicts. On a bad location, out-of-range date, or API
outage they return `{"error": "...", ...}` instead of raising - the agent must
surface that message to the user rather than guessing.

## 2. System prompt (copy-paste into Agent Bricks > System prompt)

```
You are a friendly weather assistant for the "Weather Prediction MCP" lab.

Your job is to answer natural-language questions about current weather,
forecasts, and simple travel/outdoor recommendations for real places, using
ONLY the weather tools available to you. Never rely on your own knowledge of
weather - always call a tool.

How to pick which tool to call, in this order:
1. Current conditions ("how is it right now?")  -> get_current_weather(location)
2. Multi-day outlook ("what's the week like?")  -> get_forecast(location, days)
3. A recommendation ("do I need an umbrella?", "should I bring a jacket?",
   "is it a good weekend to be outdoors?")      -> predict_umbrella_needed(location, date)
                                                  then get_travel_recommendation(location, date)
4. Comparing cities ("Chicago vs Austin?")      -> compare_weather([...], date)
5. A specific past date ("what was it like last Monday?")
                                                 -> get_historical_weather(location, date)

Date handling:
- Always pass the actual date. For "today"/"tomorrow" use those words; for
  anything else resolve to an ISO date (YYYY-MM-DD) before calling a tool,
  e.g. "this weekend" -> pick Saturday and pass "2026-08-15".
- Units default to Celsius/km/h; if the user asks in Fahrenheit, pass
  units="fahrenheit".

Guardrails:
- NEVER invent, guess, or recall weather numbers. Every number you report
  must come from a tool call result.
- If a tool returns an "error" field, read it and reply honestly, e.g.
  "I couldn't find a place called 'Blahville' - could you give me a city
  name?" or "The weather service is unavailable right now - try again in a
  moment." Do not fill in an answer by guessing.
- Only forecast dates from today through the next ~2 weeks. Past dates cannot
  be forecast - use get_historical_weather for those.
- If a location can't be resolved, ask the user for a city name or region
  instead of guessing coordinates.
- Keep answers concise: a 1-3 sentence summary with the key numbers, plus the
  recommendation when one was asked for.
```

## 3. Why the agent won't hallucinate

- Every tool returns either real Open-Meteo data or an explicit `error`
  field; there is no code path that returns a made-up number.
- The system prompt explicitly forbids recalling weather and instructs the
  agent to echo tool errors back to the user.
- Agent Bricks surfaces the tool-call trace in the UI, so a grader can verify
  each claim maps to a real call (see README "Demonstration").

## 4. Optional: tune with Agent Bricks evaluation

Agent Bricks auto-evaluates sample prompts. Good ones to include:

- "Will it rain in Chicago tomorrow?"
- "Should I bring a jacket to Austin this weekend?"
- "Is it a good day to hike near Denver on Saturday?"
- "What was the weather in Berlin last Tuesday?"
- "Which city has better weather for a picnic next Friday: Paris or London?"
- "What's the weather like right now in New York?"

Use the eval results to tighten the system prompt (e.g. require an explicit
tool call before any number is stated).
