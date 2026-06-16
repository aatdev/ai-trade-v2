"""Tests for the fundamental_gate module (no network — calculator outputs injected).

The soft quality-floor consumes ``calculate_quarterly_growth`` /
``calculate_annual_growth`` result dicts, so these tests hand-build those shapes
and never touch TradingView or the canslim calculators.
"""

from __future__ import annotations

from fundamental_gate import (
    GATE_BLOCKED,
    GATE_PASS,
    GATE_UNKNOWN,
    build_fundamental_fields,
    classify_fundamentals,
)


def _q(eps_yoy, rev_yoy, latest_eps, score=80, error=None):
    """A calculate_quarterly_growth-shaped result."""
    return {
        "score": score,
        "latest_qtr_eps_growth": eps_yoy,
        "latest_qtr_revenue_growth": rev_yoy,
        "latest_eps": latest_eps,
        "error": error,
    }


def _a(score=70):
    """A calculate_annual_growth-shaped result."""
    return {"score": score, "eps_cagr_3yr": 30.0, "error": None}


class TestClassifyFundamentals:
    def test_healthy_growth_passes_and_annotates(self):
        result = classify_fundamentals(_q(25.0, 12.0, 1.40, score=60), _a(score=70))
        assert result["fundamental_gate"] == GATE_PASS
        assert result["fundamental_reason"] is None
        assert result["eps_growth_yoy"] == 25.0
        assert result["revenue_growth_yoy"] == 12.0
        assert result["c_score"] == 60
        assert result["a_score"] == 70

    def test_negative_latest_eps_blocked(self):
        result = classify_fundamentals(_q(5.0, 8.0, -0.30), None)
        assert result["fundamental_gate"] == GATE_BLOCKED
        assert "EPS negative" in result["fundamental_reason"]
        # still annotates the raw figures
        assert result["latest_eps"] == -0.30

    def test_both_yoy_negative_blocked(self):
        result = classify_fundamentals(_q(-12.0, -4.0, 0.80), _a())
        assert result["fundamental_gate"] == GATE_BLOCKED
        assert "both negative" in result["fundamental_reason"]

    def test_eps_negative_but_revenue_positive_passes(self):
        # Floor needs BOTH shrinking; a soft EPS dip with rising revenue stays.
        result = classify_fundamentals(_q(-8.0, 6.0, 0.90), _a())
        assert result["fundamental_gate"] == GATE_PASS

    def test_turnaround_to_profit_passes(self):
        # year-ago EPS <= 0 -> calculator caps growth at 999.9; latest EPS > 0.
        result = classify_fundamentals(_q(999.9, 30.0, 0.50), _a())
        assert result["fundamental_gate"] == GATE_PASS

    def test_zero_latest_eps_not_blocked(self):
        # 0 is not < 0; breakeven is not "clear decay".
        result = classify_fundamentals(_q(2.0, 1.0, 0.0), _a())
        assert result["fundamental_gate"] == GATE_PASS

    def test_missing_quarterly_is_unknown(self):
        result = classify_fundamentals(None, _a())
        assert result["fundamental_gate"] == GATE_UNKNOWN
        assert result["c_score"] is None

    def test_calculator_error_is_unknown(self):
        bad = _q(None, None, None, score=0, error="Insufficient quarterly data")
        result = classify_fundamentals(bad, None)
        assert result["fundamental_gate"] == GATE_UNKNOWN

    def test_eps_negative_with_revenue_unknown_passes(self):
        # revenue None -> cannot confirm "both negative" -> fail-open to pass.
        result = classify_fundamentals(_q(-10.0, None, 0.5), None)
        assert result["fundamental_gate"] == GATE_PASS


class TestBuildFundamentalFields:
    def test_fetch_failed_is_unknown(self):
        result = build_fundamental_fields("AAPL", {}, fetch_failed=True)
        assert result["fundamental_gate"] == GATE_UNKNOWN
        assert result["fundamental_reason"] == "fundamentals fetch failed"

    def test_symbol_absent_is_unknown(self):
        result = build_fundamental_fields("AAPL", {"MSFT": {"quarterly": _q(20, 10, 1.0)}})
        assert result["fundamental_gate"] == GATE_UNKNOWN

    def test_symbol_present_classifies(self):
        fmap = {"NVDA": {"quarterly": _q(50.0, 30.0, 2.0, score=100), "annual": _a(90)}}
        result = build_fundamental_fields("NVDA", fmap)
        assert result["fundamental_gate"] == GATE_PASS
        assert result["c_score"] == 100
        assert result["a_score"] == 90

    def test_symbol_lookup_is_case_insensitive(self):
        fmap = {"NVDA": {"quarterly": _q(-20.0, -10.0, 0.5), "annual": None}}
        result = build_fundamental_fields("nvda", fmap)
        assert result["fundamental_gate"] == GATE_BLOCKED
