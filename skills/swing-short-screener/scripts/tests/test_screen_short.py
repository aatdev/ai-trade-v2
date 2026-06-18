#!/usr/bin/env python3
"""End-to-end and filter tests for the screener orchestration (offline)."""

import json
import sys
import types

from screen_short import (
    analyze_symbol,
    filter_and_rank,
    main,
    parse_arguments,
    passes_short_filter,
    run_from_fixture,
    run_live,
    stop_geometry_reason,
)
from weakness_metrics import compute_metrics

from conftest import downtrend_closes, make_series, uptrend_closes


def test_filter_rejects_above_ma200(uptrend_bars):
    m = compute_metrics(uptrend_bars)
    passed, reason = passes_short_filter(m)
    assert passed is False
    assert reason == "above_ma200_not_stage4"


def test_filter_rejects_illiquid(downtrend_bars):
    m = compute_metrics(downtrend_bars)
    passed, reason = passes_short_filter(m, min_dollar_vol=10**12)
    assert passed is False
    assert reason == "illiquid_squeeze_risk"


def test_filter_rejects_insufficient_history():
    passed, reason = passes_short_filter(None)
    assert passed is False
    assert reason == "insufficient_history"


def test_analyze_downtrend_produces_record(downtrend_bars):
    record, reason = analyze_symbol(downtrend_bars, spy_return=0.0, name="WEAK", sector="Tech")
    assert record is not None
    assert reason == ""
    assert record["grade"] in ("A", "B", "C")
    assert record["metrics"]["below_ma200"] is True


def test_stop_geometry_bounds():
    assert stop_geometry_reason(0.44) == "stop_too_tight_noise"  # the real ALLE case
    assert stop_geometry_reason(25.9) == "stop_too_wide_post_crash"  # the real ADBE case
    assert stop_geometry_reason(4.0) == ""
    assert stop_geometry_reason(2.0) == ""  # inclusive bounds
    assert stop_geometry_reason(10.0) == ""


def test_analyze_symbol_rejects_noise_stop(downtrend_bars, monkeypatch):
    import screen_short

    fake_score = {
        "composite_score": 85.0,
        "grade": "A",
        "raw_grade": "A",
        "state_cap_applied": False,
        "oversold_extended": False,
        "components": {},
        "strongest_signal": "trend_structure",
        "weakest_signal": "liquidity",
        "trade_levels": {"entry": 100.0, "stop": 100.5, "stop_pct": 0.5, "target_2r": 99.0},
    }
    monkeypatch.setattr(screen_short, "score_candidate", lambda m, s, sector_info=None: fake_score)
    record, reason = analyze_symbol(downtrend_bars, spy_return=0.0)
    assert record is None
    assert reason == "stop_too_tight_noise"


def test_filter_and_rank_orders_and_drops_d():
    results = [
        {"grade": "A", "composite_score": 88},
        {"grade": "C", "composite_score": 55},
        {"grade": "D", "composite_score": 40},
        {"grade": "B", "composite_score": 70},
    ]
    ranked = filter_and_rank(results, min_grade="C", top=10)
    assert [r["composite_score"] for r in ranked] == [88, 70, 55]  # D dropped, sorted desc


def _build_fixture(tmp_path):
    fixture = {
        "index": make_series(uptrend_closes(260, 195.0, 200.0)),
        "symbols": {
            "WEAK": {
                "name": "Weak Co",
                "sector": "Technology",
                "bars": make_series(
                    downtrend_closes(260, 220.0, 140.0), base_volume=3_000_000, last_vol_mult=2.5
                ),
            },
            "STRONG": {
                "name": "Strong Co",
                "sector": "Technology",
                "bars": make_series(uptrend_closes(260, 100.0, 220.0), base_volume=3_000_000),
            },
        },
    }
    path = tmp_path / "fix.json"
    path.write_text(json.dumps(fixture))
    return str(path)


def test_run_from_fixture_filters_strong_out(tmp_path):
    fixture_path = _build_fixture(tmp_path)
    args = parse_arguments(
        [
            "--fixture",
            fixture_path,
            "--rs-lookback",
            "63",
            "--min-price",
            "5",
            "--min-dollar-vol",
            "1000000",
        ]
    )
    results, meta = run_from_fixture(fixture_path, 63, args)
    symbols = {r["symbol"] for r in results}
    assert "WEAK" in symbols  # Stage 4 passes
    assert "STRONG" not in symbols  # above MA200 invalidated
    assert meta["source"] == "fixture"


