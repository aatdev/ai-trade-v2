#!/usr/bin/env python3
"""
TVClient — single shared TradingView-backed drop-in replacement for FMPClient.

This is the ONE copy used by every migrated screener. Each skill's
`scripts/tv_client.py` (and `tv_client_base.py`, `metrics_cache.py`) is a
symlink to the canonical files here in `scripts/lib/`, so there is exactly one
implementation to maintain; packaging (`scripts/package_skills.py`) dereferences
the symlinks via `read_bytes()`, so each `.skill` archive is still self-contained.

All price / fundamental / macro logic lives in `tv_client_base.TVClient`; this
module only fixes the shared configuration and exposes the names the skills
import:

  - `FMPClient` (alias of `TVClient`)  — the default, returns price history as
    the `{symbol, historical}` dict.
  - `TVClientListHistory`              — identical, except `get_historical_prices`
    returns the bare `list[dict]` (most-recent-first). Only
    earnings-trade-analyzer wants this shape.
  - `ApiCallBudgetExceeded`            — re-exported for parity with FMPClient.

Shared config: quotes returned as `[dict]` (FMP style), caret index tickers
remapped to TradingView symbols, and the metrics-cache fast path toggled with
`TV_NO_CACHE=1`.
"""

from tv_client_base import (  # noqa: F401
    ApiCallBudgetExceeded,
    DEFAULT_INDEX_REMAP,
    TVClient as _BaseTVClient,
)


class TVClient(_BaseTVClient):
    """Default client: get_historical_prices -> {symbol, historical}."""

    def __init__(self, api_key=None, **kwargs):
        kwargs.setdefault("quote_as_list", True)
        kwargs.setdefault("index_remap", DEFAULT_INDEX_REMAP)
        kwargs.setdefault("cache_disable_env", "TV_NO_CACHE")
        super().__init__(api_key, **kwargs)


class TVClientListHistory(TVClient):
    """Variant for earnings-trade-analyzer: get_historical_prices returns the
    bare list[dict] (most-recent-first), not the {symbol, historical} dict.
    Internal base callers use self._history() (the dict), so this override is
    safe for get_quote / get_company_profile."""

    def get_historical_prices(self, symbol, days=250):
        res = self._history(symbol)
        return res["historical"] if res else None


# Drop-in alias: existing code imports/constructs FMPClient.
FMPClient = TVClient
