#!/usr/bin/env python3
"""
Swing Short Screener - Weakness Metric Calculations

Pure functions that turn a daily OHLCV history (most-recent-first, the shape
FMP returns under the ``historical`` key) into the raw measurements the scorer
needs. No network, no I/O — every function is deterministic and unit-testable.

Bar shape: ``{"date", "open", "high", "low", "close", "volume"}``.
All series are MOST-RECENT-FIRST (index 0 == latest session).
"""

from typing import Optional


def _closes(bars: list[dict]) -> list[float]:
    return [float(b["close"]) for b in bars]


def _highs(bars: list[dict]) -> list[float]:
    return [float(b["high"]) for b in bars]


def _lows(bars: list[dict]) -> list[float]:
    return [float(b["low"]) for b in bars]


def _volumes(bars: list[dict]) -> list[float]:
    return [float(b.get("volume", 0) or 0) for b in bars]


def sma(values: list[float], period: int, offset: int = 0) -> Optional[float]:
    """Simple moving average over ``period`` values starting at ``offset``.

    ``values`` is most-recent-first, so ``offset=0`` is the latest SMA and a
    positive offset walks back in time. Returns None if not enough data.
    """
    window = values[offset : offset + period]
    if len(window) < period:
        return None
    return sum(window) / period


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI on a most-recent-first close series."""
    if len(closes) < period + 1:
        return None
    # Reverse to chronological for the classic gain/loss walk.
    chrono = closes[: period + 1][::-1]
    gains = 0.0
    losses = 0.0
    for i in range(1, len(chrono)):
        delta = chrono[i] - chrono[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def pct_return(closes: list[float], period: int) -> Optional[float]:
    """Fractional return over ``period`` sessions (e.g. 0.05 == +5%)."""
    if len(closes) < period + 1:
        return None
    latest = closes[0]
    past = closes[period]
    if past == 0:
        return None
    return (latest - past) / past


def atr(bars: list[dict], period: int = 14) -> Optional[float]:
    """Average True Range over ``period`` sessions (most-recent-first bars).

    TR = max(high − low, |high − prev_close|, |low − prev_close|); simple
    average. Returns None when there is not enough history.
    """
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(period):
        high = float(bars[i]["high"])
        low = float(bars[i]["low"])
        prev_close = float(bars[i + 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / period


def last_swing_high(highs: list[float], k: int = 2, lookback: int = 20) -> Optional[float]:
    """Most recent swing high within ``lookback`` sessions (most-recent-first).

    A swing high is a bar strictly above its ``k`` more-recent neighbours and
    at-or-above its ``k`` older neighbours — the "last lower high" a short stop
    belongs above. Returns None when the window has no local maximum (e.g. a
    steady decline with no bounce yet).
    """
    n = min(len(highs), lookback)
    for i in range(k, n - k):
        center = highs[i]
        more_recent = highs[i - k : i]
        older = highs[i + 1 : i + 1 + k]
        if all(center > h for h in more_recent) and all(center >= h for h in older):
            return center
    return None


def compute_metrics(bars: list[dict], rs_lookback: int = 63) -> Optional[dict]:
    """Compute every weakness measurement for one symbol.

    Returns None when there is not enough history (need >= 200 sessions for a
    meaningful MA200). The returned dict feeds both the hard-invalidation
    filter and the 5-factor scorer.
    """
    if len(bars) < 200:
        return None

    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)

    price = closes[0]
    ma50 = sma(closes, 50)
    ma200 = sma(closes, 200)
    ma50_prev = sma(closes, 50, offset=10)  # MA50 ~2 weeks ago for slope
    if ma50 is None or ma200 is None or ma50_prev is None:
        return None

    # Volume: today vs trailing 20-session average (excluding today).
    avg_vol_20 = sma(vols, 20, offset=1) or 0.0
    vol_ratio = (vols[0] / avg_vol_20) if avg_vol_20 > 0 else 0.0
    avg_dollar_vol = (
        (sum(closes[i] * vols[i] for i in range(20)) / 20) if len(bars) >= 20 else price * vols[0]
    )

    # Support breakdown: did the latest close break the prior 20-session low?
    prior_low_20 = min(lows[1:21]) if len(lows) >= 21 else min(lows[1:])
    broke_support = price < prior_low_20

    # Lower-highs structure: recent 20d swing high vs the prior 20d swing high.
    recent_high = max(highs[0:20]) if len(highs) >= 20 else max(highs)
    prior_high = max(highs[20:40]) if len(highs) >= 40 else recent_high
    lower_high_pct = ((prior_high - recent_high) / prior_high) if prior_high > 0 else 0.0

    pct_below_ma50 = ((ma50 - price) / ma50 * 100) if ma50 > 0 else 0.0

    atr14 = atr(bars, 14)
    swing_high = last_swing_high(highs, k=2, lookback=20)

    return {
        "price": round(price, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "ma50_prev": round(ma50_prev, 2),
        "below_ma50": price < ma50,
        "below_ma200": price < ma200,
        "death_cross": ma50 < ma200,
        "ma50_falling": ma50 < ma50_prev,
        "rsi14": rsi(closes, 14),
        "stock_return": pct_return(closes, rs_lookback),
        "vol_ratio": round(vol_ratio, 2),
        "avg_dollar_vol": round(avg_dollar_vol, 0),
        "prior_low_20": round(prior_low_20, 2),
        "broke_support": broke_support,
        "recent_high_20": round(recent_high, 2),
        "prior_high_20_40": round(prior_high, 2),
        "lower_high_pct": round(lower_high_pct, 4),
        "pct_below_ma50": round(pct_below_ma50, 2),
        "atr14": round(atr14, 2) if atr14 is not None else None,
        "swing_high_20": round(swing_high, 2) if swing_high is not None else None,
        "bars_available": len(bars),
    }
