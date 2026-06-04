"""Dividend-layer contract for tv_client_base.

The dividend-growth-pullback-screener needs (a) an annual DPS history for
3Y-CAGR / consistency math and (b) snapshot ratios (payout, D/E, current
ratio, ROE, margins, P/E, P/B) — both sourced from the TradingView scanner
(`tv fundamentals --history`), no FMP key. These tests pin the FMP-shaped
projections with a fully mocked `_cli` / `_fundamentals` (no live chart).

Scanner field semantics worth pinning:
  - dps_common_stock_prim_issue_fy_h : annual DPS, most-recent-first, ~20y
  - dividend_payout_ratio_ttm        : PERCENT (60.12) — FMP shape wants decimal
  - return_on_equity_fq / net_margin_ttm : PERCENT (kept as-is, documented)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPTS_LIB))

import tv_client_base as tvb  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(tvb, "_resolve_cli", lambda: ["echo"])
    monkeypatch.setattr(tvb.time, "sleep", lambda *_: None)
    return tvb.TVClient(cache_disable_env="TV_NO_CACHE", settle=0.0, poll_interval=0.01)


JNJ_FUNDAMENTALS = {
    "success": True,
    "symbol": "NYSE:JNJ",
    "name": "Johnson & Johnson",
    "valuation": {
        "market_cap_basic": 471_000_000_000,
        "price_earnings_ttm": 25.6,
        "price_book_fq": 5.9,
        "total_shares_outstanding_fundamental": 2_407_220_000,
    },
    "margins": {"net_margin_ttm": 21.0},
    "returns": {"return_on_equity_fq": 32.9},
    "balance": {"debt_to_equity_fq": 0.62, "current_ratio_fq": 1.11},
    "cashflow": {
        "free_cash_flow_ttm": 19_698_000_000,
        "cash_f_operating_activities_ttm": 24_000_000_000,
    },
    "dividends": {
        "dividends_yield_current": 2.35,
        "dividend_payout_ratio_ttm": 60.12,
        "dps_common_stock_prim_issue_fy": 5.14,
        "continuous_dividend_growth": 45,
    },
    "history": {
        "dps_common_stock_prim_issue_fy_h": [5.14, 4.91, 4.70, 4.45, 4.19],
        "free_cash_flow_fy_h": [19_698_000_000, 19_842_000_000, 18_248_000_000],
    },
}


# ------------------------------------------------------------ dividend history
def test_dividend_history_shapes_annual_dps(client, monkeypatch):
    monkeypatch.setattr(client, "_fundamentals", lambda sym: JNJ_FUNDAMENTALS)

    out = client.get_dividend_history("JNJ")
    assert out is not None
    hist = out["historical"]
    assert len(hist) == 5
    # NEWEST-FIRST, FMP shape: one synthetic year-end entry per fiscal year.
    assert hist[0]["dividend"] == 5.14
    assert hist[-1]["dividend"] == 4.19
    last_completed = date.today().year - 1
    assert hist[0]["date"] == f"{last_completed}-12-31"
    assert hist[1]["date"] == f"{last_completed - 1}-12-31"
    # adjDividend mirrors dividend (annual series is already split-adjusted).
    assert hist[0]["adjDividend"] == 5.14


def test_dividend_history_none_when_no_dps(client, monkeypatch):
    no_dps = {**JNJ_FUNDAMENTALS, "history": {"total_revenue_fy_h": [1, 2]}}
    monkeypatch.setattr(client, "_fundamentals", lambda sym: no_dps)
    monkeypatch.setattr(client, "_fundamentals_live", lambda sym: no_dps)
    assert client.get_dividend_history("XYZ") is None


def test_dividend_history_none_when_fundamentals_fail(client, monkeypatch):
    monkeypatch.setattr(client, "_fundamentals", lambda sym: None)
    monkeypatch.setattr(client, "_fundamentals_live", lambda sym: None)
    assert client.get_dividend_history("XYZ") is None


def test_dividend_history_refetches_live_when_cache_lacks_dps(client, monkeypatch):
    """Metrics-cache snapshots collected before the dividend fields were added
    have no dps history — the client must retry with a live scanner fetch."""
    stale = {**JNJ_FUNDAMENTALS, "history": {"total_revenue_fy_h": [1, 2, 3]}}
    calls = {"live": 0}

    def fake_live(sym):
        calls["live"] += 1
        return JNJ_FUNDAMENTALS

    monkeypatch.setattr(client, "_fundamentals", lambda sym: stale)
    monkeypatch.setattr(client, "_fundamentals_live", fake_live)

    out = client.get_dividend_history("JNJ")
    assert calls["live"] == 1
    assert out["historical"][0]["dividend"] == 5.14


def test_fundamentals_live_updates_cache(client, monkeypatch):
    """_fundamentals_live switches the chart itself (the metrics-cache
    get_quote fast path never touches the chart), then pins the fresh payload
    so later _fundamentals('SYM') calls see the new fields."""
    switched = []
    monkeypatch.setattr(client, "_switch_symbol", lambda sym: switched.append(sym) or True)
    monkeypatch.setattr(client, "_cli", lambda *a, parse=True: JNJ_FUNDAMENTALS)

    out = client._fundamentals_live("JNJ")
    assert switched == ["JNJ"]
    assert out["success"] is True
    assert client.cache["fund_JNJ"] is out
    # And the regular path now serves it without re-fetching.
    monkeypatch.setattr(client, "_cli", lambda *a, parse=True: pytest.fail("re-fetched"))
    assert client._fundamentals("JNJ") is out


def test_fundamentals_live_rejects_wrong_symbol_payload(client, monkeypatch):
    """If the chart lags the switch and `tv fundamentals` reports a different
    symbol, the payload must be discarded — never cached under the wrong key."""
    monkeypatch.setattr(client, "_switch_symbol", lambda sym: True)
    monkeypatch.setattr(client, "_cli", lambda *a, parse=True: JNJ_FUNDAMENTALS)

    out = client._fundamentals_live("MSFT")  # payload says NYSE:JNJ
    assert out is None
    assert client.cache["fund_MSFT"] is None


# ---------------------------------------------------------------- key metrics
def test_key_metrics_fmp_shape(client, monkeypatch):
    monkeypatch.setattr(client, "_fundamentals", lambda sym: JNJ_FUNDAMENTALS)

    metrics = client.get_key_metrics("JNJ")
    assert isinstance(metrics, list) and len(metrics) == 1
    m = metrics[0]
    assert m["peRatio"] == 25.6
    assert m["pbRatio"] == 5.9
    # PERCENT semantics (TradingView native) — documented divergence from FMP.
    assert m["roe"] == 32.9
    assert m["netProfitMargin"] == 21.0
    # payoutRatio keeps FMP's decimal semantics (callers do `* 100`).
    assert m["payoutRatio"] == pytest.approx(0.6012)
    assert m["debtToEquity"] == 0.62
    assert m["currentRatio"] == 1.11
    assert m["dividendYield"] == 2.35
    assert m["freeCashFlow"] == 19_698_000_000
    assert m["operatingCashFlow"] == 24_000_000_000
    assert m["sharesOutstanding"] == 2_407_220_000
    assert m["annualDividendPerShare"] == 5.14
    assert m["continuousDividendGrowth"] == 45


def test_key_metrics_missing_groups(client, monkeypatch):
    monkeypatch.setattr(
        client, "_fundamentals", lambda sym: {"success": True, "symbol": "X", "name": "X"}
    )
    m = client.get_key_metrics("X")[0]
    assert m["peRatio"] is None
    assert m["payoutRatio"] is None
    assert m["debtToEquity"] is None


def test_key_metrics_none_when_fundamentals_fail(client, monkeypatch):
    monkeypatch.setattr(client, "_fundamentals", lambda sym: None)
    assert client.get_key_metrics("XYZ") is None
