"""TradingView-data-layer contract for dividend-growth-pullback-screener.

The screener's data layer moved from FMP REST to the shared tv_client
(TradingView scanner + chart bars). These tests pin the pieces the migration
changed: annual-DPS dividend math, snapshot-ratio financial health, the
key-metrics payout reconstruction (DPS x shares), and the end-to-end
screening loop against a fully mocked client — no live chart, no keys.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import screen_dividend_growth_rsi as mod  # noqa: E402

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
        # 1.00 -> 1.40 over 3 years ≈ 11.9% CAGR; newest-first input
        hist = _annual_dps_history([1.40, 1.25, 1.10, 1.00, 0.95])
        cagr, consistent, latest, years = mod.StockAnalyzer.analyze_dividend_growth(hist)
        assert cagr == pytest.approx(11.87, abs=0.1)
        assert consistent is True
        assert latest == 1.40
        assert years >= 3

    def test_dividend_cut_breaks_consistency(self):
        hist = _annual_dps_history([1.40, 1.25, 1.50, 1.00])  # cut between years
        _, consistent, _, _ = mod.StockAnalyzer.analyze_dividend_growth(hist)
        assert consistent is False

    def test_too_short_history_rejected(self):
        hist = _annual_dps_history([1.40, 1.25])
        cagr, consistent, latest, years = mod.StockAnalyzer.analyze_dividend_growth(hist)
        assert cagr is None and latest is None


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
        # Parity with the FMP version: absent data does not disqualify.
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


# ----------------------------------------------------------- composite score
def test_composite_score_handles_none_debt_to_equity():
    """Negative-equity names (e.g. DPZ) carry debt_to_equity=None through the
    health check — scoring must not crash on the None comparison."""
    score = mod.StockAnalyzer.calculate_composite_score(
        {
            "dividend_cagr_3y": 16.5,
            "dividend_consistent": True,
            "roe": 0,
            "profit_margin": 12.0,
            "debt_to_equity": None,
            "rsi": 32.0,
            "pe_ratio": 24.0,
            "pb_ratio": 0,
        }
    )
    assert 0 < score <= 100


# ----------------------------------------------------- end-to-end screening
def _make_client():
    """Mock tv_client with one qualifying stock (strong grower, oversold)."""
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
            "peRatio": 18.0,
            "pbRatio": 2.5,
            "roe": 25.0,
            "netProfitMargin": 18.0,
            "payoutRatio": 0.40,
            "debtToEquity": 0.8,
            "currentRatio": 1.4,
            "dividendYield": 2.1,
            "freeCashFlow": 5_000_000_000,
            "operatingCashFlow": 6_000_000_000,
            "sharesOutstanding": 500_000_000,
            "annualDividendPerShare": 2.0,
            "continuousDividendGrowth": 10,
        }
    ]
    client.get_dividend_history.return_value = _annual_dps_history(
        [2.00, 1.74, 1.51, 1.31, 1.14]  # ~15% CAGR
    )
    # 30 falling closes -> RSI well under 40
    client.get_historical_prices.return_value = {
        "symbol": "GOOD",
        "historical": [{"close": 100.0 + i} for i in range(30)],  # newest first, falling
    }
    client.get_income_statement.return_value = [
        {"date": None, "eps": 5.0, "revenue": 1_000},
        {"date": None, "eps": 4.5, "revenue": 950},
        {"date": None, "eps": 4.0, "revenue": 900},
        {"date": None, "eps": 3.5, "revenue": 850},
    ]
    client.rate_limit_reached = False
    return client


def test_screen_qualifies_mocked_stock(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_dividend_growth_pullbacks(min_yield=1.5, min_div_growth=12.0)

    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "GOOD"
    assert r["dividend_yield"] == pytest.approx(2.0)
    assert r["dividend_cagr_3y"] > 12
    assert r["rsi"] is not None and r["rsi"] <= 40
    assert r["roe"] == 25.0  # percent passthrough
    assert r["payout_ratio"] == pytest.approx(40.0)
    assert r["financially_healthy"] is True
    assert r["composite_score"] > 0
    # Annual statements requested (scanner fiscal-year series)
    client.get_income_statement.assert_called_with("GOOD", period="annual", limit=5)


def test_screen_skips_low_yield_prefilter(monkeypatch):
    client = _make_client()
    km = client.get_key_metrics.return_value[0]
    km["dividendYield"] = 0.4  # below 0.7 * min_yield
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_dividend_growth_pullbacks(min_yield=1.5)

    assert results == []
    client.get_dividend_history.assert_not_called()


def test_screen_skips_high_rsi(monkeypatch):
    client = _make_client()
    # 30 rising closes (newest first: highest first) -> RSI near 100
    client.get_historical_prices.return_value = {
        "symbol": "GOOD",
        "historical": [{"close": 130.0 - i} for i in range(30)],
    }
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    assert mod.screen_dividend_growth_pullbacks() == []


def test_finviz_symbols_bypass_sp500(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(mod, "FMPClient", lambda *a, **k: client)

    results = mod.screen_dividend_growth_pullbacks(finviz_symbols={"GOOD"})

    assert len(results) == 1
    client.get_sp500_constituents.assert_not_called()
