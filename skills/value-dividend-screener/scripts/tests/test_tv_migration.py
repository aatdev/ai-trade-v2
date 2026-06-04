"""TradingView-data-layer contract for value-dividend-screener.

The screener's data layer moved from FMP REST (+FMP stock-screener stage 1) to
the shared tv_client (TradingView scanner + chart bars). These tests pin the
pieces the migration changed: annual-DPS dividend math, snapshot-ratio
financial health, the key-metrics payout reconstruction (DPS x shares),
percent-scale quality scoring, and the end-to-end screening loop against a
fully mocked client — no live chart, no keys.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import screen_dividend_stocks as mod  # noqa: E402

YEAR = date.today().year - 1  # last completed fiscal/calendar year


def _annual_dps_history(values):
    """FMP-shaped dividend history as tv_client.get_dividend_history emits:
    one synthetic year-end entry per fiscal year, newest first."""
    return {
        "symbol": "TEST",
        "historical": [
            {"date": f"{YEAR - i}-12-31", "dividend": v, "adjDividend": v}
            for i, v in enumerate(values)
        ],
    }


# ---------------------------------------------------------- dividend growth
class TestDividendGrowthOnAnnualSeries:
    def test_cagr_from_annual_series(self):
        # 2.70 -> 3.30 over 3 years ≈ 6.9% CAGR; newest-first input
        hist = _annual_dps_history([3.30, 3.00, 2.85, 2.70, 2.55])
        cagr, consistent, latest = mod.StockAnalyzer.analyze_dividend_growth(hist)
        assert cagr == pytest.approx(6.92, abs=0.1)
        assert consistent is True
        assert latest == 3.30

    def test_dividend_cut_breaks_consistency(self):
        hist = _annual_dps_history([3.30, 3.00, 3.50, 2.70])  # cut between years
        _, consistent, _ = mod.StockAnalyzer.analyze_dividend_growth(hist)
        assert consistent is False

    def test_too_short_history_rejected(self):
        hist = _annual_dps_history([3.30, 3.00])
        cagr, consistent, latest = mod.StockAnalyzer.analyze_dividend_growth(hist)
        assert cagr is None and latest is None


# ------------------------------------------------------- dividend stability
class TestDividendStabilityOnAnnualSeries:
    def test_stable_grower(self):
        hist = _annual_dps_history([3.30, 3.00, 2.85, 2.70, 2.55])
        out = mod.StockAnalyzer.analyze_dividend_stability(hist)
        assert out["is_stable"] is True
        assert out["is_growing"] is True
        assert out["years_of_growth"] >= 3

    def test_volatile_payer_flagged(self):
        hist = _annual_dps_history([4.00, 0.50, 3.80, 0.40])
        out = mod.StockAnalyzer.analyze_dividend_stability(hist)
        assert out["is_stable"] is False
        assert out["volatility_pct"] > 100


# --------------------------------------------------------- financial health
class TestFinancialHealthFromMetrics:
    def test_healthy(self):
        out = mod.StockAnalyzer.analyze_financial_health(
            {"debtToEquity": 0.62, "currentRatio": 1.11}
        )
        assert out == {
            "debt_to_equity": 0.62,
            "current_ratio": 1.11,
            "financially_healthy": True,
        }

    def test_overleveraged(self):
        out = mod.StockAnalyzer.analyze_financial_health({"debtToEquity": 2.5, "currentRatio": 1.5})
        assert out["financially_healthy"] is False

    def test_missing_ratios_pass(self):
        # Absent data does not disqualify (matches sibling screener semantics).
        out = mod.StockAnalyzer.analyze_financial_health({"debtToEquity": None})
        assert out["financially_healthy"] is True

    def test_empty_metrics(self):
        assert mod.StockAnalyzer.analyze_financial_health({}) == {}


# ------------------------------------------------------------ payout ratios
class TestPayoutRatiosFromMetrics:
    METRICS = {
        "annualDividendPerShare": 5.14,
        "sharesOutstanding": 2_407_220_000,
        "freeCashFlow": 19_698_000_000,
        "operatingCashFlow": 24_000_000_000,
        "payoutRatio": 0.6012,
    }

    def test_non_reit_uses_scanner_payout(self):
        out = mod.StockAnalyzer.calculate_payout_ratios_from_metrics(self.METRICS)
        assert out["payout_ratio"] == pytest.approx(60.1, abs=0.05)
        # DPS x shares / FCF = 12.37B / 19.70B ≈ 62.8%
        assert out["fcf_payout_ratio"] == pytest.approx(62.8, abs=0.1)

    def test_reit_uses_ocf_proxy(self):
        out = mod.StockAnalyzer.calculate_payout_ratios_from_metrics(self.METRICS, is_reit=True)
        # DPS x shares / OCF = 12.37B / 24.0B ≈ 51.6%
        assert out["payout_ratio"] == pytest.approx(51.6, abs=0.1)

    def test_empty_metrics(self):
        out = mod.StockAnalyzer.calculate_payout_ratios_from_metrics({})
        assert out == {"payout_ratio": None, "fcf_payout_ratio": None}


# ------------------------------------------------------------ quality score
class TestQualityScoreFromMetrics:
    def test_percent_scale_inputs(self):
        # TradingView serves ROE / net margin as PERCENT (e.g. 25.0, 18.0)
        out = mod.StockAnalyzer.calculate_quality_score({"roe": 25.0, "netProfitMargin": 18.0})
        assert out["roe"] == 25.0
        assert out["profit_margin"] == 18.0
        assert out["quality_score"] == 100.0  # both components capped

    def test_partial_credit(self):
        out = mod.StockAnalyzer.calculate_quality_score({"roe": 10.0, "netProfitMargin": 7.5})
        # 10/20*50 = 25 ; 7.5/15*50 = 25
        assert out["quality_score"] == pytest.approx(50.0)

    def test_empty_metrics(self):
        out = mod.StockAnalyzer.calculate_quality_score({})
        assert out["quality_score"] == 0


# ----------------------------------------------------- end-to-end screening
def _make_client():
    """Mock tv_client with one qualifying value-dividend stock (3.3% yield,
    cheap multiples, steady grower, oversold)."""
    client = MagicMock()
    client.get_sp500_constituents.return_value = [
        {"symbol": "GOOD", "name": "Good Co", "sector": "Industrials"}
    ]
    client.get_company_profile.return_value = {
        "symbol": "GOOD",
        "companyName": "Good Co",
        "sector": "Industrials",
        "industry": "Machinery",
        "mktCap": 50_000_000_000,
        "price": 100.0,
    }
    client.get_key_metrics.return_value = [
        {
            "peRatio": 15.0,
            "pbRatio": 1.5,
            "roe": 25.0,
            "netProfitMargin": 18.0,
            "payoutRatio": 0.40,
            "debtToEquity": 0.8,
            "currentRatio": 1.4,
            "dividendYield": 3.3,
            "freeCashFlow": 5_000_000_000,
            "operatingCashFlow": 6_000_000_000,
            "sharesOutstanding": 500_000_000,
            "annualDividendPerShare": 3.30,
            "continuousDividendGrowth": 8,
        }
    ]
    client.get_dividend_history.return_value = _annual_dps_history(
        [3.30, 3.00, 2.85, 2.70, 2.55]  # ~6.9% CAGR, stable grower
    )
    client.get_income_statement.return_value = [
        {"date": None, "eps": 5.0, "revenue": 1_000, "netIncome": 180},
        {"date": None, "eps": 4.5, "revenue": 950, "netIncome": 170},
        {"date": None, "eps": 4.0, "revenue": 900, "netIncome": 160},
        {"date": None, "eps": 3.5, "revenue": 850, "netIncome": 150},
    ]
    # 30 falling closes (newest first, rising values back in time) -> RSI << 40
    client.get_historical_prices.return_value = {
        "symbol": "GOOD",
        "historical": [{"close": 100.0 + i} for i in range(30)],
    }
    client.rate_limit_reached = False
    return client


def test_screen_qualifies_mocked_stock(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_value_dividend_stocks(top_n=20)

    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "GOOD"
    assert r["dividend_yield"] == pytest.approx(3.3)
    assert r["pe_ratio"] == 15.0
    assert r["pb_ratio"] == 1.5
    assert r["dividend_cagr_3y"] > 4
    assert r["rsi"] is not None and r["rsi"] <= 40
    assert r["roe"] == 25.0  # percent passthrough
    assert r["payout_ratio"] == pytest.approx(40.0)
    assert r["fcf_payout_ratio"] == pytest.approx(33.0)
    assert r["dividend_sustainable"] is True
    assert r["financially_healthy"] is True
    assert r["quality_score"] == 100.0
    assert r["composite_score"] > 0
    # Annual statements requested (scanner fiscal-year series)
    client.get_income_statement.assert_called_with("GOOD", period="annual", limit=5)


def test_screen_skips_expensive_multiples(monkeypatch):
    client = _make_client()
    client.get_key_metrics.return_value[0]["peRatio"] = 28.0  # value filter: P/E ≤ 20
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_value_dividend_stocks()

    assert results == []
    client.get_dividend_history.assert_not_called()


def test_screen_skips_low_yield_prefilter(monkeypatch):
    client = _make_client()
    client.get_key_metrics.return_value[0]["dividendYield"] = 1.0  # below 0.7 * 3.0
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_value_dividend_stocks()

    assert results == []
    client.get_dividend_history.assert_not_called()


def test_screen_rejects_low_actual_yield(monkeypatch):
    client = _make_client()
    # Scanner yield passes the pre-filter but exact DPS/price math fails 3.0%
    client.get_key_metrics.return_value[0]["dividendYield"] = 2.9
    client.get_dividend_history.return_value = _annual_dps_history(
        [2.50, 2.27, 2.16, 2.05, 1.93]  # 2.5% actual yield at price 100
    )
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    assert mod.screen_value_dividend_stocks() == []


def test_no_oversold_falls_back_to_lowest_rsi(monkeypatch):
    client = _make_client()
    # 30 rising closes (newest first: highest first) -> RSI near 100
    client.get_historical_prices.return_value = {
        "symbol": "GOOD",
        "historical": [{"close": 130.0 - i} for i in range(30)],
    }
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_value_dividend_stocks()

    # Original FMP behavior preserved: no RSI<=40 names -> lowest-RSI fallback
    assert len(results) == 1
    assert results[0]["rsi"] > 40


def test_finviz_symbols_bypass_sp500(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_value_dividend_stocks(finviz_symbols={"GOOD"})

    assert len(results) == 1
    client.get_sp500_constituents.assert_not_called()
