"""Market data provider abstraction.

The rest of the app calls fetch_ohlcv / fetch_ohlcv_bulk in
stock_screener.data.fetcher without knowing where the data comes from.
Those functions delegate to whichever MarketDataProvider is active,
selected by the DATA_PROVIDER environment variable.

Providers currently registered:
  yfinance   — free, per-ticker, rate-limit prone (current default)
  massive    — massive.com (formerly polygon.io), REST + grouped-daily
               bulk endpoint. Needs MASSIVE_API_KEY.
  bloomberg  — stub for a future BLPAPI implementation running on a
               machine with a live Bloomberg Terminal login.

Adding a new provider = drop a file next to this one implementing the
MarketDataProvider protocol, register the class in PROVIDERS, and the
DATA_PROVIDER=<name> env flip is the only change anywhere else.
"""

from __future__ import annotations

import os
from typing import Type

from stock_screener.data.providers.base import MarketDataProvider
from stock_screener.data.providers.yfinance_provider import YFinanceProvider
from stock_screener.data.providers.massive_provider import MassiveProvider
from stock_screener.data.providers.bloomberg_stub import BloombergStubProvider


PROVIDERS: dict[str, Type[MarketDataProvider]] = {
    "yfinance": YFinanceProvider,
    "massive": MassiveProvider,
    "bloomberg": BloombergStubProvider,
}

DEFAULT_PROVIDER = "yfinance"

_instance: MarketDataProvider | None = None
_instance_name: str | None = None


def active_provider_name() -> str:
    """Which provider name the env var currently selects. Cheap; no
    provider instantiation. Used for the Data Health chip."""
    return os.environ.get("DATA_PROVIDER", DEFAULT_PROVIDER).lower()


def get_provider() -> MarketDataProvider:
    """Return the (cached) active provider instance."""
    global _instance, _instance_name
    name = active_provider_name()
    if _instance is not None and _instance_name == name:
        return _instance
    cls = PROVIDERS.get(name)
    if cls is None:
        valid = ", ".join(sorted(PROVIDERS.keys()))
        raise ValueError(
            f"Unknown DATA_PROVIDER={name!r}. Valid values: {valid}."
        )
    _instance = cls()
    _instance_name = name
    return _instance


def reset_provider_cache() -> None:
    """Force a re-read of DATA_PROVIDER on the next get_provider() call.
    Useful in tests that mutate the env."""
    global _instance, _instance_name
    _instance = None
    _instance_name = None


__all__ = [
    "MarketDataProvider",
    "YFinanceProvider",
    "MassiveProvider",
    "BloombergStubProvider",
    "PROVIDERS",
    "DEFAULT_PROVIDER",
    "active_provider_name",
    "get_provider",
    "reset_provider_cache",
]
