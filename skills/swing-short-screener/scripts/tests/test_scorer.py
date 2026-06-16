#!/usr/bin/env python3
"""Tests for the 5-factor weakness scorer."""

from scorer import (
    COMPONENT_WEIGHTS,
    score_base_breakdown,
    score_candidate,
    score_liquidity,
    score_relative_strength,
    score_trend_structure,
)


def _clean_stage4_metrics(**overrides):
    """A liquid, clearly-weak-but-not-oversold candidate."""
    m = {
        "price": 140.0,
        "ma50": 150.0,
        "ma200": 175.0,
        "below_ma50": True,
        "below_ma200": True,
        "death_cross": True,
        "ma50_falling": True,
        "rsi14": 38.0,
        "stock_return": -0.18,
        "vol_ratio": 2.4,
        "avg_dollar_vol": 80_000_000,
        "broke_support": True,
        "recent_high_20": 152.0,
        "prior_high_20_40": 168.0,
        "lower_high_pct": 0.095,
        "pct_below_ma50": 6.7,
    }
    m.update(overrides)
    return m


def test_weights_sum_to_one():
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


def test_trend_structure_full_marks():
    assert score_trend_structure(_clean_stage4_metrics()) == 100.0


def test_trend_structure_partial():
    m = _clean_stage4_metrics(death_cross=False, ma50_falling=False)
    # below_ma200 (40) + below_ma50 (20) = 60
    assert score_trend_structure(m) == 60.0


def test_relative_strength_underperformance():
    # stock -20% vs index +0% → rel -0.20 → max score
    assert score_relative_strength(-0.20, 0.0) == 100.0
    # outperforming the index → 0
    assert score_relative_strength(0.05, 0.0) == 0.0


def test_base_breakdown_volume_component():
    full = score_base_breakdown(_clean_stage4_metrics(broke_support=True, vol_ratio=2.0))
    assert full == 100.0  # 50 (break) + 50 (2x volume)
    none = score_base_breakdown(_clean_stage4_metrics(broke_support=False, vol_ratio=1.0))
    assert none == 0.0


def test_liquidity_subprice_penalty():
    base = score_liquidity(_clean_stage4_metrics(avg_dollar_vol=80_000_000, price=140))
    penalized = score_liquidity(_clean_stage4_metrics(avg_dollar_vol=80_000_000, price=4))
    assert penalized == base * 0.5


def test_clean_stage4_scores_grade_a():
    result = score_candidate(_clean_stage4_metrics(), spy_return=0.0)
    assert result["grade"] == "A"
    assert result["composite_score"] >= 80
    assert result["state_cap_applied"] is False
    # short trade levels: stop above entry, target below
    tl = result["trade_levels"]
    assert tl["stop"] > tl["entry"] > tl["target_2r"]


def test_oversold_extended_caps_grade_at_c():
    m = _clean_stage4_metrics(rsi14=18.0, pct_below_ma50=28.0)
    result = score_candidate(m, spy_return=0.0)
    assert result["oversold_extended"] is True
    assert result["state_cap_applied"] is True
    assert result["grade"] == "C"
    assert result["raw_grade"] in ("A", "B")


def test_squeeze_pop_caps_grade_at_c():
    # A sharp counter-trend 1-day pop on an otherwise clean Stage 4 short.
    m = _clean_stage4_metrics(max_up_day_10=14.0)
    result = score_candidate(m, spy_return=0.0)
    assert result["squeeze_risk"] is True
    assert "pop" in (result["squeeze_reason"] or "")
    assert result["state_cap_applied"] is True
    assert result["grade"] == "C"
    assert result["raw_grade"] in ("A", "B")


def test_squeeze_bounce_off_low_caps_grade_at_c():
    # Price has rallied far above its 20-session low — being run in.
    m = _clean_stage4_metrics(pct_above_low_20=22.0)
    result = score_candidate(m, spy_return=0.0)
    assert result["squeeze_risk"] is True
    assert result["grade"] == "C"


def test_no_squeeze_when_price_action_is_quiet():
    m = _clean_stage4_metrics(max_up_day_10=4.0, pct_above_low_20=6.0)
    result = score_candidate(m, spy_return=0.0)
    assert result["squeeze_risk"] is False
    assert result["grade"] == "A"


def test_stop_uses_swing_high_plus_atr_buffer_not_20d_max():
    # ADBE-like post-crash shape: the 20d absolute max (275) is the pre-crash
    # top; the relevant lower high is the recent bounce (222). The stop must
    # sit just above the lower high, not 26% away at the old top.
    m = _clean_stage4_metrics(price=218.8, recent_high_20=275.44, swing_high_20=222.0, atr14=4.0)
    tl = score_candidate(m, spy_return=0.0)["trade_levels"]
    assert tl["stop"] == 224.0  # 222 + 0.5 × ATR(4)
    assert tl["stop_pct"] < 5
    assert tl["target_2r"] == round(218.8 - 2 * (224.0 - 218.8), 2)


def test_stop_falls_back_to_20d_max_without_swing_high():
    m = _clean_stage4_metrics(swing_high_20=None, atr14=None)
    tl = score_candidate(m, spy_return=0.0)["trade_levels"]
    assert tl["stop"] == 152.0  # recent_high_20, no ATR buffer available


def test_strong_market_lowers_relative_strength():
    # Same weak stock, but index ripped +20% → underperformance even larger,
    # RS already maxed; confirm scorer still produces a valid grade.
    result = score_candidate(_clean_stage4_metrics(), spy_return=0.20)
    assert result["components"]["relative_strength"] == 100.0
