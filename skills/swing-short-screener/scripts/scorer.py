#!/usr/bin/env python3
"""
Swing Short Screener - 5-Factor Weakness Scoring Engine

Mirror of the long-side VCP scorer, inverted for the short side. Combines five
weakness components into a weighted composite (0-100) and assigns an A/B/C/D
grade. Higher score == more confirmed Stage 4 downtrend weakness == better
swing-short candidate.

Component Weights:
1. Trend Structure:   30%   (below MA50 & MA200, death cross, MA50 falling)
2. Relative Strength: 25%   (underperformance vs the index)
3. Base Breakdown:    20%   (support broken on expanding volume)
4. Lower Highs:       15%   (descending swing-high structure)
5. Liquidity/Borrow:  10%   (tradable, borrowable, low squeeze risk)
Total: 100%

Grade Bands:
  80-100: A  - Clean Stage 4 weakness, prime short candidate
  65-79:  B  - Strong weakness, tradable
  50-64:  C  - Developing weakness, watchlist
  <50:    D  - Weak signal, skip

State Cap:
  oversold_extended (RSI < 25 or > 20% below MA50) → grade capped at C.
  Shorting a falling knife late invites a mean-reversion bounce; the cap
  flags "weak structurally but too extended to chase down here".
"""

from typing import Optional

COMPONENT_WEIGHTS = {
    "trend_structure": 0.30,
    "relative_strength": 0.25,
    "base_breakdown": 0.20,
    "lower_highs": 0.15,
    "liquidity": 0.10,
}

# Stop buffer above the last swing high, in ATRs — keeps the stop out of
# one-bar noise without ballooning the risk distance.
STOP_ATR_BUFFER = 0.5

# Squeeze proxy thresholds (no short-interest feed — price action only): a short
# being run in invites a mean-reversion bounce, so cap it like a falling knife.
SQUEEZE_MAX_UP_DAY_PCT = 10.0  # any single-day close-to-close pop in last 10 sessions
SQUEEZE_ABOVE_LOW_PCT = 15.0  # rally extent above the 20-session low

COMPONENT_LABELS = {
    "trend_structure": "Trend Structure (Stage 4)",
    "relative_strength": "Relative Strength (underperformance)",
    "base_breakdown": "Base Breakdown on Volume",
    "lower_highs": "Lower-Highs Structure",
    "liquidity": "Liquidity / Borrow Suitability",
}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_trend_structure(m: dict) -> float:
    """Stage 4 trend points: below MA200 (40), death cross (25),
    below MA50 (20), MA50 falling (15)."""
    score = 0.0
    if m.get("below_ma200"):
        score += 40
    if m.get("death_cross"):
        score += 25
    if m.get("below_ma50"):
        score += 20
    if m.get("ma50_falling"):
        score += 15
    return round(score, 1)


def score_relative_strength(stock_return: Optional[float], spy_return: Optional[float]) -> float:
    """Underperformance vs the index over the RS lookback.

    rel = stock_return - spy_return (negative == weaker than index).
    -20% relative underperformance or worse → 100; at-or-above index → 0.
    """
    if stock_return is None or spy_return is None:
        return 0.0
    rel = stock_return - spy_return
    return round(_clamp((-rel / 0.20) * 100), 1)


def score_base_breakdown(m: dict) -> float:
    """Support break (50) + volume expansion on the break (up to 50).

    Volume component rewards distribution even without a clean break, but a
    break with a >=2x volume spike maxes the factor.
    """
    base = 50.0 if m.get("broke_support") else 0.0
    vol_ratio = m.get("vol_ratio", 0.0) or 0.0
    vol_pts = _clamp((vol_ratio - 1.0) * 50, 0, 50)
    return round(_clamp(base + vol_pts), 1)


def score_lower_highs(m: dict) -> float:
    """Recent 20d swing high below the prior 20d swing high.

    A 10% lower high (or more) maxes the factor; a flat/higher high scores 0.
    """
    pct = m.get("lower_high_pct", 0.0) or 0.0
    return round(_clamp((pct / 0.10) * 100), 1)


def score_liquidity(m: dict) -> float:
    """Tradability / borrow suitability. High dollar volume and a normal share
    price reduce squeeze and locate risk.

    >= $50M avg daily dollar volume → 100; >= $10M → 60; >= $3M → 30; else low.
    A sub-$5 price halves the score (low-float squeeze risk).
    """
    adv = m.get("avg_dollar_vol", 0.0) or 0.0
    if adv >= 50_000_000:
        score = 100.0
    elif adv >= 10_000_000:
        score = 60.0
    elif adv >= 3_000_000:
        score = 30.0
    else:
        score = 10.0
    if m.get("price", 0) < 5:
        score *= 0.5
    return round(score, 1)


