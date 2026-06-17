#!/usr/bin/env python3
"""
Bottom Flow Divergence Screener - Scoring Engine

Pure, network-free classification of a single scanner row into a
"bottom + flow divergence" candidate. A name qualifies when its PRICE is on the
floor (near the 52-week low and deep below the 52-week high) while a FLOW signal
refuses to confirm that floor:

- Fundamental flow: TTM revenue still growing AND operating cash flow positive
  (the business never broke — HOOD-type).
- Accumulation flow: Chaikin Money Flow > 0 or Money Flow Index >= threshold
  (smart money buying the lows — the contrarian / "MRNA-type" layer).

Grades:
    A        bottom + BOTH divergences (fundamental and accumulation)
    B-accum  bottom + accumulation only (flows weak/negative — speculative)
    B-fund   bottom + fundamental only (no tape accumulation yet)
    None     no divergence, or price not actually on the floor (rejected)

Every record carries informational tags (turning vs falling, recovering vs
resilient, possible-M&A organic warning) and survivability risk flags. The two
optional hard gates (require-turn, require-survivable) are applied downstream in
``screen_bottom_flow.filter_and_rank`` so this module stays purely descriptive.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Canonical scanner field keys (as returned by scanner.tradingview.com) ---
F_SYMBOL = "symbol"
F_CLOSE = "close"
F_LOW_52W = "price_52_week_low"
F_HIGH_52W = "price_52_week_high"
F_PERF_Y = "Perf.Y"
F_PERF_6M = "Perf.6M"
F_PERF_3M = "Perf.3M"
F_RSI = "RSI"
F_REV_TTM = "total_revenue_yoy_growth_ttm"
F_REV_QOQ = "total_revenue_qoq_growth_fq"
F_OCF = "cash_f_operating_activities_ttm"
F_FCF = "free_cash_flow_ttm"
F_FCF_MARGIN = "free_cash_flow_margin_ttm"
F_GROSS_MARGIN = "gross_margin_ttm"
F_OPER_MARGIN = "operating_margin_ttm"
F_NET_INCOME = "net_income_ttm"
F_MFI = "MoneyFlow"
F_CMF = "ChaikinMoneyFlow"
F_ALTMAN_Z = "altman_z_score_ttm"
F_CURRENT_RATIO = "current_ratio_fq"
F_SMA50 = "SMA50"
F_MKT_CAP = "market_cap_basic"
F_AVG_VOL = "average_volume_30d_calc"

# Columns the live scan must request (order is preserved into the response "d").
SCAN_COLUMNS = [
    F_CLOSE,
    F_LOW_52W,
    F_HIGH_52W,
    F_PERF_Y,
    F_PERF_6M,
    F_PERF_3M,
    F_RSI,
    F_REV_TTM,
    F_REV_QOQ,
    F_OCF,
    F_FCF,
    F_FCF_MARGIN,
    F_GROSS_MARGIN,
    F_OPER_MARGIN,
    F_NET_INCOME,
    F_MFI,
    F_CMF,
    F_ALTMAN_Z,
    F_CURRENT_RATIO,
    F_SMA50,
    F_MKT_CAP,
    F_AVG_VOL,
]

GRADES = ("A", "B-accum", "B-fund")
GRADE_ORDER = {"A": 0, "B-accum": 1, "B-fund": 2}

GRADE_GUIDANCE = {
    "A": "Bottom + dual divergence — flows healthy AND tape accumulating. Prime reversal candidate.",
    "B-accum": "Bottom + accumulation only — smart money buying weak/negative fundamentals (contrarian, speculative).",
    "B-fund": "Bottom + fundamentals holding — business intact, but no tape accumulation yet.",
}


@dataclass
class ScoreConfig:
    """Tunable thresholds for the bottom + divergence model."""

    near_low_pct: float = 25.0  # close must sit within this % above the 52w low
    min_drawdown_pct: float = 35.0  # close must be at least this % below the 52w high
    rev_ttm_min: float = 0.0  # TTM revenue YoY growth floor (fundamental "not falling")
    mfi_min: float = 50.0  # Money Flow Index accumulation threshold
    qoq_recover_min: float = 5.0  # sequential revenue growth -> "recovering" tag
    resilient_ttm_min: float = 15.0  # steady high TTM growth -> "resilient" tag
    organic_warn_ttm: float = 50.0  # TTM growth above this -> possible M&A flag
    organic_warn_qoq: float = 40.0  # QoQ growth above this -> possible M&A flag
    altman_safe: float = 3.0  # Altman Z above this counts toward survivability
    current_ratio_safe: float = 1.5  # current ratio above this counts toward survivability


def _num(row: dict, key: str) -> float | None:
    """Return a numeric field as float, or None when missing / non-numeric."""
    value = row.get(key)
    if isinstance(value, bool):  # guard: bools are ints in Python
        return None
    return float(value) if isinstance(value, (int, float)) else None


def extract_metrics(row: dict) -> dict:
    """Flatten one scanner row into the metrics the model needs (None-safe)."""
    symbol = row.get(F_SYMBOL, "")
    if ":" in symbol:
        symbol = symbol.split(":", 1)[1]
    close = _num(row, F_CLOSE)
    low = _num(row, F_LOW_52W)
    high = _num(row, F_HIGH_52W)
    pct_off_low = ((close - low) / low * 100) if (close is not None and low and low > 0) else None
    pct_off_high = (
        ((high - close) / high * 100) if (close is not None and high and high > 0) else None
    )
    return {
        "symbol": symbol,
        "close": close,
        "low_52w": low,
        "high_52w": high,
        "pct_off_low": pct_off_low,
        "pct_off_high": pct_off_high,
        "perf_y": _num(row, F_PERF_Y),
        "perf_6m": _num(row, F_PERF_6M),
        "perf_3m": _num(row, F_PERF_3M),
        "rsi": _num(row, F_RSI),
        "rev_ttm": _num(row, F_REV_TTM),
        "rev_qoq": _num(row, F_REV_QOQ),
        "ocf": _num(row, F_OCF),
        "fcf": _num(row, F_FCF),
        "fcf_margin": _num(row, F_FCF_MARGIN),
        "gross_margin": _num(row, F_GROSS_MARGIN),
        "oper_margin": _num(row, F_OPER_MARGIN),
        "net_income": _num(row, F_NET_INCOME),
        "mfi": _num(row, F_MFI),
        "cmf": _num(row, F_CMF),
        "altman_z": _num(row, F_ALTMAN_Z),
        "current_ratio": _num(row, F_CURRENT_RATIO),
        "sma50": _num(row, F_SMA50),
        "mkt_cap": _num(row, F_MKT_CAP),
        "avg_vol": _num(row, F_AVG_VOL),
    }


def passes_bottom_gate(m: dict, cfg: ScoreConfig) -> tuple[bool, str]:
    """The hard 'is this actually on the floor?' gate, applied before grading."""
    if m["pct_off_low"] is None or m["pct_off_high"] is None:
        return False, "missing_price_data"
    if m["pct_off_low"] > cfg.near_low_pct:
        return False, "not_near_low"
    if m["pct_off_high"] < cfg.min_drawdown_pct:
        return False, "not_deep_enough"
    return True, ""


def _fundamental_ok(m: dict, cfg: ScoreConfig) -> bool:
    """Business never broke: TTM revenue still growing AND operating cash flow > 0."""
    return (
        m["rev_ttm"] is not None
        and m["rev_ttm"] > cfg.rev_ttm_min
        and m["ocf"] is not None
        and m["ocf"] > 0
    )


def _accumulation_ok(m: dict, cfg: ScoreConfig) -> bool:
    """Tape accumulation: Chaikin Money Flow > 0 OR Money Flow Index >= threshold."""
    cmf, mfi = m["cmf"], m["mfi"]
    return (cmf is not None and cmf > 0) or (mfi is not None and mfi >= cfg.mfi_min)


def _is_survivable(m: dict, cfg: ScoreConfig) -> bool:
    """Can the name survive long enough to revert? Any one signal suffices."""
    checks = (
        (m["net_income"] is not None and m["net_income"] > 0),
        (m["fcf"] is not None and m["fcf"] > 0),
        (m["altman_z"] is not None and m["altman_z"] > cfg.altman_safe),
        (m["current_ratio"] is not None and m["current_ratio"] > cfg.current_ratio_safe),
    )
    return any(checks)


def _is_turning(m: dict) -> bool:
    """Early reversal confirmation: 3-month perf >= 0 OR price back above SMA50."""
    if m["perf_3m"] is not None and m["perf_3m"] >= 0:
        return True
    if m["close"] is not None and m["sma50"] is not None and m["close"] > m["sma50"]:
        return True
    return False


def _risk_flags(m: dict, cfg: ScoreConfig) -> list[str]:
    flags = []
    if m["net_income"] is not None and m["net_income"] <= 0:
        flags.append("unprofitable")
    if m["fcf"] is not None and m["fcf"] <= 0:
        flags.append("fcf_negative")
    if m["altman_z"] is not None and m["altman_z"] < cfg.altman_safe:
        flags.append("low_altman_z")
    return flags


def _flow_profile(m: dict, cfg: ScoreConfig) -> list[str]:
    """Distinguish 'recovering' (QoQ re-accelerating) from 'resilient' (high steady TTM)."""
    tags = []
    if m["rev_qoq"] is not None and m["rev_qoq"] >= cfg.qoq_recover_min:
        tags.append("recovering")
    if m["rev_ttm"] is not None and m["rev_ttm"] >= cfg.resilient_ttm_min:
        tags.append("resilient")
    return tags


def _organic_warn(m: dict, cfg: ScoreConfig) -> bool:
    """Flag suspiciously high growth (likely M&A, not organic) for manual review."""
    return (m["rev_ttm"] is not None and m["rev_ttm"] > cfg.organic_warn_ttm) or (
        m["rev_qoq"] is not None and m["rev_qoq"] > cfg.organic_warn_qoq
    )


def compute_score(m: dict, survivable: bool, turning: bool) -> float:
    """Composite rank score (higher = stronger divergence). See references for rationale."""
    rev = m["rev_ttm"] or 0.0
    qoq = m["rev_qoq"] or 0.0
    fcf_margin = m["fcf_margin"] or 0.0
    cmf = m["cmf"] or 0.0
    mfi = m["mfi"]
    p3, p6 = m["perf_3m"], m["perf_6m"]
    decel = (p3 - p6) if (p3 is not None and p6 is not None) else 0.0
    score = (
        min(max(rev, 0.0), 60.0) * 0.6  # flow strength, capped (M&A outliers)
        + max(qoq, 0.0) * 1.2  # recent sequential re-acceleration weighted up
        + max(fcf_margin, 0.0) * 0.5  # cash-generative quality
        + 25.0 * max(cmf, 0.0)  # Chaikin accumulation (-1..1 scaled)
        + max((mfi - 50.0) if mfi is not None else 0.0, 0.0) * 0.6  # MFI buying pressure
        + max(decel, 0.0) * 0.3  # decline decelerating = bottoming
    )
    if survivable:
        score += 10.0
    if turning:
        score += 5.0
    return round(score, 1)


def classify(m: dict, cfg: ScoreConfig) -> dict:
    """Grade + tag a row that has already passed the bottom gate."""
    fund_ok = _fundamental_ok(m, cfg)
    accum_ok = _accumulation_ok(m, cfg)
    if fund_ok and accum_ok:
        grade: str | None = "A"
    elif accum_ok:
        grade = "B-accum"
    elif fund_ok:
        grade = "B-fund"
    else:
        grade = None

    survivable = _is_survivable(m, cfg)
    turning = _is_turning(m)
    return {
        "grade": grade,
        "fundamental_ok": fund_ok,
        "accumulation_ok": accum_ok,
        "survivable": survivable,
        "turning": turning,
        "flow_profile": _flow_profile(m, cfg),
        "organic_warn": _organic_warn(m, cfg),
        "risk_flags": _risk_flags(m, cfg),
        "score": compute_score(m, survivable, turning),
        "reject_reason": "" if grade else "no_divergence",
    }
