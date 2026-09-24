"""Tests for heat_calculator.py - Theme Heat Score (0-100)"""

import pytest
from calculators.heat_calculator import (
    breadth_signal_score,
    calculate_theme_heat,
    momentum_strength_score,
    uptrend_signal_score,
    volume_intensity_score,
)

# ── momentum_strength_score ──────────────────────────────────────────


class TestMomentumStrengthScore:
    """v2 log-sigmoid: 100 / (1 + exp(-2 * (ln(1 + |wr|) - ln(16))))
    == 100 / (1 + (16 / (1 + |wr|))**2); midpoint 50 at |wr| = 15%."""

    def test_zero_return(self):
        assert momentum_strength_score(0.0) == pytest.approx(100 / 257, abs=1e-6)

    def test_five_percent(self):
        assert momentum_strength_score(5.0) == pytest.approx(100 / (1 + (16 / 6) ** 2), abs=1e-6)

    def test_negative_five_percent(self):
        assert momentum_strength_score(-5.0) == pytest.approx(momentum_strength_score(5.0))

    def test_fifteen_percent(self):
        assert momentum_strength_score(15.0) == pytest.approx(50.0, abs=1e-9)

    def test_twenty_percent(self):
        assert momentum_strength_score(20.0) == pytest.approx(100 / (1 + (16 / 21) ** 2), abs=1e-6)

    def test_negative_twenty_percent(self):
        assert momentum_strength_score(-20.0) == pytest.approx(momentum_strength_score(20.0))

    def test_monotonic_in_absolute_return(self):
        vals = [momentum_strength_score(x) for x in (0, 5, 15, 30, 50)]
        assert vals == sorted(vals)

    def test_returns_float(self):
        assert isinstance(momentum_strength_score(3.0), float)


# ── volume_intensity_score ───────────────────────────────────────────


class TestVolumeIntensityScore:
    """v2 sqrt scaling: min(100, sqrt(max(0, ratio - 0.8)) / sqrt(1.2) * 100)."""

    def test_ratio_0_8_returns_zero(self):
        assert volume_intensity_score(80.0, 100.0) == pytest.approx(0.0)

    def test_ratio_1_0(self):
        assert volume_intensity_score(100.0, 100.0) == pytest.approx((0.2**0.5) / (1.2**0.5) * 100)

    def test_ratio_1_2(self):
        assert volume_intensity_score(120.0, 100.0) == pytest.approx((0.4**0.5) / (1.2**0.5) * 100)

    def test_ratio_2_0_hits_ceiling(self):
        assert volume_intensity_score(200.0, 100.0) == pytest.approx(100.0)

    def test_ratio_above_cap_clamped_to_100(self):
        assert volume_intensity_score(300.0, 100.0) == pytest.approx(100.0)

    def test_ratio_below_floor_clamped_to_0(self):
        assert volume_intensity_score(50.0, 100.0) == pytest.approx(0.0)

    def test_none_vol_20d(self):
        assert volume_intensity_score(None, 100.0) == pytest.approx(50.0)

    def test_none_vol_60d(self):
        assert volume_intensity_score(100.0, None) == pytest.approx(50.0)

    def test_zero_vol_60d(self):
        assert volume_intensity_score(100.0, 0.0) == pytest.approx(50.0)


# ── uptrend_signal_score ─────────────────────────────────────────────


class TestUptrendSignalScore:
    """v2 continuous: base = min(80, ratio * 100) + 10 (ratio > ma_10) + 10 (slope > 0)."""

    def _make_sector(self, ratio, ma_10, slope, weight=1.0):
        return {
            "sector": "test",
            "ratio": ratio,
            "ma_10": ma_10,
            "slope": slope,
            "weight": weight,
        }

    def test_both_bonuses(self):
        data = [self._make_sector(ratio=0.5, ma_10=0.4, slope=0.5)]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(70.0)

    def test_ma_bonus_only(self):
        data = [self._make_sector(ratio=0.5, ma_10=0.4, slope=-0.1)]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(60.0)

    def test_slope_bonus_only(self):
        data = [self._make_sector(ratio=0.3, ma_10=0.4, slope=0.5)]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(40.0)

    def test_no_bonus(self):
        data = [self._make_sector(ratio=0.3, ma_10=0.4, slope=-0.1)]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(30.0)

    def test_base_capped_at_80(self):
        data = [self._make_sector(ratio=0.95, ma_10=0.9, slope=0.1)]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(100.0)

    def test_weighted_average(self):
        # A: 70 (weight 2), B: 30 (weight 1) -> (140 + 30) / 3
        data = [
            self._make_sector(ratio=0.5, ma_10=0.4, slope=0.5, weight=2.0),
            self._make_sector(ratio=0.3, ma_10=0.4, slope=-0.1, weight=1.0),
        ]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(170 / 3)

    def test_bearish_inversion(self):
        data = [self._make_sector(ratio=0.5, ma_10=0.4, slope=0.5)]
        assert uptrend_signal_score(data, is_bearish=True) == pytest.approx(30.0)

    def test_empty_list(self):
        assert uptrend_signal_score([], is_bearish=False) == pytest.approx(50.0)

    def test_equal_ratio_and_ma10(self):
        # ratio == ma_10 -> no MA bonus; slope 0 -> no slope bonus
        data = [self._make_sector(ratio=0.4, ma_10=0.4, slope=0)]
        assert uptrend_signal_score(data, is_bearish=False) == pytest.approx(40.0)