def _install_fake_tv_client(monkeypatch, bars_map, constituents=None):
    """Replace the `tv_client` module run_live imports with a recording fake.

    The fake mirrors the batch contract of the real TradingView client: a single
    get_batch_historical() warms an internal "prefetched" set, and any later
    get_historical_prices() for a symbol outside that set is recorded as a
    one-at-a-time fetch (the slow path the batch is meant to eliminate).
    Pass ``constituents`` to drive the S&P 500 universe path (each entry carries
    a sector). Returns the list the constructed client instances are appended to.
    """
    instances = []

    class FakeTVClient:
        def __init__(self, api_key=None):
            self.rate_limit_reached = False
            self.api_calls_made = 0
            self.batch_calls = []
            self.individual_fetches = []
            self._prefetched = set()
            instances.append(self)

        def get_sp500_constituents(self):
            return constituents

        def get_batch_historical(self, symbols, days=260):
            syms = list(symbols)
            self.batch_calls.append(syms)
            self._prefetched.update(syms)
            return {s: bars_map[s] for s in syms if s in bars_map}

        def get_historical_prices(self, symbol, days=260):
            if symbol not in self._prefetched:
                self.individual_fetches.append(symbol)
            bars = bars_map.get(symbol)
            return {"symbol": symbol, "historical": bars} if bars else None

    fake_mod = types.ModuleType("tv_client")
    fake_mod.FMPClient = FakeTVClient
    monkeypatch.setitem(sys.modules, "tv_client", fake_mod)
    return instances


def test_run_live_batches_history_in_one_prefetch(monkeypatch):
    bars_map = {
        "WEAK": make_series(
            downtrend_closes(260, 220.0, 140.0), base_volume=3_000_000, last_vol_mult=2.5
        ),
        "STRONG": make_series(uptrend_closes(260, 100.0, 220.0), base_volume=3_000_000),
        "SPY": make_series(uptrend_closes(260, 195.0, 200.0)),
    }
    instances = _install_fake_tv_client(monkeypatch, bars_map)

    args = parse_arguments(
        ["--universe", "WEAK", "STRONG", "--sector-rs-gate", "0", "--min-dollar-vol", "1000000"]
    )
    results, meta = run_live(args)

    client = instances[0]
    # Exactly one batch fetch, covering every universe symbol + the SPY benchmark.
    assert len(client.batch_calls) == 1
    assert set(client.batch_calls[0]) == {"WEAK", "STRONG", "SPY"}
    # Every per-symbol read after the prefetch is a cache hit — the slow
    # one-symbol-per-process path is never taken.
    assert client.individual_fetches == []
    assert meta["source"] == "tradingview"
    assert {r["symbol"] for r in results} == {"WEAK"}  # STRONG is above MA200


def test_run_live_prefetch_includes_sector_etfs(monkeypatch):
    # S&P 500 path: WEAK is Technology, so XLK must ride along in the single
    # prefetch batch — then compute_sector_rs() is a cache hit too.
    bars_map = {
        "WEAK": make_series(
            downtrend_closes(260, 220.0, 140.0), base_volume=3_000_000, last_vol_mult=2.5
        ),
        "SPY": make_series(uptrend_closes(260, 195.0, 200.0)),
        "XLK": make_series(uptrend_closes(260, 100.0, 120.0)),
    }
    constituents = [{"symbol": "WEAK", "name": "Weak Co", "sector": "Technology"}]
    instances = _install_fake_tv_client(monkeypatch, bars_map, constituents=constituents)

    monkeypatch.setattr(
        "screen_short._load_profile",
        lambda: {"sector_rs_gate": 1, "sector_rs_threshold": 5.0},
    )
    args = parse_arguments(["--full-sp500", "--min-dollar-vol", "1000000"])
    results, meta = run_live(args)

    client = instances[0]
    assert len(client.batch_calls) == 1
    assert set(client.batch_calls[0]) == {"WEAK", "SPY", "XLK"}
    assert client.individual_fetches == []


def test_main_writes_reports(tmp_path):
    fixture_path = _build_fixture(tmp_path)
    out_dir = tmp_path / "reports"
    rc = main(
        [
            "--fixture",
            fixture_path,
            "--output-dir",
            str(out_dir),
            "--as-of",
            "2026-04-30",
            "--min-dollar-vol",
            "1000000",
        ]
    )
    assert rc == 0
    json_file = out_dir / "swing_short_screener_2026-04-30.json"
    md_file = out_dir / "swing_short_screener_2026-04-30.md"
    assert json_file.exists()
    assert md_file.exists()
    payload = json.loads(json_file.read_text())
    assert "candidates" in payload and "meta" in payload
    assert any(c["symbol"] == "WEAK" for c in payload["candidates"])
