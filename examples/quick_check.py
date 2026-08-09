#!/usr/bin/env python
"""Minimal demo: query the keyless data layer directly.

Shows the pieces an agent would call without the MCP transport in the way.

Usage:
    python -m examples.quick_check
"""

from atmos import datasource as source


def main() -> None:
    print("live conditions:")
    for city in ("Buenos Aires", "Cape Town", "Reykjavik"):
        now = source.current_conditions(city)
        temp = now["temperature"]
        print(f"  {now['location']:<42} {temp}°C  {now['conditions']}")

    outlook = source.daily_outlook("Austin, TX", horizon=5)
    print(f"\noutlook for {outlook['location']} (next {outlook['days']} days):")
    for day in outlook["outlook"]:
        print(f"  {day['date']}  hi {day['high']}°C  lo {day['low']}°C  "
              f"rain {day['precipitation_probability']}%  {day['conditions']}")


if __name__ == "__main__":
    main()
