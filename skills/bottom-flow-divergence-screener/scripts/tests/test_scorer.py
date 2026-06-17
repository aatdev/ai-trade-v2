#!/usr/bin/env python3
"""Unit tests for the pure scoring/classification logic (offline)."""

import math

from scorer import (
    ScoreConfig,
    classify,
    compute_score,
    extract_metrics,
    passes_bottom_gate,
)

from conftest import row_by_symbol

CFG = ScoreConfig()


def test_extract_metrics_derives_proximity(fixture_rows):
    m = extract_metrics(row_by_symbol(fixture_rows, "FOO"))
    assert m["symbol"] == "FOO"  # exchange prefix stripped
    assert math.isclose(m["pct_off_low"], (10.0 - 8.5) / 8.5 * 100, rel_tol=1e-6)
    assert math.isclose(m["pct_off_high"], (20.0 - 10.0) / 20.0 * 100, rel_tol=1e-6)


def test_extract_metrics_handles_nulls(fixture_rows):
    m = extract_metrics(row_by_symbol(fixture_rows, "NULLP"))
    assert m["pct_off_low"] is None
    assert m["rev_ttm"] is None


def test_bottom_gate_accepts_floor(fixture_rows):
    m = extract_metrics(row_by_symbol(fixture_rows, "FOO"))
    ok, reason = passes_bottom_gate(m, CFG)
    assert ok is True and reason == ""


def test_bottom_gate_rejects_far_from_low(fixture_rows):
    m = extract_metrics(row_by_symbol(fixture_rows, "HIGH"))
    ok, reason = passes_bottom_gate(m, CFG)
    assert ok is False and reason == "not_near_low"


def test_bottom_gate_rejects_shallow_drawdown(fixture_rows):
    m = extract_metrics(row_by_symbol(fixture_rows, "SHALLOW"))
    ok, reason = passes_bottom_gate(m, CFG)
    assert ok is False and reason == "not_deep_enough"


def test_bottom_gate_rejects_missing_price(fixture_rows):
    m = extract_metrics(row_by_symbol(fixture_rows, "NULLP"))
    ok, reason = passes_bottom_gate(m, CFG)
    assert ok is False and reason == "missing_price_data"


def test_classify_grade_a_dual_divergence(fixture_rows):
    v = classify(extract_metrics(row_by_symbol(fixture_rows, "FOO")), CFG)
    assert v["grade"] == "A"
    assert v["fundamental_ok"] and v["accumulation_ok"]
    assert v["survivable"] and v["turning"]
    assert "recovering" in v["flow_profile"] and "resilient" in v["flow_profile"]
    assert v["organic_warn"] is False
    assert v["risk_flags"] == []


def test_classify_b_accum_is_speculative(fixture_rows):
    v = classify(extract_metrics(row_by_symbol(fixture_rows, "BAR")), CFG)
    assert v["grade"] == "B-accum"
    assert v["fundamental_ok"] is False and v["accumulation_ok"] is True
    assert v["survivable"] is False
    assert v["turning"] is False
    assert "unprofitable" in v["risk_flags"]
    assert "fcf_negative" in v["risk_flags"]
    assert "low_altman_z" in v["risk_flags"]


def test_classify_b_fund_no_accumulation(fixture_rows):
    v = classify(extract_metrics(row_by_symbol(fixture_rows, "BAZ")), CFG)
    assert v["grade"] == "B-fund"
    assert v["fundamental_ok"] is True and v["accumulation_ok"] is False
    assert v["survivable"] is True


def test_classify_no_divergence_rejected(fixture_rows):
    v = classify(extract_metrics(row_by_symbol(fixture_rows, "DEAD")), CFG)
    assert v["grade"] is None
    assert v["reject_reason"] == "no_divergence"


def test_classify_flags_possible_ma(fixture_rows):
    v = classify(extract_metrics(row_by_symbol(fixture_rows, "MNA")), CFG)
    assert v["grade"] == "A"
    assert v["organic_warn"] is True


def test_compute_score_rewards_survivability_and_turn():
    m = extract_metrics(
        {
            "symbol": "X",
            "close": 10,
            "price_52_week_low": 9,
            "price_52_week_high": 20,
            "total_revenue_yoy_growth_ttm": 10,
            "total_revenue_qoq_growth_fq": 5,
            "free_cash_flow_margin_ttm": 10,
            "ChaikinMoneyFlow": 0.1,
            "MoneyFlow": 60,
            "Perf.3M": 2,
            "Perf.6M": -10,
        }
    )
    base = compute_score(m, survivable=False, turning=False)
    boosted = compute_score(m, survivable=True, turning=True)
    assert boosted == base + 15.0  # +10 survivable, +5 turning
    assert base > 0
