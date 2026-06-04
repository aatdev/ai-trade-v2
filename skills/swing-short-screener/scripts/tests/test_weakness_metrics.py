#!/usr/bin/env python3
"""Tests for the pure weakness-metric calculations."""

from weakness_metrics import compute_metrics, pct_return, rsi, sma


def test_sma_and_offset():
    values = [10.0, 20.0, 30.0, 40.0]  # most-recent-first
    assert sma(values, 2) == 15.0  # (10+20)/2
    assert sma(values, 2, offset=2) == 35.0  # (30+40)/2
    assert sma(values, 5) is None  # not enough data


def test_pct_return():
    closes = [110.0, 105.0, 100.0]  # latest 110, 2 sessions ago 100
    assert pct_return(closes, 2) == (110.0 - 100.0) / 100.0
    assert pct_return(closes, 10) is None


def test_rsi_bounds():
    rising = list(range(50, 0, -1))  # most-recent-first, strictly rising chrono
    assert rsi([float(x) for x in rising], 14) == 100.0  # no losses


def test_downtrend_metrics_are_stage4(downtrend_bars):
    m = compute_metrics(downtrend_bars)
    assert m is not None
    assert m["below_ma50"] is True
    assert m["below_ma200"] is True
    assert m["death_cross"] is True
    assert m["ma50_falling"] is True
    assert m["broke_support"] is True
    assert m["lower_high_pct"] > 0  # recent highs below prior highs


def test_uptrend_metrics_not_stage4(uptrend_bars):
    m = compute_metrics(uptrend_bars)
    assert m is not None
    assert m["below_ma200"] is False
    assert m["death_cross"] is False


def test_insufficient_history_returns_none():
    short = [{"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 50
    assert compute_metrics(short) is None
