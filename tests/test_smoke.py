#!/usr/bin/env python
"""Offline-friendly sanity checks for the atmos data layer.

These call the live keyless Open-Meteo API directly - no MCP transport and no
Databricks needed. Good for a quick smoke test and for demo screenshots.

Usage:
    python -m tests.test_smoke
    # or: pytest tests/
"""

import sys

from atmos import config
from atmos import datasource as source


def _check(label, fn):
    print(f"\n--- {label} ---")
    try:
        out = fn()
        print("ok:", out)
        return True
    except Exception as exc:  # noqa: BLE001 - test harness
        print("failed:", exc)
        return False


def _umbrella_check(place, when):
    outlook = source.daily_outlook(place, horizon=config.MAX_HORIZON_DAYS)
    index = source.day_for(when, outlook["outlook"])
    day = outlook["outlook"][index]
    prob = day.get("precipitation_probability") or 0
    mm = day.get("precipitation_mm") or 0.0
    needed = prob >= config.RAIN_CHANCE_CUTOFF or mm >= config.RAIN_AMOUNT_CUTOFF
    return {"date": day["date"], "umbrella_needed": needed, "prob": prob, "mm": mm}


def _unknown_place():
    try:
        source.geocode("zzxxqq-not-a-place-123")
        raise AssertionError("expected a ValueError")
    except ValueError as exc:
        return f"clean error as expected: {exc}"


def main() -> bool:
    print("=" * 60)
    print("atmos data-layer smoke test (Open-Meteo, no key)")
    print("=" * 60)

    results = [
        _check("geocode('Madrid')", lambda: source.geocode("Madrid")),
        _check("current_conditions('Tokyo')", lambda: source.current_conditions("Tokyo")),
        _check("daily_outlook('Austin, TX', 3)", lambda: source.daily_outlook("Austin, TX", 3)),
        _check("umbrella rule (Bogota today)", lambda: _umbrella_check("Bogota", "today")),
        _check("bad place returns clean error", _unknown_place),
    ]

    print("\n" + "=" * 60)
    print(f"passed {sum(results)}/{len(results)}")
    print("=" * 60)
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
