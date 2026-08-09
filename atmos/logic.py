"""The reasoning layer: transparent rules that turn raw outlook numbers into
plain-language judgments.

Nothing here talks to the network or to MCP - it is pure, testable decision
logic. Thresholds live in atmos.config and are referenced (never re-declared)
so the numbers stay consistent across tools.
"""

from __future__ import annotations

from . import config


def umbrella_verdict(prob, mm) -> tuple[bool, str]:
    """Apply the rain rule to a single day.

    Returns (needed, reason). An umbrella is needed when the chance of
    precipitation reaches config.RAIN_CHANCE_CUTOFF or the expected amount
    reaches config.RAIN_AMOUNT_CUTOFF. The probability catches "might rain"
    days; the millimetre floor catches days where the chance looks modest but
    meaningful rain is still expected.
    """
    prob = prob or 0
    mm = mm or 0.0
    by_chance = prob >= config.RAIN_CHANCE_CUTOFF
    by_amount = mm >= config.RAIN_AMOUNT_CUTOFF
    needed = bool(by_chance or by_amount)

    if needed:
        triggers = []
        if by_chance:
            triggers.append(f"{prob}% chance of rain (at or above {config.RAIN_CHANCE_CUTOFF}%)")
        if by_amount:
            triggers.append(f"{mm} mm expected (at or above {config.RAIN_AMOUNT_CUTOFF} mm)")
        reason = "Pack an umbrella: " + " and ".join(triggers) + "."
    else:
        reason = (f"No umbrella needed: {prob}% chance and {mm} mm expected - "
                  f"both below {config.RAIN_CHANCE_CUTOFF}% / {config.RAIN_AMOUNT_CUTOFF} mm.")
    return needed, reason


def travel_verdict(prob, mm, high, low, wind) -> tuple[str, list[str]]:
    """Pick a travel headline and packing list for a day.

    First matching rule wins for the headline:
        - chance >= 60% or amount >= 10 mm -> wet, plan around rain
        - wind >= GUSTY_KMH                 -> windy, secure loose items
        - high >= HEAT_THRESHOLD_C          -> very hot, hydrate
        - high <= FREEZING_THRESHOLD_C      -> freezing, dress warm
        - otherwise                         -> generally fine

    Returns (headline, bring-list).
    """
    prob = prob or 0
    mm = mm or 0.0
    wind = wind or 0

    if prob >= config.SOAKED_CHANCE or mm >= 10:
        headline = "Likely wet - allow extra time and plan around the rain."
    elif wind >= config.GUSTY_KMH:
        headline = "Windy - secure loose items and expect gusts."
    elif high is not None and high >= config.HEAT_THRESHOLD_C:
        headline = "Very hot - hydrate well and avoid the midday sun."
    elif high is not None and high <= config.FREEZING_THRESHOLD_C:
        headline = "Freezing - dress in layers and watch for ice."
    else:
        headline = "Generally fine for travel."

    bring = []
    if prob >= config.RAIN_CHANCE_CUTOFF or mm >= config.RAIN_AMOUNT_CUTOFF:
        bring.append("umbrella")
    if low is not None and low < config.JACKET_BELOW_C:
        bring.append("jacket")
    if high is not None and high >= config.HEAT_THRESHOLD_C:
        bring.append("water")

    return headline, bring
