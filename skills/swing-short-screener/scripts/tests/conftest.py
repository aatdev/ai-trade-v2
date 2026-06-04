"""Shared fixtures and synthetic-series helpers for Swing Short Screener tests."""

import os
import sys

import pytest

# Make the scripts/ modules importable (mirrors vcp-screener convention).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))


def make_series(closes_chrono, base_volume=2_000_000, last_vol_mult=1.0):
    """Build a most-recent-first OHLCV bar list from a chronological close list.

    High/low bracket each close by ±0.5%. The latest bar's volume is multiplied
    by ``last_vol_mult`` to simulate a volume spike on the most recent session.
    """
    bars_chrono = []
    n = len(closes_chrono)
    for i, c in enumerate(closes_chrono):
        vol = base_volume * (last_vol_mult if i == n - 1 else 1.0)
        bars_chrono.append(
            {
                "date": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(c, 2),
                "high": round(c * 1.005, 2),
                "low": round(c * 0.995, 2),
                "close": round(c, 2),
                "volume": int(vol),
            }
        )
    return list(reversed(bars_chrono))  # most-recent-first


def downtrend_closes(n=260, start=200.0, end=120.0):
    """Smooth linear decline (Stage 4)."""
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


def uptrend_closes(n=260, start=100.0, end=200.0):
    """Smooth linear advance (Stage 2)."""
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


@pytest.fixture
def downtrend_bars():
    # Decline, then a fresh break to a new low on a volume spike — but not so
    # deep below MA50 that the oversold cap triggers.
    closes = downtrend_closes(260, 220.0, 140.0)
    closes[-1] = closes[-1] * 0.985  # small fresh break of the prior low
    return make_series(closes, base_volume=3_000_000, last_vol_mult=2.5)


@pytest.fixture
def uptrend_bars():
    return make_series(uptrend_closes(260, 100.0, 220.0), base_volume=3_000_000)


@pytest.fixture
def index_bars():
    # Index roughly flat over the lookback so a declining stock underperforms.
    return make_series(uptrend_closes(260, 195.0, 200.0), base_volume=5_000_000)