def _grade(composite: float) -> str:
    if composite >= 80:
        return "A"
    if composite >= 65:
        return "B"
    if composite >= 50:
        return "C"
    return "D"


def _cap_grade_at_c(grade: str) -> str:
    """Downgrade A/B to C; leave C/D unchanged."""
    return "C" if grade in ("A", "B") else grade


def _detect_squeeze(m: dict) -> tuple[bool, Optional[str]]:
    """Price-action squeeze proxy (no short-interest feed). A sharp counter-trend
    pop or a large bounce off the recent low means the short is being run in —
    a poor spot to add short risk. Returns (is_squeeze, human reason)."""
    reasons = []
    max_up = m.get("max_up_day_10")
    above_low = m.get("pct_above_low_20")
    if max_up is not None and max_up >= SQUEEZE_MAX_UP_DAY_PCT:
        reasons.append(f"1-day pop +{max_up:.0f}% in last 10 sessions")
    if above_low is not None and above_low >= SQUEEZE_ABOVE_LOW_PCT:
        reasons.append(f"+{above_low:.0f}% above the 20-session low")
    return (bool(reasons), "; ".join(reasons) if reasons else None)


def score_candidate(
    m: dict, spy_return: Optional[float], sector_info: Optional[dict] = None
) -> dict:
    """Full weakness score for one symbol's metrics dict.

    Returns composite_score, grade, component breakdown, flags, and short
    trade levels (entry / stop / 2R target).
    """
    components = {
        "trend_structure": score_trend_structure(m),
        "relative_strength": score_relative_strength(m.get("stock_return"), spy_return),
        "base_breakdown": score_base_breakdown(m),
        "lower_highs": score_lower_highs(m),
        "liquidity": score_liquidity(m),
    }

    composite = 0.0
    for key, weight in COMPONENT_WEIGHTS.items():
        composite += components[key] * weight
    composite = round(composite, 1)

    raw_grade = _grade(composite)

    # State cap: too extended to chase down (falling-knife) OR being squeezed up
    # (sharp counter-trend pop / big bounce off lows) — both invite a bounce.
    rsi14 = m.get("rsi14")
    pct_below_ma50 = m.get("pct_below_ma50", 0.0) or 0.0
    oversold_extended = (rsi14 is not None and rsi14 < 25) or pct_below_ma50 > 20
    squeeze_risk, squeeze_reason = _detect_squeeze(m)
    sector_info = sector_info or {}
    sector_leadership = sector_info.get("leadership")
    sector_fight = sector_leadership == "leading"  # shorting into a leading sector
    grade = (
        _cap_grade_at_c(raw_grade)
        if (oversold_extended or squeeze_risk or sector_fight)
        else raw_grade
    )
    cap_applied = grade != raw_grade

    # Short trade levels: enter near current price, stop above the most recent
    # LOWER HIGH plus an ATR buffer (plan rule: «стоп НАД последним нижним
    # максимумом»), target 2R below entry. The 20-session absolute max is only
    # a fallback — on a post-crash name it is the pre-crash top, which produced
    # 25%+ stops with unreachable 2R targets.
    entry = m.get("price", 0.0)
    swing = m.get("swing_high_20") or m.get("recent_high_20", entry)
    atr14 = m.get("atr14") or 0.0
    stop = swing + STOP_ATR_BUFFER * atr14
    risk = max(stop - entry, 0.0)
    target = round(entry - 2 * risk, 2)
    stop_pct = round((risk / entry) * 100, 2) if entry else 0.0

    weakest = max(components, key=components.get)  # strongest weakness signal
    laggard = min(components, key=components.get)

    return {
        "composite_score": composite,
        "grade": grade,
        "raw_grade": raw_grade,
        "state_cap_applied": cap_applied,
        "oversold_extended": oversold_extended,
        "squeeze_risk": squeeze_risk,
        "squeeze_reason": squeeze_reason,
        "sector_fight": sector_fight,
        "sector_etf": sector_info.get("etf"),
        "sector_rs": sector_info.get("sector_rs"),
        "sector_leadership": sector_leadership,
        "components": components,
        "strongest_signal": weakest,
        "weakest_signal": laggard,
        "trade_levels": {
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "stop_pct": stop_pct,
            "target_2r": target,
        },
    }
