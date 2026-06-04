#!/usr/bin/env python3
"""
Value Dividend Stock Screener — TradingView data layer (no FMP key).

Two screening modes:
1. Default: S&P 500 universe, all data (annual DPS history, fundamentals,
   daily bars for RSI) from a live TradingView Desktop chart via the shared
   tv_client data layer. No API key, no request quota.
2. --use-finviz: FINVIZ Elite pre-screen widens the universe beyond the
   S&P 500 (value + dividend-growth filters), then TradingView supplies the
   detailed analysis. Requires FINVIZ_API_KEY.

Screens US stocks based on:
- Dividend yield >= 3.0%
- P/E ratio <= 20
- P/B ratio <= 2
- Dividend CAGR >= 4% (3-year)
- Revenue growth: positive trend over 3 years
- EPS growth: positive trend over 3 years
- Additional analysis: dividend sustainability, financial health, quality scores

Outputs top N stocks ranked by composite score (oversold RSI <= 40 preferred).
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Shared TradingView data layer (drop-in FMPClient replacement).
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts", "lib"),
)
from tv_client import FMPClient  # noqa: E402


class FINVIZClient:
    """Client for FINVIZ Elite API"""

    BASE_URL = "https://elite.finviz.com/export.ashx"

    def __init__(self, api_key: str):
        # requests is only needed for the optional FINVIZ pre-screen, so the
        # TradingView-only default path runs without it installed.
        import requests

        self.api_key = api_key
        self.session = requests.Session()
        self._requests = requests

    def screen_stocks(self) -> set[str]:
        """
        Screen stocks using FINVIZ Elite API with predefined criteria

        Criteria:
        - Market cap: Mid-cap or higher
        - Dividend yield: 3%+
        - Dividend growth (3Y): 5%+
        - EPS growth (3Y): Positive
        - P/B: Under 2
        - P/E: Under 20
        - Sales growth (3Y): Positive
        - Geography: USA

        Returns:
            Set of stock symbols
        """
        # Build filter string in FINVIZ format: key_value,key_value,...
        filters = "cap_midover,fa_div_o3,fa_divgrowth_3yo5,fa_eps3years_pos,fa_pb_u2,fa_pe_u20,fa_sales3years_pos,geo_usa"

        params = {
            "v": "151",  # View type
            "f": filters,  # Filter conditions
            "ft": "4",  # File type: CSV export
            "auth": self.api_key,
        }

        try:
            print("Fetching pre-screened stocks from FINVIZ Elite API...", file=sys.stderr)
            response = self.session.get(self.BASE_URL, params=params, timeout=30)

            if response.status_code == 200:
                # Parse CSV response
                csv_content = response.content.decode("utf-8")
                reader = csv.DictReader(io.StringIO(csv_content))

                symbols = set()
                for row in reader:
                    # FINVIZ CSV has 'Ticker' column
                    ticker = row.get("Ticker", "").strip()
                    if ticker:
                        symbols.add(ticker)

                print(f"✅ FINVIZ returned {len(symbols)} pre-screened stocks", file=sys.stderr)
                return symbols

            elif response.status_code == 401 or response.status_code == 403:
                print(
                    "ERROR: FINVIZ API authentication failed. Check your API key.", file=sys.stderr
                )
                print(f"Status code: {response.status_code}", file=sys.stderr)
                return set()
            else:
                print(f"ERROR: FINVIZ API request failed: {response.status_code}", file=sys.stderr)
                return set()

        except self._requests.exceptions.RequestException as e:
            print(f"ERROR: FINVIZ request exception: {e}", file=sys.stderr)
            return set()


class RSICalculator:
    """Calculate RSI (Relative Strength Index)"""

    @staticmethod
    def calculate_rsi(prices: list[float], period: int = 14) -> Optional[float]:
        """
        Calculate RSI from price data.

        Args:
            prices: List of closing prices (oldest to newest)
            period: RSI period (default 14)

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(prices) < period + 1:
            return None

        # Calculate price changes
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # Separate gains and losses
        gains = [max(0, change) for change in changes]
        losses = [abs(min(0, change)) for change in changes]

        # Calculate initial average gain/loss
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Smooth using Wilder's method for remaining periods
        for i in range(period, len(changes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        # Calculate RSI
        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 1)


class StockAnalyzer:
    """Analyzes stock data and calculates scores"""

    @staticmethod
    def is_reit(stock_data: dict) -> bool:
        """
        Determine if a stock is a REIT based on sector/industry.

        Args:
            stock_data: Dict containing sector and/or industry fields

        Returns:
            True if the stock is likely a REIT
        """
        sector = stock_data.get("sector", "") or ""
        industry = stock_data.get("industry", "") or ""

        sector_lower = sector.lower()
        industry_lower = industry.lower()

        if "real estate" in sector_lower:
            return True
        if "reit" in industry_lower:
            return True

        return False

    @staticmethod
    def calculate_cagr(start_value: float, end_value: float, years: int) -> Optional[float]:
        """Calculate Compound Annual Growth Rate"""
        if start_value <= 0 or end_value <= 0 or years <= 0:
            return None
        return (pow(end_value / start_value, 1 / years) - 1) * 100

    @staticmethod
    def check_positive_trend(values: list[float]) -> bool:
        """Check if values show positive trend (one dip allowed)"""
        if len(values) < 3:
            return False

        # Check overall trend: first < last
        if values[0] >= values[-1]:
            return False

        # Allow one dip but overall upward trend
        dips = sum(1 for i in range(1, len(values)) if values[i] < values[i - 1])
        return dips <= 1

    @staticmethod
    def analyze_dividend_growth(
        dividend_history: dict,
    ) -> tuple[Optional[float], bool, Optional[float]]:
        """Analyze dividend growth rate (3-year CAGR and consistency) and return
        latest annual dividend. Works on the annual DPS series tv_client emits
        (one synthetic year-end entry per fiscal year, no partial years)."""
        if not dividend_history or "historical" not in dividend_history:
            return None, False, None

        dividends = dividend_history["historical"]
        if len(dividends) < 4:  # Need at least 4 years
            return None, False, None

        # Sort by date
        dividends = sorted(dividends, key=lambda x: x["date"])

        # Aggregate to annual dividends (annual series: one entry per year)
        annual_dividends = {}
        for div in dividends:
            year = div["date"][:4]
            annual_dividends[year] = annual_dividends.get(year, 0) + div.get("dividend", 0)

        if len(annual_dividends) < 4:
            return None, False, None

        years = sorted(annual_dividends.keys())[-4:]
        div_values = [annual_dividends[y] for y in years]

        # Calculate 3-year CAGR
        cagr = StockAnalyzer.calculate_cagr(div_values[0], div_values[-1], 3)

        # Check for consistency (no dividend cuts)
        consistent = all(
            div_values[i] >= div_values[i - 1] * 0.95 for i in range(1, len(div_values))
        )

        # Get latest annual dividend (most recent year)
        latest_annual_dividend = div_values[-1]

        return cagr, consistent, latest_annual_dividend

    @staticmethod
    def analyze_revenue_growth(income_statements: list[dict]) -> tuple[bool, Optional[float]]:
        """Analyze revenue growth trend"""
        if len(income_statements) < 4:
            return False, None

        revenues = [stmt.get("revenue", 0) for stmt in income_statements[:4]]
        revenues.reverse()  # Oldest to newest

        positive_trend = StockAnalyzer.check_positive_trend(revenues)
        cagr = (
            StockAnalyzer.calculate_cagr(revenues[0], revenues[-1], 3) if revenues[0] > 0 else None
        )

        return positive_trend, cagr

    @staticmethod
    def analyze_eps_growth(income_statements: list[dict]) -> tuple[bool, Optional[float]]:
        """Analyze EPS growth trend"""
        if len(income_statements) < 4:
            return False, None

        eps_values = [stmt.get("eps", 0) for stmt in income_statements[:4]]
        eps_values.reverse()  # Oldest to newest

        positive_trend = StockAnalyzer.check_positive_trend(eps_values)
        cagr = (
            StockAnalyzer.calculate_cagr(eps_values[0], eps_values[-1], 3)
            if eps_values[0] > 0
            else None
        )

        return positive_trend, cagr

    @staticmethod
    def calculate_payout_ratios_from_metrics(key_metrics: dict, is_reit: bool = False) -> dict:
        """
        Calculate payout ratios from the TradingView key-metrics snapshot.

        Dividends paid are reconstructed as annual DPS x shares outstanding
        (the scanner serves no cash-flow dividendsPaid line).

        For REITs the net-income payout ratio is meaningless, so operating
        cash flow stands in for FFO (the scanner exposes no D&A line); for
        non-REITs the scanner's own payout ratio (dividends / net income)
        is used directly.

        Args:
            key_metrics: Single key-metrics dict from tv_client.get_key_metrics
            is_reit: Whether the stock is a REIT (uses OCF≈FFO for payout)

        Returns:
            Dict with payout_ratio and fcf_payout_ratio (as percentages)
        """
        result = {"payout_ratio": None, "fcf_payout_ratio": None}
        if not key_metrics:
            return result

        dps = key_metrics.get("annualDividendPerShare") or 0
        shares = key_metrics.get("sharesOutstanding") or 0
        dividends_paid = dps * shares
        fcf = key_metrics.get("freeCashFlow") or 0
        ocf = key_metrics.get("operatingCashFlow") or 0

        if is_reit:
            # OCF ≈ FFO proxy (net income + non-cash charges) — close enough
            # for a sustainability check.
            if ocf > 0 and dividends_paid > 0:
                result["payout_ratio"] = round((dividends_paid / ocf) * 100, 1)
        else:
            payout = key_metrics.get("payoutRatio")
            if payout is not None and payout > 0:
                result["payout_ratio"] = round(payout * 100, 1)

        # FCF payout ratio (same for both REIT and non-REIT)
        if fcf > 0 and dividends_paid > 0:
            result["fcf_payout_ratio"] = round((dividends_paid / fcf) * 100, 1)

        return result

    @staticmethod
    def analyze_financial_health(key_metrics: dict) -> dict:
        """Analyze financial health from the scanner's snapshot ratios
        (debt_to_equity_fq / current_ratio_fq, already computed by
        TradingView — no balance-sheet arithmetic needed). Absent data does
        not disqualify (parity with the FMP version's missing-data path)."""
        if not key_metrics:
            return {}

        debt_to_equity = key_metrics.get("debtToEquity")
        current_ratio = key_metrics.get("currentRatio")
        if debt_to_equity is not None:
            debt_to_equity = round(debt_to_equity, 2)
        if current_ratio is not None:
            current_ratio = round(current_ratio, 2)

        financially_healthy = (debt_to_equity is None or debt_to_equity < 2.0) and (
            current_ratio is None or current_ratio > 1.0
        )

        return {
            "debt_to_equity": debt_to_equity,
            "current_ratio": current_ratio,
            "financially_healthy": financially_healthy,
        }

    @staticmethod
    def analyze_dividend_stability(dividend_history: dict) -> dict:
        """
        Analyze dividend stability and growth consistency.

        Evaluates:
        - Year-over-year dividend growth
        - Volatility (variation in annual dividends)
        - Consecutive years of growth

        Args:
            dividend_history: Dict with 'historical' key containing dividend records

        Returns:
            Dict with is_stable, is_growing, volatility_pct, years_of_growth
        """
        result = {
            "is_stable": False,
            "is_growing": False,
            "volatility_pct": None,
            "years_of_growth": 0,
            "annual_dividends": {},
        }

        if not dividend_history or "historical" not in dividend_history:
            return result

        dividends = dividend_history["historical"]
        if len(dividends) < 4:
            return result

        # Calculate annual dividends
        annual_dividends = {}
        for div in dividends:
            year = div.get("date", "")[:4]
            if year:
                annual_dividends[year] = annual_dividends.get(year, 0) + div.get("dividend", 0)

        if len(annual_dividends) < 3:
            return result

        result["annual_dividends"] = annual_dividends

        # Get sorted years (newest to oldest for analysis)
        years = sorted(annual_dividends.keys(), reverse=True)
        div_values = [annual_dividends[y] for y in years]

        # Calculate volatility (coefficient of variation)
        if len(div_values) >= 2:
            avg_div = sum(div_values) / len(div_values)
            if avg_div > 0:
                max_div = max(div_values)
                min_div = min(div_values)
                # Volatility as percentage variation from average
                volatility = ((max_div - min_div) / avg_div) * 100
                result["volatility_pct"] = round(volatility, 1)

                # Stable if volatility < 50% (allowing some variation)
                result["is_stable"] = volatility < 50

        # Count consecutive years of growth (from oldest to newest)
        years_oldest_first = sorted(annual_dividends.keys())
        div_values_oldest_first = [annual_dividends[y] for y in years_oldest_first]

        years_of_growth = 0
        for i in range(1, len(div_values_oldest_first)):
            # Allow small decrease (5%) as "no cut"
            if div_values_oldest_first[i] >= div_values_oldest_first[i - 1] * 0.95:
                years_of_growth += 1
            else:
                years_of_growth = 0  # Reset on dividend cut

        result["years_of_growth"] = years_of_growth

        # Growing if at least 2 consecutive years of growth and overall uptrend
        if len(div_values_oldest_first) >= 3:
            overall_growth = div_values_oldest_first[-1] > div_values_oldest_first[0]
            result["is_growing"] = years_of_growth >= 2 and overall_growth

        return result

    @staticmethod
    def analyze_revenue_trend(income_statements: list[dict]) -> dict:
        """
        Analyze revenue trend for year-over-year growth.

        Args:
            income_statements: List of income statements (newest first)

        Returns:
            Dict with is_uptrend, years_of_growth, cagr
        """
        result = {"is_uptrend": False, "years_of_growth": 0, "cagr": None}

        if len(income_statements) < 3:
            return result

        # Get revenues (newest first in input, reverse for analysis)
        revenues = [stmt.get("revenue", 0) for stmt in income_statements[:4]]
        revenues_oldest_first = list(reversed(revenues))

        # Count years of growth
        years_of_growth = 0
        for i in range(1, len(revenues_oldest_first)):
            if revenues_oldest_first[i] > revenues_oldest_first[i - 1]:
                years_of_growth += 1

        result["years_of_growth"] = years_of_growth

        # Calculate CAGR
        if revenues_oldest_first[0] > 0 and revenues_oldest_first[-1] > 0:
            years = len(revenues_oldest_first) - 1
            if years > 0:
                cagr = (
                    pow(revenues_oldest_first[-1] / revenues_oldest_first[0], 1 / years) - 1
                ) * 100
                result["cagr"] = round(cagr, 2)

        # Uptrend if overall growth and at least 2 years of growth
        overall_growth = revenues_oldest_first[-1] > revenues_oldest_first[0]
        result["is_uptrend"] = overall_growth and years_of_growth >= 2

        return result

    @staticmethod
    def analyze_earnings_trend(income_statements: list[dict]) -> dict:
        """
        Analyze earnings/profit trend for year-over-year growth.

        Args:
            income_statements: List of income statements (newest first)

        Returns:
            Dict with is_uptrend, years_of_growth, cagr
        """
        result = {"is_uptrend": False, "years_of_growth": 0, "cagr": None}

        if len(income_statements) < 3:
            return result

        # Get net income (newest first in input, reverse for analysis)
        earnings = [stmt.get("netIncome") or 0 for stmt in income_statements[:4]]
        earnings_oldest_first = list(reversed(earnings))

        # Check for negative earnings (not a good sign)
        if any(e <= 0 for e in earnings_oldest_first):
            return result

        # Count years of growth
        years_of_growth = 0
        for i in range(1, len(earnings_oldest_first)):
            if earnings_oldest_first[i] > earnings_oldest_first[i - 1]:
                years_of_growth += 1

        result["years_of_growth"] = years_of_growth

        # Calculate CAGR
        if earnings_oldest_first[0] > 0 and earnings_oldest_first[-1] > 0:
            years = len(earnings_oldest_first) - 1
            if years > 0:
                cagr = (
                    pow(earnings_oldest_first[-1] / earnings_oldest_first[0], 1 / years) - 1
                ) * 100
                result["cagr"] = round(cagr, 2)

        # Uptrend if overall growth and at least 2 years of growth
        overall_growth = earnings_oldest_first[-1] > earnings_oldest_first[0]
        result["is_uptrend"] = overall_growth and years_of_growth >= 2

        return result

    @staticmethod
    def calculate_stability_score(stability: dict) -> float:
        """
        Calculate a stability score based on dividend stability metrics.

        Args:
            stability: Dict from analyze_dividend_stability

        Returns:
            Score from 0-100
        """
        score = 0

        # Stability bonus (max 40 points)
        if stability.get("is_stable"):
            score += 40
        elif stability.get("volatility_pct") is not None:
            # Partial credit for lower volatility
            volatility = stability["volatility_pct"]
            if volatility < 100:
                score += max(0, 40 - (volatility * 0.4))

        # Growth bonus (max 30 points)
        if stability.get("is_growing"):
            score += 30

        # Years of growth bonus (max 30 points, 10 per year)
        years = stability.get("years_of_growth", 0)
        score += min(years * 10, 30)

        return round(score, 1)

    @staticmethod
    def calculate_quality_score(key_metrics: dict) -> dict:
        """Calculate quality scores (ROE, Profit Margin) from the scanner's
        key-metrics snapshot. ROE and net margin arrive as PERCENT values
        (TradingView native scale, e.g. 25.0), unlike FMP's decimals."""
        result = {"roe": None, "profit_margin": None, "quality_score": 0}

        if not key_metrics:
            return result

        result["roe"] = key_metrics.get("roe")
        result["profit_margin"] = key_metrics.get("netProfitMargin")

        # Quality score (0-100)
        score = 0
        if result["roe"]:
            score += min(result["roe"] / 20 * 50, 50)  # Max 50 points for 20%+ ROE

        if result["profit_margin"]:
            score += min(result["profit_margin"] / 15 * 50, 50)  # Max 50 points for 15%+ margin

        result["quality_score"] = round(score, 1)

        return result


def screen_value_dividend_stocks(
    api_key: Optional[str] = None,
    top_n: int = 20,
    finviz_symbols: Optional[set[str]] = None,
    max_candidates: Optional[int] = None,
    min_yield: float = 3.0,
    pe_max: float = 20.0,
    pb_max: float = 2.0,
    min_div_growth: float = 4.0,
) -> list[dict]:
    """
    Main screening function (TradingView data layer).

    Args:
        api_key: Unused; accepted for interface parity (TradingView needs no key)
        top_n: Number of top stocks to return
        finviz_symbols: Optional set of symbols from FINVIZ pre-screening
        max_candidates: Maximum number of candidates to analyze (None = all)
        min_yield: Minimum dividend yield % (default 3.0)
        pe_max: Maximum P/E ratio (default 20)
        pb_max: Maximum P/B ratio (default 2)
        min_div_growth: Minimum 3-year dividend CAGR % (default 4.0)

    Returns:
        List of stocks with detailed analysis, sorted by composite score
    """
    client = FMPClient(api_key)  # TVClient drop-in; no key, no request quota
    analyzer = StockAnalyzer()
    rsi_calc = RSICalculator()

    print(f"\n{'=' * 60}", file=sys.stderr)
    print("Value Dividend Screener (TradingView data layer)", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)
    print("\nCriteria:", file=sys.stderr)
    print(f"  - Dividend Yield ≥ {min_yield}%", file=sys.stderr)
    print(f"  - P/E ≤ {pe_max}, P/B ≤ {pb_max}", file=sys.stderr)
    print(f"  - Dividend Growth (3Y CAGR) ≥ {min_div_growth}%", file=sys.stderr)
    print("  - Market Cap ≥ $2B; positive revenue/EPS trends", file=sys.stderr)
    print(f"\n{'=' * 60}\n", file=sys.stderr)

    # Step 1: Get candidate list
    if finviz_symbols:
        print(
            f"Step 1: Using FINVIZ pre-screened symbols ({len(finviz_symbols)} stocks)...",
            file=sys.stderr,
        )
        symbols = sorted(finviz_symbols)
    else:
        print("Step 1: S&P 500 universe via TradingView...", file=sys.stderr)
        constituents = client.get_sp500_constituents() or []
        symbols = [c["symbol"] for c in constituents]
        print(f"Found {len(symbols)} constituents", file=sys.stderr)

    if not symbols:
        print("ERROR: No candidates found", file=sys.stderr)
        return []

    if max_candidates:
        symbols = symbols[:max_candidates]
        print(f"Limiting analysis to first {max_candidates} candidates", file=sys.stderr)

    print("\nStep 2: Detailed analysis of candidates...", file=sys.stderr)

    results = []

    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] Analyzing {symbol}...", file=sys.stderr)

        # Profile carries sector/industry (REIT detection), market cap, price.
        stock = client.get_company_profile(symbol)
        if not stock:
            print("  ⚠️  No profile data", file=sys.stderr)
            continue
        company_name = stock.get("companyName") or symbol

        market_cap = stock.get("mktCap") or 0
        if market_cap < 2_000_000_000:
            print("  ⚠️  Market cap below $2B", file=sys.stderr)
            continue

        current_price = stock.get("price") or 0
        if current_price <= 0:
            print("  ⚠️  No valid price data", file=sys.stderr)
            continue

        # Valuation filters from the scanner snapshot (the FMP version applied
        # these in its stock-screener stage-1 call; with the S&P 500 universe
        # they're enforced per symbol here).
        key_metrics = client.get_key_metrics(symbol, limit=1)
        latest_metrics = key_metrics[0] if key_metrics else {}

        pe_ratio = latest_metrics.get("peRatio")
        if pe_ratio is None or pe_ratio <= 0 or pe_ratio > pe_max:
            print(f"  ⚠️  P/E {pe_ratio} outside (0, {pe_max}]", file=sys.stderr)
            continue

        pb_ratio = latest_metrics.get("pbRatio")
        if pb_ratio is None or pb_ratio <= 0 or pb_ratio > pb_max:
            print(f"  ⚠️  P/B {pb_ratio} outside (0, {pb_max}]", file=sys.stderr)
            continue

        # Cheap pre-filter on the scanner's current yield (same snapshot the
        # profile came from — no extra fetch). 0.7x slack so borderline names
        # still reach the exact DPS/price check below.
        yield_now = latest_metrics.get("dividendYield")
        if yield_now is None or yield_now < min_yield * 0.7:
            print(f"  ⚠️  Current yield {yield_now}% too low (pre-filter)", file=sys.stderr)
            continue

        # Fetch dividend history (annual DPS series from the scanner)
        dividend_history = client.get_dividend_history(symbol)
        if not dividend_history:
            print("  ⚠️  No dividend history", file=sys.stderr)
            continue

        # Analyze dividend growth and get latest annual dividend
        div_cagr, div_consistent, annual_dividend = analyzer.analyze_dividend_growth(
            dividend_history
        )
        if not div_cagr or div_cagr < min_div_growth:
            print(f"  ⚠️  Dividend CAGR < {min_div_growth}% (or no data)", file=sys.stderr)
            continue

        if not annual_dividend:
            print("  ⚠️  Cannot determine annual dividend", file=sys.stderr)
            continue

        # Calculate actual dividend yield
        actual_dividend_yield = (annual_dividend / current_price) * 100

        # Verify dividend yield >= min_yield
        if actual_dividend_yield < min_yield:
            print(
                f"  ⚠️  Dividend yield {actual_dividend_yield:.2f}% < {min_yield}%",
                file=sys.stderr,
            )
            continue

        # Annual income statements (scanner fiscal-year series)
        income_stmts = client.get_income_statement(symbol, period="annual", limit=5) or []
        if len(income_stmts) < 4:
            print("  ⚠️  Insufficient income statement data", file=sys.stderr)
            continue

        # Analyze revenue growth
        revenue_positive, revenue_cagr = analyzer.analyze_revenue_growth(income_stmts)
        if not revenue_positive:
            print("  ⚠️  Revenue trend not positive", file=sys.stderr)
            continue

        # Analyze EPS growth
        eps_positive, eps_cagr = analyzer.analyze_eps_growth(income_stmts)
        if not eps_positive:
            print("  ⚠️  EPS trend not positive", file=sys.stderr)
            continue

        # Check dividend stability - filter out highly volatile dividends
        dividend_stability = analyzer.analyze_dividend_stability(dividend_history)
        if dividend_stability["volatility_pct"] and dividend_stability["volatility_pct"] > 100:
            # Allow if consistently growing despite volatility
            if not dividend_stability["is_growing"] or dividend_stability["years_of_growth"] < 3:
                print(
                    f"  ⚠️  Dividend too volatile ({dividend_stability['volatility_pct']:.1f}%) and not consistently growing",
                    file=sys.stderr,
                )
                continue

        # Fetch historical prices for RSI (newest-first bars; slice the
        # ~30-day window the FMP version used so RSI smoothing matches)
        price_data = client.get_historical_prices(symbol, days=30)
        historical_prices = (price_data or {}).get("historical") or []
        historical_prices = historical_prices[:30]

        if len(historical_prices) < 20:
            print("  ⚠️  Insufficient price data for RSI calculation", file=sys.stderr)
            continue

        prices = [p["close"] for p in reversed(historical_prices)]  # Oldest first
        rsi = rsi_calc.calculate_rsi(prices, period=14)

        if rsi is None:
            print("  ⚠️  RSI calculation failed (insufficient price data)", file=sys.stderr)
            continue

        # Check if this is a REIT (uses different payout ratio calculation)
        is_reit = analyzer.is_reit(stock)

        # Payout ratios from the key-metrics snapshot
        payout_ratios = analyzer.calculate_payout_ratios_from_metrics(
            latest_metrics, is_reit=is_reit
        )
        payout_ratio = payout_ratios["payout_ratio"]
        fcf_payout_ratio = payout_ratios["fcf_payout_ratio"]

        # Sustainable if payout ratio < 80% and FCF covers dividends
        dividend_sustainable = False
        if payout_ratio and fcf_payout_ratio:
            dividend_sustainable = payout_ratio < 80 and fcf_payout_ratio < 100
        elif payout_ratio:
            dividend_sustainable = payout_ratio < 80

        # Financial health (snapshot D/E and current ratio)
        financial_health = analyzer.analyze_financial_health(latest_metrics)

        # Quality scores (percent-scale ROE / margin from the scanner)
        quality = analyzer.calculate_quality_score(latest_metrics)

        # Calculate stability score (dividend_stability already analyzed above)
        stability_score = analyzer.calculate_stability_score(dividend_stability)

        # Analyze revenue and earnings trends
        revenue_trend = analyzer.analyze_revenue_trend(income_stmts)
        earnings_trend = analyzer.analyze_earnings_trend(income_stmts)

        # Calculate composite score
        composite_score = 0
        composite_score += min(div_cagr / 10 * 15, 15)  # Max 15 points for 10%+ div growth
        composite_score += stability_score * 0.2  # Max 20 points from stability (100 * 0.2)
        composite_score += min((revenue_cagr or 0) / 10 * 10, 10)  # Max 10 points for revenue
        composite_score += min((eps_cagr or 0) / 15 * 10, 10)  # Max 10 points for EPS
        composite_score += 10 if dividend_sustainable else 0
        composite_score += 10 if financial_health.get("financially_healthy") else 0
        composite_score += quality["quality_score"] * 0.25  # Max 25 points from quality

        result = {
            "symbol": symbol,
            "company_name": company_name,
            "sector": stock.get("sector") or "N/A",
            "market_cap": market_cap,
            "price": current_price,
            "dividend_yield": round(actual_dividend_yield, 2),
            "annual_dividend": round(annual_dividend, 2),
            "pe_ratio": pe_ratio,
            "pb_ratio": pb_ratio,
            "rsi": rsi,
            "dividend_cagr_3y": round(div_cagr, 2),
            "dividend_consistent": div_consistent,
            "dividend_stable": dividend_stability["is_stable"],
            "dividend_growing": dividend_stability["is_growing"],
            "dividend_volatility_pct": dividend_stability["volatility_pct"],
            "dividend_years_of_growth": dividend_stability["years_of_growth"],
            "revenue_cagr_3y": round(revenue_cagr, 2) if revenue_cagr else None,
            "revenue_uptrend": revenue_trend["is_uptrend"],
            "revenue_years_of_growth": revenue_trend["years_of_growth"],
            "eps_cagr_3y": round(eps_cagr, 2) if eps_cagr else None,
            "earnings_uptrend": earnings_trend["is_uptrend"],
            "earnings_years_of_growth": earnings_trend["years_of_growth"],
            "payout_ratio": payout_ratio,
            "fcf_payout_ratio": fcf_payout_ratio,
            "dividend_sustainable": dividend_sustainable,
            "debt_to_equity": financial_health.get("debt_to_equity"),
            "current_ratio": financial_health.get("current_ratio"),
            "financially_healthy": financial_health.get("financially_healthy", False),
            # PERCENT values (TradingView native scale)
            "roe": round(quality["roe"], 1) if quality["roe"] is not None else None,
            "profit_margin": round(quality["profit_margin"], 1)
            if quality["profit_margin"] is not None
            else None,
            "quality_score": quality["quality_score"],
            "stability_score": stability_score,
            "composite_score": round(composite_score, 1),
        }

        results.append(result)
        print(
            f"  ✅ Passed all criteria (RSI: {rsi:.1f}, Score: {result['composite_score']})",
            file=sys.stderr,
        )

    # Step 3: Filter by RSI
    # Prefer RSI <= 40 (oversold), but if none found, return lowest RSI stocks
    oversold_results = [r for r in results if r["rsi"] <= 40]

    if oversold_results:
        print(
            f"\nStep 3: Found {len(oversold_results)} oversold stocks (RSI <= 40)", file=sys.stderr
        )
        # Sort oversold stocks by composite score
        oversold_results.sort(key=lambda x: x["composite_score"], reverse=True)
        results = oversold_results[:top_n]
    else:
        print(
            "\nStep 3: No oversold stocks found (RSI <= 40). Returning lowest RSI stocks.",
            file=sys.stderr,
        )
        # Sort by RSI (lowest first), then by composite score
        results.sort(key=lambda x: (x["rsi"], -x["composite_score"]))
        results = results[:top_n]

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Screen value dividend stocks using the TradingView data layer (no API key required)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # S&P 500 universe via TradingView (default, no API key)
  python3 screen_dividend_stocks.py

  # Two-stage screening: FINVIZ pre-screen + TradingView detailed analysis
  python3 screen_dividend_stocks.py --use-finviz

  # Custom output location
  python3 screen_dividend_stocks.py --output /path/to/results.json

  # Get top 50 stocks
  python3 screen_dividend_stocks.py --top 50

