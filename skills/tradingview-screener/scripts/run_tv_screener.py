#!/usr/bin/env python3
"""Screen stocks via the TradingView Stock Screener (All Stocks tab) scanner API.

Queries https://scanner.tradingview.com/<market>/scan — the same endpoint the
TradingView "All stocks" screener tab uses — with JSON filter expressions built
from compact CLI tokens. No API key or authentication required.

Usage:
    python3 run_tv_screener.py --filters "mkt_cap>10B,pe<20,div_yield=3..8" --output-dir reports/
    python3 run_tv_screener.py --filters "rsi<30,close>EMA200" --columns technicals --limit 30
    python3 run_tv_screener.py --index sp500 --sectors "Finance,Utilities" --sort -div_yield
    python3 run_tv_screener.py --filters "pe<15" --dry-run        # print payload, no network

Filter token syntax (comma-separated in --filters):
    field>value   field>=value   field<value   field<=value
    field=value   field!=value   field=low..high   field=A|B|C
    Right side may reference another field (e.g. close>EMA200).
    Value suffixes: K, M, B, T (e.g. 300M, 1.5B); trailing % is stripped.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCAN_URL_TEMPLATE = "https://scanner.tradingview.com/{market}/scan"
USER_AGENT = "Mozilla/5.0 (claude-trading-skills tradingview-screener)"
DEFAULT_LIMIT = 50
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Field aliases: friendly snake_case → canonical scanner field name.
# Canonical names verified against scanner.tradingview.com/america/metainfo.
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, str] = {
    # Market data
    "price": "close",
    "change": "change",
    "change_abs": "change_abs",
    "change_from_open": "change_from_open",
    "gap": "gap",
    "volume": "volume",
    "avg_volume": "average_volume_30d_calc",
    "avg_volume_10d": "average_volume_10d_calc",
    "avg_volume_30d": "average_volume_30d_calc",
    "avg_volume_60d": "average_volume_60d_calc",
    "avg_volume_90d": "average_volume_90d_calc",
    "rel_volume": "relative_volume_10d_calc",
    "volatility": "Volatility.D",
    "volatility_week": "Volatility.W",
    "volatility_month": "Volatility.M",
    "beta": "beta_1_year",
    "premarket_change": "premarket_change",
    "premarket_gap": "premarket_gap",
    "postmarket_change": "postmarket_change",
    "high_52w": "price_52_week_high",
    "low_52w": "price_52_week_low",
    "all_time_high": "all_time_high",
    "all_time_low": "all_time_low",
    "perf_week": "Perf.W",
    "perf_month": "Perf.1M",
    "perf_3m": "Perf.3M",
    "perf_6m": "Perf.6M",
    "perf_ytd": "Perf.YTD",
    "perf_year": "Perf.Y",
    "perf_5y": "Perf.5Y",
    # Size / valuation
    "market_cap": "market_cap_basic",
    "mkt_cap": "market_cap_basic",
    "pe": "price_earnings_ttm",
    "forward_pe": "price_earnings_forward_fy",
    "peg": "price_earnings_growth_ttm",
    "pb": "price_book_fq",
    "ps": "price_sales_current",
    "price_fcf": "price_free_cash_flow_ttm",
    "earnings_yield": "earnings_yield",
    "ev": "enterprise_value_current",
    "ev_ebitda": "enterprise_value_ebitda_ttm",
    "ev_ebit": "enterprise_value_to_ebit_ttm",
    "ev_revenue": "enterprise_value_to_revenue_ttm",
    # Dividends
    "div_yield": "dividends_yield_current",
    "div_yield_fy": "dividends_yield_fy",
    "payout_ratio": "dividend_payout_ratio_ttm",
    "dps": "dps_common_stock_prim_issue_fy",
    "dps_growth": "dps_common_stock_prim_issue_yoy_growth_fy",
    "div_growth_years": "continuous_dividend_growth",
    "div_payout_years": "continuous_dividend_payout",
    # Fundamentals
    "revenue": "total_revenue_ttm",
    "revenue_growth": "total_revenue_yoy_growth_ttm",
    "revenue_growth_5y": "total_revenue_cagr_5y",
    "net_income": "net_income_ttm",
    "net_income_growth": "net_income_yoy_growth_ttm",
    "ebitda": "ebitda_ttm",
    "eps": "earnings_per_share_diluted_ttm",
    "eps_growth": "earnings_per_share_diluted_yoy_growth_ttm",
    "eps_growth_quarterly": "earnings_per_share_diluted_qoq_growth_fq",
    "eps_growth_5y": "earnings_per_share_basic_cagr_5y",
    "eps_surprise": "eps_surprise_percent_fq",
    "fcf": "free_cash_flow_ttm",
    "fcf_growth_5y": "free_cash_flow_cagr_5y",
    "fcf_margin": "free_cash_flow_margin_ttm",
    "gross_margin": "gross_margin_ttm",
    "operating_margin": "operating_margin_ttm",
    "net_margin": "net_margin_ttm",
    "pretax_margin": "pre_tax_margin_ttm",
    "ebitda_margin": "ebitda_margin_ttm",
    "roe": "return_on_equity_fq",
    "roa": "return_on_assets_fq",
    "roic": "return_on_invested_capital_fq",
    "current_ratio": "current_ratio_fq",
    "quick_ratio": "quick_ratio_fq",
    "debt_to_equity": "debt_to_equity_fq",
    "total_debt": "total_debt_fq",
    "net_debt": "net_debt_fq",
    "total_assets": "total_assets_fq",
    "total_equity": "total_equity_fq",
    "cash": "cash_n_short_term_invest_fq",
    "capex": "capital_expenditures_ttm",
    "piotroski": "piotroski_f_score_ttm",
    "altman_z": "altman_z_score_ttm",
    "graham": "graham_numbers_ttm",
    "employees": "number_of_employees",
    "shares_outstanding": "total_shares_outstanding",
    "float_percent": "float_shares_percent_current",
    "float_shares": "float_shares_outstanding_current",
    "target_price": "price_target_average",
    "recommendation": "recommendation_mark",
    # Technicals (daily timeframe)
    "rsi": "RSI",
    "rsi7": "RSI7",
    "stoch_k": "Stoch.K",
    "stoch_d": "Stoch.D",
    "stoch_rsi_k": "Stoch.RSI.K",
    "macd": "MACD.macd",
    "macd_signal": "MACD.signal",
    "macd_hist": "MACD.hist",
    "adx": "ADX",
    "atr": "ATR",
    "atr_percent": "ATRP",
    "adr": "ADR",
    "adr_percent": "ADRP",
    "cci": "CCI20",
    "williams_r": "W.R",
    "momentum": "Mom",
    "awesome_oscillator": "AO",
    "ultimate_oscillator": "UO",
    "rate_of_change": "ROC",
    "money_flow": "MoneyFlow",
    "chaikin_money_flow": "ChaikinMoneyFlow",
    "bull_bear_power": "BBPower",
    "bb_upper": "BB.upper",
    "bb_lower": "BB.lower",
    "bb_basis": "BB.basis",
    "psar": "P.SAR",
    "vwap": "VWAP",
    "vwma": "VWMA",
    "sma10": "SMA10",
    "sma20": "SMA20",
    "sma50": "SMA50",
    "sma100": "SMA100",
    "sma200": "SMA200",
    "ema10": "EMA10",
    "ema20": "EMA20",
    "ema50": "EMA50",
    "ema100": "EMA100",
    "ema200": "EMA200",
    "tech_rating": "Recommend.All",
    "ma_rating": "Recommend.MA",
    "oscillators_rating": "Recommend.Other",
}

# Scanner field charset: letters, digits, _ . + | [ ] - (e.g. ADX+DI, RSI|1W, AO[2])
_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.+|\[\]-]*$")
_MARKET_RE = re.compile(r"^[a-z]+$")
# Right-hand side treated as a field reference when it looks like a scanner field
_FIELD_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.+|\[\]-]*$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SUFFIX_MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

# ---------------------------------------------------------------------------
# Column presets — mirror the TradingView screener column-set tabs.
# All field names verified against the america metainfo registry.
# ---------------------------------------------------------------------------

COLUMN_PRESETS: dict[str, list[str]] = {
    "overview": [
        "name",
        "description",
        "close",
        "change",
        "volume",
        "relative_volume_10d_calc",
        "market_cap_basic",
        "price_earnings_ttm",
        "earnings_per_share_diluted_ttm",
        "dividends_yield_current",
        "sector",
        "exchange",
    ],
    "performance": [
        "name",
        "close",
        "change",
        "Perf.W",
        "Perf.1M",
        "Perf.3M",
        "Perf.6M",
        "Perf.YTD",
        "Perf.Y",
        "Perf.5Y",
        "Volatility.W",
        "Volatility.M",
        "beta_1_year",
    ],
    "valuation": [
        "name",
        "close",
        "market_cap_basic",
        "price_earnings_ttm",
        "price_earnings_growth_ttm",
        "price_sales_current",
        "price_book_fq",
        "price_free_cash_flow_ttm",
        "enterprise_value_current",
        "enterprise_value_ebitda_ttm",
        "enterprise_value_to_revenue_ttm",
        "earnings_yield",
    ],
    "dividends": [
        "name",
        "close",
        "dividends_yield_current",
        "dividend_payout_ratio_ttm",
        "dps_common_stock_prim_issue_fy",
        "dps_common_stock_prim_issue_yoy_growth_fy",
        "continuous_dividend_growth",
        "continuous_dividend_payout",
        "dividend_ex_date_upcoming",
        "dividend_amount_upcoming",
    ],
    "profitability": [
        "name",
        "close",
        "gross_margin_ttm",
        "operating_margin_ttm",
        "pre_tax_margin_ttm",
        "net_margin_ttm",
        "free_cash_flow_margin_ttm",
        "ebitda_margin_ttm",
        "return_on_assets_fq",
        "return_on_equity_fq",
        "return_on_invested_capital_fq",
    ],
    "income": [
        "name",
        "close",
        "total_revenue_ttm",
        "total_revenue_yoy_growth_ttm",
        "gross_profit_ttm",
        "oper_income_ttm",
        "net_income_ttm",
        "ebitda_ttm",
        "earnings_per_share_diluted_ttm",
        "earnings_per_share_diluted_yoy_growth_ttm",
    ],
    "balance": [
        "name",
        "close",
        "total_assets_fq",
        "total_current_assets_fq",
        "cash_n_short_term_invest_fq",
        "total_liabilities_fq",
        "total_debt_fq",
        "net_debt_fq",
        "total_equity_fq",
        "current_ratio_fq",
        "quick_ratio_fq",
        "debt_to_equity_fq",
    ],
    "cashflow": [
        "name",
        "close",
        "cash_f_operating_activities_ttm",
        "cash_f_investing_activities_ttm",
        "cash_f_financing_activities_ttm",
        "free_cash_flow_ttm",
        "capital_expenditures_ttm",
        "free_cash_flow_per_share_ttm",
    ],
    "technicals": [
        "name",
        "close",
        "change",
        "RSI",
        "Stoch.K",
        "MACD.macd",
        "MACD.signal",
        "ADX",
        "ATR",
        "Mom",
        "CCI20",
        "W.R",
        "SMA50",
        "SMA200",
        "Recommend.All",
        "Recommend.MA",
        "Recommend.Other",
    ],
}

# Pseudo-columns valid in scan requests but absent from the metainfo registry
_EXTRA_VALID_COLUMNS = {"name", "description", "logoid", "ticker-view"}

# ---------------------------------------------------------------------------
# Index symbol sets (the "Index" pill). Raw SYML:* values pass through.
# ---------------------------------------------------------------------------

INDEX_SYMBOLSETS: dict[str, str] = {
    "sp500": "SYML:SP;SPX",
    "sp100": "SYML:SP;OEX",
    "sp400": "SYML:SP;MID",
    "nasdaq100": "SYML:NASDAQ;NDX",
    "nasdaqcomposite": "SYML:NASDAQ;IXIC",
    "dow30": "SYML:DJ;DJI",
    "russell1000": "SYML:TVC;RUI",
    "russell2000": "SYML:TVC;RUT",
    "russell3000": "SYML:TVC;RUA",
}

# ---------------------------------------------------------------------------
# Rating envelopes (unauthenticated scans cannot read the string enum fields,
# so categorical ratings map to numeric ranges of working fields).
# recommendation_mark: 1 = Strong buy … 5 = Strong sell.
# Recommend.All: +1 = Strong buy … -1 = Strong sell.
# ---------------------------------------------------------------------------

ANALYST_RATING_RANGES: dict[str, tuple[float, float]] = {
    "strong_buy": (1.0, 1.5),
    "buy": (1.5, 2.5),
    "hold": (2.5, 3.5),
    "neutral": (2.5, 3.5),
    "sell": (3.5, 4.5),
    "strong_sell": (4.5, 5.0),
}

TECHNICAL_RATING_RANGES: dict[str, tuple[float, float]] = {
    "strong_buy": (0.5, 1.0),
    "buy": (0.1, 0.5),
    "neutral": (-0.1, 0.1),
    "sell": (-0.5, -0.1),
    "strong_sell": (-1.0, -0.5),
}

# ---------------------------------------------------------------------------
# Universe definitions (filter2 block) — captured from the live All Stocks tab.
# ---------------------------------------------------------------------------

_ARM_COMMON = {
    "operation": {
        "operator": "and",
        "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
            {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}},
        ],
    }
}
_ARM_PREFERRED = {
    "operation": {
        "operator": "and",
        "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
            {"expression": {"left": "typespecs", "operation": "has", "right": ["preferred"]}},
        ],
    }
}
_ARM_DR = {
    "operation": {
        "operator": "and",
        "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "dr"}},
        ],
    }
}
_ARM_FUND = {
    "operation": {
        "operator": "and",
        "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "fund"}},
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["etf", "mutual"],
                }
            },
        ],
    }
}

UNIVERSES: dict[str, list[dict]] = {
    # The All Stocks tab: common + preferred stocks, DRs, closed-end funds
    "all": [_ARM_COMMON, _ARM_PREFERRED, _ARM_DR, _ARM_FUND],
    "common": [_ARM_COMMON],
}


def _build_filter2(universe: str) -> dict:
    arms = UNIVERSES.get(universe)
    if arms is None:
        raise ValueError(f"Unknown universe '{universe}'. Choose from: {sorted(UNIVERSES)}")
    return {
        "operator": "and",
        "operands": [
            {"operation": {"operator": "or", "operands": copy.deepcopy(arms)}},
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    }


# ---------------------------------------------------------------------------
# Filter token parsing
# ---------------------------------------------------------------------------


def resolve_field(name: str) -> str:
    """Resolve a friendly alias or raw scanner field name to its canonical form."""
    name = name.strip()
    alias = FIELD_ALIASES.get(name.lower())
    if alias:
        return alias
    if not _FIELD_RE.match(name):
        raise ValueError(
            f"Invalid field '{name}'. Use a known alias or a raw scanner field name "
            "(letters, digits, '_', '.', '+', '|', '[', ']')."
        )
    return name


def parse_value(raw: str) -> float | bool | str:
    """Parse a filter value: number (with K/M/B/T suffix, % stripped), bool, or string."""
    raw = raw.strip()
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    candidate = raw[:-1] if raw.endswith("%") else raw
    suffix = candidate[-1:].upper()
    if suffix in _SUFFIX_MULTIPLIERS and _NUMBER_RE.match(candidate[:-1]):
        return float(candidate[:-1]) * _SUFFIX_MULTIPLIERS[suffix]
    if _NUMBER_RE.match(candidate):
        return float(candidate)
    return raw


_OPERATORS = [
    (">=", "egreater"),
    ("<=", "eless"),
    ("!=", "nequal"),
    (">", "greater"),
    ("<", "less"),
    ("=", "equal"),
]


def parse_filter_token(token: str) -> dict:
    """Parse one filter token like ``pe<20`` into a scanner filter expression."""
    token = token.strip()
    for symbol, operation in _OPERATORS:
        idx = token.find(symbol)
        if idx > 0:
            left_raw = token[:idx]
            right_raw = token[idx + len(symbol) :].strip()
            if not right_raw:
                raise ValueError(f"Filter '{token}' is missing a value.")
            left = resolve_field(left_raw)
            if operation == "equal":
                if ".." in right_raw:
                    lo_raw, hi_raw = right_raw.split("..", 1)
                    lo, hi = parse_value(lo_raw), parse_value(hi_raw)
                    if not isinstance(lo, float) or not isinstance(hi, float):
                        raise ValueError(f"Range bounds in '{token}' must be numeric.")
                    return {"left": left, "operation": "in_range", "right": [lo, hi]}
                if "|" in right_raw:
                    values = [v.strip() for v in right_raw.split("|") if v.strip()]
                    return {"left": left, "operation": "in_range", "right": values}
            return {"left": left, "operation": operation, "right": parse_value(right_raw)}
    raise ValueError(
        f"Invalid filter token '{token}'. "
        "Expected field<op>value with op in >=, <=, !=, >, <, = "
        "(e.g. pe<20, mkt_cap>=1.5B, div_yield=3..8)."
    )


def _rating_envelope(
    selections: list[str], ranges: dict[str, tuple[float, float]], label: str
) -> list[float]:
    bounds: list[tuple[float, float]] = []
    for sel in selections:
        key = sel.strip().lower().replace(" ", "_").replace("-", "_")
        if key not in ranges:
            raise ValueError(f"Unknown {label} rating '{sel}'. Choose from: {sorted(set(ranges))}")
        bounds.append(ranges[key])
    return [min(lo for lo, _ in bounds), max(hi for _, hi in bounds)]


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


def resolve_columns(columns: str, add_columns: list[str] | None = None) -> list[str]:
    """Resolve a preset name or comma-separated column list, plus extras."""
    if columns in COLUMN_PRESETS:
        resolved = list(COLUMN_PRESETS[columns])
    else:
        resolved = []
        for col in columns.split(","):
            col = col.strip()
            if not col:
                continue
            resolved.append(col if col in _EXTRA_VALID_COLUMNS else resolve_field(col))
    for col in add_columns or []:
        col = col.strip()
        if not col:
            continue
        col = col if col in _EXTRA_VALID_COLUMNS else resolve_field(col)
        if col not in resolved:
            resolved.append(col)
    return resolved


def build_payload(
    filters: list[str],
    *,
    sectors: list[str] | None = None,
    industries: list[str] | None = None,
    countries: list[str] | None = None,
    exchanges: list[str] | None = None,
    index: str | None = None,
    analyst_rating: list[str] | None = None,
    technical_rating: list[str] | None = None,
    columns: str = "overview",
    add_columns: list[str] | None = None,
    sort: str = "-market_cap_basic",
    limit: int = DEFAULT_LIMIT,
    market: str = "america",
    universe: str = "all",
    include_secondary: bool = False,
) -> dict:
    """Build the scanner /scan request payload (All Stocks tab semantics)."""
    expressions: list[dict] = [
        {"left": "is_blacklisted", "operation": "equal", "right": False},
    ]
    if not include_secondary:
        expressions.append({"left": "is_primary", "operation": "equal", "right": True})

    for token in filters:
        expressions.append(parse_filter_token(token))

    for field, values in (
        ("sector", sectors),
        ("industry", industries),
        ("country", countries),
        ("exchange", exchanges),
    ):
        if values:
            expressions.append({"left": field, "operation": "in_range", "right": list(values)})

    if analyst_rating:
        expressions.append(
            {
                "left": "recommendation_mark",
                "operation": "in_range",
                "right": _rating_envelope(analyst_rating, ANALYST_RATING_RANGES, "analyst"),
            }
        )
    if technical_rating:
        expressions.append(
            {
                "left": "Recommend.All",
                "operation": "in_range",
                "right": _rating_envelope(technical_rating, TECHNICAL_RATING_RANGES, "technical"),
            }
        )

    sort_field, sort_order = sort, "asc"
    if ":" in sort_field:
        sort_field, _, suffix = sort_field.partition(":")
        if suffix not in ("asc", "desc"):
            raise ValueError(f"Invalid sort suffix ':{suffix}' — use ':asc' or ':desc'.")
        sort_order = suffix
    elif sort_field.startswith("-"):
        sort_field, sort_order = sort_field[1:], "desc"
    sort_field = resolve_field(sort_field)

    payload: dict = {
        "columns": resolve_columns(columns, add_columns),
        "filter": expressions,
        "filter2": _build_filter2(universe),
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "markets": [market],
        "range": [0, limit],
        "sort": {"sortBy": sort_field, "sortOrder": sort_order},
    }

    if index:
        if index.startswith("SYML:"):
            symbolset = index
        elif index.lower().replace("-", "").replace("_", "") in INDEX_SYMBOLSETS:
            symbolset = INDEX_SYMBOLSETS[index.lower().replace("-", "").replace("_", "")]
        else:
            raise ValueError(
                f"Unknown index '{index}'. Choose from {sorted(INDEX_SYMBOLSETS)} "
                "or pass a raw 'SYML:...' id."
            )
        payload["symbols"] = {"symbolset": [symbolset]}

    return payload


# ---------------------------------------------------------------------------
# HTTP layer with retry
# ---------------------------------------------------------------------------


class ScanError(Exception):
    """Fatal scan failure (validation error or retries exhausted)."""


class TransientScanError(Exception):
    """Retryable failure (HTTP 429/5xx, network timeouts)."""


def _http_post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # pragma: no cover - best effort
            pass
        if exc.code == 429 or exc.code >= 500:
            raise TransientScanError(f"HTTP {exc.code}: {detail}") from exc
        raise ScanError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TransientScanError(f"Network error: {exc.reason}") from exc


def run_scan(
    payload: dict,
    market: str,
    *,
    timeout: int = 30,
    max_retries: int = MAX_RETRIES,
    retry_base_delay: float = 1.5,
) -> dict:
    """POST the payload to the scanner with exponential-backoff retries."""
    url = SCAN_URL_TEMPLATE.format(market=market)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return _http_post_json(url, payload, timeout)
        except TransientScanError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                delay = retry_base_delay * (2**attempt)
                print(
                    f"Warning: {exc} — retrying in {delay:.1f}s ({attempt + 1}/{max_retries - 1})",
                    file=sys.stderr,
                )
                time.sleep(delay)
    raise ScanError(f"Scan failed after {max_retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

# Signed % — direction matters (change, performance, gap, growth)
_SIGNED_PERCENT_FIELD_RE = re.compile(
    r"(^|_)(change|gap)|^Perf\.|_growth|growth_|cagr|surprise",
    re.IGNORECASE,
)
# Unsigned % — levels (yield, margin, percent, volatility)
_UNSIGNED_PERCENT_FIELD_RE = re.compile(
    r"yield|margin|percent|^Volatility\.|^ATRP$|^ADRP$",
    re.IGNORECASE,
)
# Ratio-like fields that contain big-number words but are not big numbers
_RATIO_FIELD_RE = re.compile(r"relative_volume|_ratio|_to_|recommendation_mark", re.IGNORECASE)
# Unix-timestamp date fields (scanner returns epoch seconds)
_DATE_FIELD_RE = re.compile(r"(_date($|_)|\.Date$|_time$|^last_trade_time$)", re.IGNORECASE)
# Integer counters (years of dividend growth/payout, Piotroski score)
_COUNT_FIELD_RE = re.compile(r"^continuous_dividend_|piotroski", re.IGNORECASE)
_BIG_NUMBER_FIELD_RE = re.compile(
    r"market_cap|volume|revenue|income|ebitda|profit|debt|assets|equity|liabilities"
    r"|cash|expenditures|enterprise_value|float_shares_outstanding|shares_outstanding"
    r"|employees|dividends_paid",
    re.IGNORECASE,
)


def humanize(value: object, field: str) -> str:
    """Format a cell value for the markdown table."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if _DATE_FIELD_RE.search(field) and value > 1e9:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
        if _COUNT_FIELD_RE.search(field):
            return f"{value:.0f}"
        if _SIGNED_PERCENT_FIELD_RE.search(field):
            return f"{value:+.2f}%"
        if _UNSIGNED_PERCENT_FIELD_RE.search(field):
            return f"{value:.2f}%"
        if not _RATIO_FIELD_RE.search(field) and _BIG_NUMBER_FIELD_RE.search(field):
            magnitude = abs(value)
            for suffix, threshold in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
                if magnitude >= threshold:
                    return f"{value / threshold:.2f}{suffix}"
            return f"{value:.0f}"
        return f"{value:,.2f}".replace(",", "_").replace("_", ",")
    return str(value)


