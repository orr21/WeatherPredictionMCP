"""Runtime thresholds used by the derived judgments.

Kept in a single module so the numbers are easy to audit and tune without
touching the MCP tools or the data layer. Every rule in atmos.logic reads
these values instead of re-declaring them, so the thresholds stay consistent
across tools.
"""

from __future__ import annotations

# --- precipitation ---------------------------------------------------------
RAIN_CHANCE_CUTOFF = 40      # percent chance that counts as "might rain"
RAIN_AMOUNT_CUTOFF = 1.0     # mm that counts as "meaningful rain"
SOAKED_CHANCE = 60           # percent chance considered "likely wet" when travelling

# --- temperature / wind ----------------------------------------------------
HEAT_THRESHOLD_C = 35.0
FREEZING_THRESHOLD_C = 0.0
JACKET_BELOW_C = 10.0
GUSTY_KMH = 40.0

# --- forecast window -------------------------------------------------------
DEFAULT_HORIZON_DAYS = 3
MAX_HORIZON_DAYS = 16