# ── breadth_signal_score ─────────────────────────────────────────────


class TestBreadthSignalScore:
    """v2 power curve: min(100, ratio**2.5 * 80 + min(20, industry_count * 2))."""

    def test_zero(self):
        assert breadth_signal_score(0.0) == pytest.approx(0.0)

    def test_half(self):
        assert breadth_signal_score(0.5) == pytest.approx(0.5**2.5 * 80)

    def test_full(self):
        assert breadth_signal_score(1.0) == pytest.approx(80.0)

    def test_industry_count_bonus(self):
        assert breadth_signal_score(1.0, industry_count=15) == pytest.approx(100.0)
        assert breadth_signal_score(0.5, industry_count=3) == pytest.approx(0.5**2.5 * 80 + 6)

    def test_above_one_clamped(self):
        assert breadth_signal_score(1.5) == pytest.approx(100.0)

    def test_negative_clamped(self):
        assert breadth_signal_score(-0.3) == pytest.approx(0.0)

    def test_none(self):
        assert breadth_signal_score(None) == pytest.approx(50.0)


# ── calculate_theme_heat ─────────────────────────────────────────────


class TestCalculateThemeHeat:
    def test_weighted_sum(self):
        # weights momentum .35 / volume .20 / uptrend .25 / breadth .20
        # 80*0.35 + 60*0.20 + 70*0.25 + 50*0.20 = 28 + 12 + 17.5 + 10 = 67.5
        result = calculate_theme_heat(80.0, 60.0, 70.0, 50.0)
        assert result == pytest.approx(67.5)

    def test_all_100(self):
        result = calculate_theme_heat(100.0, 100.0, 100.0, 100.0)
        assert result == pytest.approx(100.0)

    def test_all_zero(self):
        result = calculate_theme_heat(0.0, 0.0, 0.0, 0.0)
        assert result == pytest.approx(0.0)

    def test_none_defaults_to_50(self):
        # All None => 50*0.40 + 50*0.25 + 50*0.20 + 50*0.15 = 50
        result = calculate_theme_heat(None, None, None, None)
        assert result == pytest.approx(50.0)

    def test_partial_none(self):
        # 80*0.35 + 50*0.20 + 50*0.25 + 50*0.20 = 28 + 10 + 12.5 + 10 = 60.5
        result = calculate_theme_heat(80.0, None, None, None)
        assert result == pytest.approx(60.5)

    def test_clamped_above_100(self):
        result = calculate_theme_heat(200.0, 200.0, 200.0, 200.0)
        assert result == 100.0

    def test_clamped_below_0(self):
        result = calculate_theme_heat(-50.0, -50.0, -50.0, -50.0)
        assert result == 0.0

    def test_returns_float(self):
        assert isinstance(calculate_theme_heat(50, 50, 50, 50), float)


# ── uptrend_signal_score with None values ────────────────────────────


class TestUptrendSignalNoneValues:
    """Ensure None values in sector_data don't cause TypeError."""

    def test_none_ma_10(self):
        """ma_10=None should not crash (treated as 0)."""
        data = [{"sector": "Tech", "ratio": 0.5, "ma_10": None, "slope": 0.01, "weight": 1.0}]
        score = uptrend_signal_score(data, is_bearish=False)
        # base 50 + MA bonus (0.5 > 0) + slope bonus = 70
        assert score == pytest.approx(70.0)

    def test_none_slope(self):
        """slope=None should not crash (treated as 0)."""
        data = [{"sector": "Tech", "ratio": 0.5, "ma_10": 0.3, "slope": None, "weight": 1.0}]
        score = uptrend_signal_score(data, is_bearish=False)
        # base 50 + MA bonus, no slope bonus = 60
        assert score == pytest.approx(60.0)

    def test_none_ratio(self):
        """ratio=None should not crash (treated as 0)."""
        data = [{"sector": "Tech", "ratio": None, "ma_10": 0.3, "slope": 0.01, "weight": 1.0}]
        score = uptrend_signal_score(data, is_bearish=False)
        # base 0 + slope bonus only = 10
        assert score == pytest.approx(10.0)

    def test_all_none(self):
        """All values None should not crash."""
        data = [{"sector": "Tech", "ratio": None, "ma_10": None, "slope": None, "weight": 1.0}]
        score = uptrend_signal_score(data, is_bearish=False)
        # base 0, no bonuses = 0
        assert score == pytest.approx(0.0)