def _rows_from_response(response: dict, columns: list[str]) -> list[dict]:
    rows = []
    for item in response.get("data", []):
        row = {"symbol": item.get("s", "")}
        for col, val in zip(columns, item.get("d", [])):
            row[col] = val
        rows.append(row)
    return rows


def render_markdown(response: dict, columns: list[str], meta: dict) -> str:
    """Render scan results as a markdown report."""
    rows = _rows_from_response(response, columns)
    generated = meta.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines = [
        "# TradingView Screener Results",
        "",
        f"- **Generated:** {generated}",
        f"- **Market:** {meta.get('market', 'america')}",
        f"- **Universe:** {meta.get('universe', 'all')} (All Stocks tab semantics)",
        f"- **Filters:** {', '.join(meta.get('filters', [])) or '(none)'}",
        f"- **Total matches: {response.get('totalCount', 0)}** (showing {len(rows)})",
        "",
    ]
    header = ["Symbol"] + columns
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for row in rows:
        cells = [row["symbol"]] + [humanize(row.get(col), col) for col in columns]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "---",
        "*Source: TradingView Stock Screener (scanner.tradingview.com). "
        "Data is informational only — verify before trading.*",
        "",
    ]
    return "\n".join(lines)


def write_reports(
    response: dict,
    columns: list[str],
    meta: dict,
    output_dir: str,
    screen_name: str = "scan",
) -> tuple[Path, Path]:
    """Write markdown + JSON reports; return their paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", screen_name.lower()).strip("-") or "scan"
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = f"tradingview_screener_{slug}_{stamp}"

    md_path = out / f"{base}.md"
    md_path.write_text(render_markdown(response, columns, meta), encoding="utf-8")

    json_path = out / f"{base}.json"
    json_payload = {
        "generated_at": meta.get("generated_at", datetime.now().isoformat(timespec="seconds")),
        "market": meta.get("market"),
        "universe": meta.get("universe"),
        "filters": meta.get("filters", []),
        "columns": columns,
        "totalCount": response.get("totalCount", 0),
        "rows": _rows_from_response(response, columns),
    }
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
    return md_path, json_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _csv(raw: str | None) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()] if raw else []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Screen stocks via the TradingView scanner API (All Stocks tab).",
    )
    parser.add_argument(
        "--filters",
        default=None,
        help="Comma-separated filter tokens (e.g. 'mkt_cap>10B,pe<20,div_yield=3..8').",
    )
    parser.add_argument("--sectors", default=None, help="Comma-separated TV sector names.")
    parser.add_argument("--industries", default=None, help="Comma-separated TV industry names.")
    parser.add_argument("--countries", default=None, help="Comma-separated country names.")
    parser.add_argument(
        "--exchanges", default=None, help="Comma-separated exchanges (NASDAQ,NYSE,AMEX,OTC)."
    )
    parser.add_argument(
        "--index",
        default=None,
        help=f"Index universe: {', '.join(sorted(INDEX_SYMBOLSETS))} or raw 'SYML:...' id.",
    )
    parser.add_argument(
        "--analyst-rating",
        default=None,
        help="Comma-separated: strong_buy,buy,hold,sell,strong_sell (via recommendation_mark).",
    )
    parser.add_argument(
        "--technical-rating",
        default=None,
        help="Comma-separated: strong_buy,buy,neutral,sell,strong_sell (via Recommend.All).",
    )
    parser.add_argument(
        "--columns",
        default="overview",
        help=f"Column preset ({', '.join(sorted(COLUMN_PRESETS))}) or comma-separated fields.",
    )
    parser.add_argument("--add-columns", default=None, help="Extra columns appended to the preset.")
    parser.add_argument(
        "--sort",
        default="-market_cap_basic",
        help="Sort field; prefix '-' for descending (default: -market_cap_basic).",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"Max rows (default {DEFAULT_LIMIT})."
    )
    parser.add_argument(
        "--market",
        default="america",
        help="TV market endpoint: america, global, germany, japan, ... (default: america).",
    )
    parser.add_argument(
        "--universe",
        default="all",
        choices=sorted(UNIVERSES),
        help="'all' = All Stocks tab (common+preferred+DR+funds); 'common' = common stocks only.",
    )
    parser.add_argument(
        "--include-secondary",
        action="store_true",
        help="Include secondary listings (drop the is_primary=true default).",
    )
    parser.add_argument(
        "--output-dir", default="reports/", help="Report output directory (default: reports/)."
    )
    parser.add_argument("--screen-name", default="scan", help="Slug used in report filenames.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the scan payload as JSON without calling the network.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    filters = _csv(args.filters)
    sectors = _csv(args.sectors)
    industries = _csv(args.industries)
    countries = _csv(args.countries)
    exchanges = _csv(args.exchanges)
    analyst_rating = _csv(args.analyst_rating)
    technical_rating = _csv(args.technical_rating)

    if not any(
        [
            filters,
            sectors,
            industries,
            countries,
            exchanges,
            args.index,
            analyst_rating,
            technical_rating,
        ]
    ):
        print(
            "Error: provide at least one screening criterion "
            "(--filters / --sectors / --industries / --countries / --exchanges "
            "/ --index / --analyst-rating / --technical-rating).",
            file=sys.stderr,
        )
        return 1

    if not _MARKET_RE.match(args.market):
        print(f"Error: invalid market '{args.market}' (lowercase letters only).", file=sys.stderr)
        return 1
    if args.limit < 1 or args.limit > 500:
        print("Error: --limit must be between 1 and 500.", file=sys.stderr)
        return 1

    try:
        payload = build_payload(
            filters,
            sectors=sectors,
            industries=industries,
            countries=countries,
            exchanges=exchanges,
            index=args.index,
            analyst_rating=analyst_rating,
            technical_rating=technical_rating,
            columns=args.columns,
            add_columns=_csv(args.add_columns),
            sort=args.sort,
            limit=args.limit,
            market=args.market,
            universe=args.universe,
            include_secondary=args.include_secondary,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    try:
        response = run_scan(payload, args.market)
    except ScanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": args.market,
        "universe": args.universe,
        "filters": filters
        + [f"sector={'|'.join(sectors)}" for _ in [1] if sectors]
        + [f"industry={'|'.join(industries)}" for _ in [1] if industries]
        + [f"index={args.index}" for _ in [1] if args.index],
    }
    if response.get("totalCount", 0) == 0:
        print(
            "Warning: 0 matches. The scanner silently ignores unknown filter fields — "
            "check field names against references/tradingview_screener_filters.md, "
            "or relax the criteria.",
            file=sys.stderr,
        )

    columns = payload["columns"]
    md_path, json_path = write_reports(response, columns, meta, args.output_dir, args.screen_name)

    print(render_markdown(response, columns, meta))
    print(f"Markdown report: {md_path}")
    print(f"JSON report:     {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