Environment Variables:
  FINVIZ_API_KEY    - FINVIZ Elite API key (required for --use-finviz)

Requires a running TradingView Desktop chart (CDP on :9222) or a fresh
state/metrics cache; the legacy FMP_API_KEY / --fmp-api-key inputs are
accepted but ignored.
        """,
    )

    parser.add_argument(
        "--fmp-api-key",
        type=str,
        help="DEPRECATED: ignored (TradingView data layer needs no FMP key)",
    )

    parser.add_argument(
        "--finviz-api-key",
        type=str,
        help="FINVIZ Elite API key (or set FINVIZ_API_KEY environment variable)",
    )

    parser.add_argument(
        "--use-finviz",
        action="store_true",
        help="Use FINVIZ Elite API for pre-screening (widens the universe beyond the S&P 500)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: <repo>/reports/value_dividend_results_<date>.json)",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory for the JSON output (default: <repo>/reports)",
    )

    parser.add_argument(
        "--top", type=int, default=20, help="Number of top stocks to return (default: 20)"
    )

    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Maximum candidates to analyze (default: all)",
    )

    args = parser.parse_args()

    if args.fmp_api_key or os.environ.get("FMP_API_KEY"):
        print(
            "NOTE: FMP key detected but ignored — data comes from TradingView.",
            file=sys.stderr,
        )

    # FINVIZ pre-screening (optional)
    finviz_symbols = None
    if args.use_finviz:
        finviz_api_key = args.finviz_api_key or os.environ.get("FINVIZ_API_KEY")
        if not finviz_api_key:
            print(
                "ERROR: FINVIZ API key required when using --use-finviz. Provide via --finviz-api-key or FINVIZ_API_KEY environment variable",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"\n{'=' * 60}", file=sys.stderr)
        print("VALUE DIVIDEND STOCK SCREENER (TWO-STAGE)", file=sys.stderr)
        print(f"{'=' * 60}\n", file=sys.stderr)

        finviz_client = FINVIZClient(finviz_api_key)
        finviz_symbols = finviz_client.screen_stocks()

        if not finviz_symbols:
            print("ERROR: FINVIZ pre-screening failed or returned no results", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print("VALUE DIVIDEND STOCK SCREENER (TRADINGVIEW)", file=sys.stderr)
        print(f"{'=' * 60}\n", file=sys.stderr)

    # Run detailed screening
    results = screen_value_dividend_stocks(
        top_n=args.top, finviz_symbols=finviz_symbols, max_candidates=args.max_candidates
    )

    if not results:
        print("\nNo stocks found matching all criteria.", file=sys.stderr)
        sys.exit(1)

    # Add metadata
    output_data = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            "data_source": "TradingView (tv_client data layer)",
            "criteria": {
                "dividend_yield_min": 3.0,
                "pe_ratio_max": 20,
                "pb_ratio_max": 2,
                "dividend_cagr_min": 4.0,
                "dividend_stability": "low volatility, year-over-year growth",
                "revenue_trend": "positive over 3 years",
                "eps_trend": "positive over 3 years",
            },
            "scoring": {
                "dividend_growth": "max 15 points (10%+ CAGR)",
                "dividend_stability": "max 20 points (stable, growing)",
                "revenue_growth": "max 10 points (10%+ CAGR)",
                "eps_growth": "max 10 points (15%+ CAGR)",
                "dividend_sustainable": "10 points",
                "financial_health": "10 points",
                "quality_score": "max 25 points",
            },
            "total_results": len(results),
        },
        "stocks": results,
    }

    # Resolve output path (repo reports/ by convention)
    if args.output:
        output_path = args.output
    else:
        if args.output_dir:
            out_dir = args.output_dir
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            # Navigate from skills/value-dividend-screener/scripts to project root
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
            out_dir = os.path.join(project_root, "reports")
        today = datetime.now().date().isoformat()
        output_path = os.path.join(out_dir, f"value_dividend_results_{today}.json")

    out_parent = os.path.dirname(output_path)
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"✅ Screening complete! Found {len(results)} stocks.", file=sys.stderr)
    print(f"📄 Results saved to: {output_path}", file=sys.stderr)
    print(f"{'=' * 60}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
