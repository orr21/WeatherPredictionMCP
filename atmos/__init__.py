"""atmos - lightweight weather intelligence over a keyless API.

The package ships a FastMCP tool surface (atmos.server) backed by a clean
data-access layer (atmos.datasource) and transparent decision rules
(atmos.logic). Open-Meteo needs no API key, so the whole stack runs and
tests without any secrets.
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__"]
