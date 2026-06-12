#!/usr/bin/env python3
"""Tests for the pure weakness-metric calculations."""

from weakness_metrics import atr, compute_metrics, last_swing_high, pct_return, rsi, sma


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


def test_atr_constant_range():
    bars = [{"high": 101.0, "low": 99.0, "close": 100.0, "open": 100.0, "volume": 1}] * 20
    assert abs(atr(bars, 14) - 2.0) < 1e-9


def test_atr_insufficient_history():
    bars = [{"high": 1.0, "low": 1.0, "close": 1.0, "open": 1.0, "volume": 1}] * 10
    assert atr(bars, 14) is None


def test_last_swing_high_prefers_most_recent_local_max():
    # most-recent-first: a fresh bounce high (101) after a crash whose pre-crash
    # top (125) is still inside the 20-session window — the stop anchor must be
    # the recent lower high, not the absolute max.
    highs = [98.0, 99.0, 101.0, 100.0, 99.0, 98.0, 97.0, 120.0, 125.0, 122.0] + [90.0] * 12
    assert last_swing_high(highs) == 101.0


def test_last_swing_high_none_when_no_local_max():
    # Highs strictly increasing into the past (steady decline) — no swing point.
    highs = [100.0 + i for i in range(20)]
    assert last_swing_high(highs) is None


def test_compute_metrics_exposes_atr_and_swing_high(downtrend_bars):
    m = compute_metrics(downtrend_bars)
    assert m["atr14"] is not None and m["atr14"] > 0
    assert "swing_high_20" in m
