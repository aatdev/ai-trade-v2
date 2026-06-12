"""TradingView-backed daily-close adapter for MAE/MFE.

Drop-in for ``FMPPriceAdapter`` (same ``get_daily_closes`` contract) that reads
prices from the repo's shared TradingView data layer (``scripts/lib/tv_client``:
vendored ``tv`` CLI via TradingView Desktop/CDP with a ``state/metrics`` cache
fast path). **No API key required.**

The TV layer serves bars relative to *today*, so the adapter fetches enough
lookback to cover ``from_date`` and filters to the requested window.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Shared TradingView data layer lives at <repo>/scripts/lib (same import used
# by vcp-screener / swing-short-screener).
_REPO_LIB = Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(_REPO_LIB) not in sys.path:
    sys.path.insert(0, str(_REPO_LIB))


class TVPriceAdapter:
    """Fetch daily adjusted closes via the shared TradingView data layer."""

    source = "tradingview_eod"

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            from tv_client import FMPClient  # TradingView-backed drop-in

            self._client = FMPClient()
        return self._client

    def get_daily_closes(self, ticker: str, from_date: str, to_date: str) -> list[dict]:
        """Return daily close prices, oldest first.

        Args:
            ticker: Stock symbol (e.g., "AAPL").
            from_date: Start date "YYYY-MM-DD" (datetime strings are truncated).
            to_date: End date "YYYY-MM-DD".

        Returns:
            List of {"date": "YYYY-MM-DD", "close": float}, oldest first.
        """
        start = from_date[:10]
        end = to_date[:10]
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        lookback_days = max((date.today() - start_date).days + 10, 30)

        history = self._get_client().get_historical_prices(ticker, days=lookback_days)
        bars = (history or {}).get("historical") or []

        rows: list[dict] = []
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            bar_date = str(bar.get("date") or "")[:10]
            close = bar.get("adjClose", bar.get("close"))
            if not bar_date or close is None:
                continue
            if start <= bar_date <= end:
                rows.append(
                    {
                        "date": bar_date,
                        "close": float(close),
                        # Intraday extremes for MAE/MFE; closes understate both.
                        "high": float(bar.get("high") or close),
                        "low": float(bar.get("low") or close),
                    }
                )

        rows.sort(key=lambda r: r["date"])  # TV layer returns newest first
        if not rows:
            logger.warning("No TradingView price data for %s (%s..%s)", ticker, start, end)
        return rows
