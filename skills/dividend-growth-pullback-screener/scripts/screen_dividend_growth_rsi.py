#!/usr/bin/env python3
"""
Dividend Growth Pullback Screener — TradingView data layer (no FMP key).

Two screening modes:
1. Default: S&P 500 universe, all data (annual DPS history, fundamentals,
   daily bars for RSI) from a live TradingView Desktop chart via the shared
   tv_client data layer. No API key, no request quota.
2. --use-finviz: FINVIZ Elite pre-screen narrows the universe beyond the
   S&P 500 (dividend growth + RSI filters), then TradingView supplies the
   detailed analysis. Requires FINVIZ_API_KEY.

Screens for high-quality dividend growth stocks (12%+ dividend CAGR, 1.5%+ yield)
that are experiencing temporary pullbacks identified by RSI oversold conditions (RSI ≤40).

Usage:
    # S&P 500 universe via TradingView (default)
    python3 screen_dividend_growth_rsi.py

    # Two-stage screening with FINVIZ pre-screen
    python3 screen_dividend_growth_rsi.py --use-finviz

Environment variables:
    export FINVIZ_API_KEY=your_finviz_key_here  # Required for --use-finviz
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, datetime
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

        Criteria for dividend growth pullback opportunities (Balanced):
        - Market cap: Mid-cap or higher
        - Dividend yield: 0.5-3% (captures dividend growers without REITs/utilities)
        - Dividend growth (3Y): 10%+ (we'll verify 12%+ with FMP)
        - EPS growth (3Y): 5%+ (positive earnings momentum)
        - Sales growth (3Y): 5%+ (positive revenue momentum)
        - RSI (14): Under 40 (oversold/pullback)
        - Geography: USA

        Returns:
            Set of stock symbols
        """
        # Build filter string in FINVIZ format: key_value,key_value,...
        # Balanced criteria: Div Growth 10%+, EPS/Sales Growth 5%+ (30-40 candidates expected)
        filters = "cap_midover,fa_div_0.5to3,fa_divgrowth_3yo10,fa_eps3years_o5,fa_sales3years_o5,geo_usa,ta_rsi_os40"

        params = {
            "v": "151",  # View type
            "f": filters,  # Filter conditions
            "ft": "4",  # File type: CSV export
            "auth": self.api_key,
        }

        try:
            print("Fetching pre-screened stocks from FINVIZ Elite API...", file=sys.stderr)
            print(
                "FINVIZ Filters: Div Yield 0.5-3%, Div Growth 10%+, EPS Growth 5%+, Sales Growth 5%+, RSI <40",
                file=sys.stderr,
            )
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
    """Calculate Relative Strength Index (RSI) from price data."""

    @staticmethod
    def calculate_rsi(prices: list[float], period: int = 14) -> Optional[float]:
        """
        Calculate RSI using standard formula.

        Args:
            prices: List of closing prices (oldest first)
            period: RSI period (default 14)

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(prices) < period + 1:
            return None

        # Calculate price changes
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # Separate gains and losses
        gains = [change if change > 0 else 0 for change in changes]
        losses = [-change if change < 0 else 0 for change in changes]

        # Calculate initial average gain and loss
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Calculate smoothed averages for remaining periods
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        # Calculate RSI
        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 2)


class StockAnalyzer:
    """Analyze stock fundamentals and dividend growth."""

    @staticmethod
    def calculate_cagr(start_value: float, end_value: float, years: int) -> Optional[float]:
        """Calculate Compound Annual Growth Rate."""
        if start_value <= 0 or end_value <= 0 or years <= 0:
            return None
        return round(((end_value / start_value) ** (1 / years) - 1) * 100, 2)

    @staticmethod
    def analyze_dividend_growth(
        dividend_history: list[dict],
    ) -> tuple[Optional[float], bool, Optional[float], int]:
        """
        Analyze dividend growth rate (3-year CAGR and consistency) and return latest annual dividend.

        Returns:
            Tuple of (CAGR%, consistent_growth, latest_annual_dividend, years_of_growth)
        """
        if not dividend_history or "historical" not in dividend_history:
            return None, False, None, 0

        dividends = dividend_history["historical"]
        if len(dividends) < 4:
            return None, False, None, 0

        # Sort by date and aggregate by year
        dividends = sorted(dividends, key=lambda x: x["date"])
        annual_dividends = {}
        for div in dividends:
            year = div["date"][:4]
            annual_dividends[year] = annual_dividends.get(year, 0) + div.get("dividend", 0)

        # Exclude current year because partial-year dividends distort CAGR calculations.
        current_year = str(date.today().year)
        annual_dividends.pop(current_year, None)

        if len(annual_dividends) < 4:
            return None, False, None, 0

        # Get all available years sorted (oldest first)
        all_years = sorted(annual_dividends.keys())
        all_div_values = [annual_dividends[y] for y in all_years]

        # Get last 4 years for CAGR calculation
        years = all_years[-4:]
        div_values = [annual_dividends[y] for y in years]

        # Calculate 3-year CAGR
        cagr = StockAnalyzer.calculate_cagr(div_values[0], div_values[-1], 3)

        # Check consistency (no significant cuts)
        consistent = all(
            div_values[i] >= div_values[i - 1] * 0.95 for i in range(1, len(div_values))
        )

        # Count consecutive years of growth (from most recent going back)
        years_of_growth = 0
        for i in range(len(all_div_values) - 1, 0, -1):
            if all_div_values[i] >= all_div_values[i - 1] * 0.95:  # Allow 5% tolerance
                years_of_growth += 1
            else:
                break

        # Latest annual dividend
        latest_annual_dividend = div_values[-1]

        return cagr, consistent, latest_annual_dividend, years_of_growth

    @staticmethod
    def is_reit(stock_data: dict) -> bool:
        """
        Determine if a stock is a REIT based on sector/industry.

        Args:
            stock_data: Dict containing sector and/or industry fields

        Returns:
            True if the stock is likely a REIT
        """
        sector = stock_data.get("sector", "").lower()
        industry = stock_data.get("industry", "").lower()

        # Check for Real Estate sector or REIT in industry
        if "real estate" in sector:
            return True
        if "reit" in industry:
            return True

        return False

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
    def get_payout_ratio_from_metrics(key_metrics: list[dict]) -> Optional[float]:
        """
        Get payout ratio directly from key_metrics as fallback.

        Args:
            key_metrics: List of key metrics (newest first)

        Returns:
            Payout ratio as percentage, or None if not available
        """
        if not key_metrics:
            return None

        latest = key_metrics[0]
        payout_ratio = latest.get("payoutRatio")

        if payout_ratio is not None:
            # payoutRatio from FMP is a decimal (e.g., 0.316 = 31.6%)
            return round(payout_ratio * 100, 1)

        return None

    @staticmethod
    def analyze_financial_health(key_metrics: dict) -> dict:
        """Analyze financial health from the scanner's snapshot ratios
        (debt_to_equity_fq / current_ratio_fq, already computed by
        TradingView — no balance-sheet arithmetic needed)."""
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
    def analyze_growth_metrics(income_stmts: list[dict]) -> dict:
        """Analyze revenue and EPS growth trends."""
        if not income_stmts or len(income_stmts) < 4:
            return {"revenue_cagr_3y": None, "eps_cagr_3y": None}

        # Sort by date (newest first from API)
        revenue_3y_ago = income_stmts[3].get("revenue", 0)
        revenue_latest = income_stmts[0].get("revenue", 0)

        eps_3y_ago = income_stmts[3].get("eps", 0)
        eps_latest = income_stmts[0].get("eps", 0)

        revenue_cagr = StockAnalyzer.calculate_cagr(revenue_3y_ago, revenue_latest, 3)
        eps_cagr = StockAnalyzer.calculate_cagr(eps_3y_ago, eps_latest, 3)

        return {"revenue_cagr_3y": revenue_cagr, "eps_cagr_3y": eps_cagr}

    @staticmethod
    def calculate_composite_score(stock_data: dict) -> float:
        """
        Calculate composite score (0-100) based on:
        - Dividend Growth (40%): Reward higher CAGR
        - Financial Quality (30%): ROE, profit margins, debt levels
        - Technical Setup (20%): Lower RSI = better entry opportunity
        - Valuation (10%): P/E and P/B for context
        """
        score = 0.0

        # Dividend Growth Score (40 points max)
        div_cagr = stock_data.get("dividend_cagr_3y", 0)
        if div_cagr >= 20:
            score += 40
        elif div_cagr >= 15:
            score += 35
        elif div_cagr >= 12:
            score += 30
        else:
            score += 20

        # Add bonus for consistency
        if stock_data.get("dividend_consistent", False):
            score += 5

        # Financial Quality Score (30 points max)
        roe = stock_data.get("roe") or 0
        profit_margin = stock_data.get("profit_margin") or 0
        # None = unknown (e.g. negative equity) — treat as no leverage bonus
        debt_to_equity = stock_data.get("debt_to_equity")
        if debt_to_equity is None:
            debt_to_equity = 999

        if roe >= 20:
            score += 12
        elif roe >= 15:
            score += 10
        elif roe >= 10:
            score += 7
        else:
            score += 3

        if profit_margin >= 20:
            score += 10
        elif profit_margin >= 15:
            score += 8
        elif profit_margin >= 10:
            score += 6
        else:
            score += 3

        if debt_to_equity < 0.5:
            score += 8
        elif debt_to_equity < 1.0:
            score += 6
        elif debt_to_equity < 2.0:
            score += 3

        # Technical Setup Score (20 points max) - Lower RSI = Higher score
        rsi = stock_data.get("rsi", 50)
        if rsi <= 25:
            score += 20  # Extreme oversold
        elif rsi <= 30:
            score += 18
        elif rsi <= 35:
            score += 15
        elif rsi <= 40:
            score += 12
        else:
            score += 5

        # Valuation Score (10 points max) - Context only, not exclusionary
        pe_ratio = stock_data.get("pe_ratio", 999)
        pb_ratio = stock_data.get("pb_ratio", 999)

        if pe_ratio < 15:
            score += 5
        elif pe_ratio < 25:
            score += 3

        if pb_ratio < 3:
            score += 5
        elif pb_ratio < 5:
            score += 3

        return round(min(score, 100), 1)


def screen_dividend_growth_pullbacks(
    api_key: Optional[str] = None,
    min_yield: float = 1.5,
    min_div_growth: float = 12.0,
    rsi_max: float = 40.0,
    max_candidates: int = None,
    finviz_symbols: Optional[set[str]] = None,
) -> list[dict]:
    """
    Main screening function (TradingView data layer).

    Args:
        api_key: Unused; accepted for interface parity (TradingView needs no key)
        min_yield: Minimum dividend yield % (default 1.5%)
        min_div_growth: Minimum 3-year dividend CAGR % (default 12%)
        rsi_max: Maximum RSI value (default 40)
        max_candidates: Maximum number of candidates to analyze (None = all)
        finviz_symbols: Optional set of symbols from FINVIZ pre-screening

    Returns:
        List of qualified stocks with full analysis
    """
    client = FMPClient(api_key)  # TVClient drop-in; no key, no request quota
    analyzer = StockAnalyzer()
    rsi_calc = RSICalculator()

    print(f"\n{'=' * 80}", file=sys.stderr)
    print("Dividend Growth Pullback Screener (TradingView data layer)", file=sys.stderr)
    print(f"{'=' * 80}", file=sys.stderr)
    print("\nCriteria:", file=sys.stderr)
    print(f"  - Dividend Yield ≥ {min_yield}%", file=sys.stderr)
    print(f"  - Dividend Growth (3Y CAGR) ≥ {min_div_growth}%", file=sys.stderr)
    print(f"  - RSI ≤ {rsi_max}", file=sys.stderr)
    print("  - Market Cap ≥ $2B", file=sys.stderr)
    print(f"\n{'=' * 80}\n", file=sys.stderr)

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

    # Limit candidates if specified
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

        # Get current price
        current_price = stock.get("price") or 0
        if current_price <= 0:
            print("  ⚠️  No valid price data", file=sys.stderr)
            continue

        # Cheap pre-filter on the scanner's current yield (same snapshot the
        # profile came from — no extra fetch). 0.7x slack so borderline names
        # still reach the exact DPS/price check below.
        key_metrics = client.get_key_metrics(symbol, limit=1)
        latest_metrics = key_metrics[0] if key_metrics else {}
        yield_now = latest_metrics.get("dividendYield")
        if yield_now is None or yield_now < min_yield * 0.7:
            print(f"  ⚠️  Current yield {yield_now}% too low (pre-filter)", file=sys.stderr)
            continue

        # Fetch dividend history (annual DPS series from the scanner)
        dividend_history = client.get_dividend_history(symbol)
        if not dividend_history:
            print("  ⚠️  No dividend history", file=sys.stderr)
            continue

        # Analyze dividend growth
        div_cagr, div_consistent, annual_dividend, div_years_of_growth = (
            analyzer.analyze_dividend_growth(dividend_history)
        )
        if not div_cagr or div_cagr < min_div_growth:
            print(f"  ⚠️  Dividend CAGR {div_cagr}% < {min_div_growth}%", file=sys.stderr)
            continue

        if not annual_dividend:
            print("  ⚠️  Cannot determine annual dividend", file=sys.stderr)
            continue

        # Calculate actual dividend yield
        actual_dividend_yield = (annual_dividend / current_price) * 100

        if actual_dividend_yield < min_yield:
            print(
                f"  ⚠️  Dividend yield {actual_dividend_yield:.2f}% < {min_yield}%", file=sys.stderr
            )
            continue

        print(
            f"  ✓ Dividend: {actual_dividend_yield:.2f}% yield, {div_cagr}% CAGR", file=sys.stderr
        )

        # Fetch historical prices for RSI (newest-first bars; slice the
        # ~30-day window the FMP version used so RSI smoothing matches)
        price_data = client.get_historical_prices(symbol, days=30)
        historical_prices = (price_data or {}).get("historical") or []
        historical_prices = historical_prices[:30]

        if len(historical_prices) < 20:
            print("  ⚠️  Insufficient price data for RSI calculation", file=sys.stderr)
            continue

        # Calculate RSI
        prices = [p["close"] for p in reversed(historical_prices)]  # Oldest first
        rsi = rsi_calc.calculate_rsi(prices, period=14)

        if rsi is None:
            print("  ⚠️  RSI calculation failed", file=sys.stderr)
            continue

        if rsi > rsi_max:
            print(f"  ⚠️  RSI {rsi} > {rsi_max}", file=sys.stderr)
            continue

        print(f"  ✓ RSI: {rsi} (oversold)", file=sys.stderr)

        # Annual income statements (scanner fiscal-year series)
        income_stmts = client.get_income_statement(symbol, period="annual", limit=5)

        # Analyze growth metrics
        growth_metrics = analyzer.analyze_growth_metrics(income_stmts if income_stmts else [])

        # Check for positive revenue and EPS growth
        revenue_cagr = growth_metrics.get("revenue_cagr_3y")
        eps_cagr = growth_metrics.get("eps_cagr_3y")

        if revenue_cagr is not None and revenue_cagr < 0:
            print("  ⚠️  Negative revenue growth", file=sys.stderr)
            continue

        if eps_cagr is not None and eps_cagr < 0:
            print("  ⚠️  Negative EPS growth", file=sys.stderr)
            continue

        # Analyze financial health (snapshot D/E and current ratio)
        health_metrics = analyzer.analyze_financial_health(latest_metrics)

        if not health_metrics.get("financially_healthy", False):
            print("  ⚠️  Financial health concerns", file=sys.stderr)
            continue

        # Check if this is a REIT (uses different payout ratio calculation)
        is_reit = analyzer.is_reit(stock)

        # Calculate payout ratios from the key-metrics snapshot
        payout_ratios = analyzer.calculate_payout_ratios_from_metrics(
            latest_metrics, is_reit=is_reit
        )
        payout_ratio = payout_ratios["payout_ratio"]
        fcf_payout_ratio = payout_ratios["fcf_payout_ratio"]

        # Determine dividend sustainability
        # Sustainable if payout ratio < 80% and FCF covers dividends
        dividend_sustainable = False
        if payout_ratio and fcf_payout_ratio:
            dividend_sustainable = payout_ratio < 80 and fcf_payout_ratio < 100
        elif payout_ratio:
            dividend_sustainable = payout_ratio < 80

        # Build result object
        result = {
            "symbol": symbol,
            "company_name": company_name,
            "sector": stock.get("sector") or "Unknown",
            "market_cap": market_cap,
            "price": current_price,
            "dividend_yield": round(actual_dividend_yield, 2),
            "annual_dividend": round(annual_dividend, 2),
            "dividend_cagr_3y": div_cagr,
            "dividend_consistent": div_consistent,
            "rsi": rsi,
            "pe_ratio": latest_metrics.get("peRatio") or 0,
            "pb_ratio": latest_metrics.get("pbRatio") or 0,
            "revenue_cagr_3y": revenue_cagr,
            "eps_cagr_3y": eps_cagr,
            "payout_ratio": payout_ratio,
            "fcf_payout_ratio": fcf_payout_ratio,
            "dividend_sustainable": dividend_sustainable,
            "dividend_years_of_growth": div_years_of_growth,
            "debt_to_equity": health_metrics.get("debt_to_equity"),
            "current_ratio": health_metrics.get("current_ratio"),
            "financially_healthy": health_metrics.get("financially_healthy", False),
            # PERCENT values (TradingView native scale — matches the
            # composite-score thresholds, e.g. roe >= 20)
            "roe": latest_metrics.get("roe") or 0,
            "profit_margin": latest_metrics.get("netProfitMargin") or 0,
        }

        # Calculate composite score
        result["composite_score"] = analyzer.calculate_composite_score(result)

        results.append(result)
        print(f"  ✅ QUALIFIED - Score: {result['composite_score']}", file=sys.stderr)

    # Sort by composite score
    results.sort(key=lambda x: x["composite_score"], reverse=True)

    print(f"\n{'=' * 80}", file=sys.stderr)
    print("Screening Complete!", file=sys.stderr)
    print(f"Qualified Stocks: {len(results)}", file=sys.stderr)
    print(f"{'=' * 80}\n", file=sys.stderr)

    return results


def generate_markdown_report(results: list[dict], criteria: dict, output_path: str):
    """Generate human-readable markdown report."""

    report = f"""# Dividend Growth Pullback Screening Report

**Generated:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC
**Data Source:** Financial Modeling Prep API

## Executive Summary

**Total Qualified Stocks:** {len(results)}

### Screening Criteria

- **Dividend Yield:** ≥ {criteria["dividend_yield_min"]}%
- **Dividend Growth (3Y CAGR):** ≥ {criteria["dividend_cagr_min"]}%
- **RSI:** ≤ {criteria["rsi_max"]} (oversold/pullback)
- **Market Cap:** ≥ $2 billion
- **Financial Health:** Positive revenue/EPS growth, D/E < 2.0, Current Ratio > 1.0

---

"""

    if not results:
        report += """## No Stocks Qualified

**Possible Reasons:**
- Strong bull market with few oversold stocks
- Dividend growth criteria (12%+) is very selective
- RSI threshold may be too strict for current market conditions

**Recommendations:**
- Relax RSI threshold to ≤45 for early pullback phase
- Lower dividend growth to ≥10% for more candidates
- Check back during market corrections or sector rotations

"""
    else:
        for i, stock in enumerate(results, 1):
            rsi_interpretation = (
                "Extreme Oversold"
                if stock["rsi"] < 30
                else "Strong Oversold"
                if stock["rsi"] < 35
                else "Early Pullback"
            )

            report += f"""## {i}. {stock["symbol"]} - {stock["company_name"]}

**Sector:** {stock["sector"]}
**Market Cap:** ${stock["market_cap"] / 1e9:.1f}B
**Current Price:** ${stock["price"]:.2f}
**Composite Score:** {stock["composite_score"]}/100

### Dividend Growth Profile

| Metric | Value | Assessment |
|--------|-------|------------|
| Dividend Yield | **{stock["dividend_yield"]:.2f}%** | {
                "✓ Above 2%" if stock["dividend_yield"] >= 2 else "⚠ Below 2%"
            } |
| Annual Dividend | ${stock["annual_dividend"]:.2f} | |
| 3Y Dividend CAGR | **{stock["dividend_cagr_3y"]:.2f}%** | {
                "🔥 Exceptional"
                if stock["dividend_cagr_3y"] >= 20
                else "✓ Excellent"
                if stock["dividend_cagr_3y"] >= 15
                else "✓ Strong"
            } |
| Dividend Consistency | {"Yes" if stock["dividend_consistent"] else "No"} | {
                "✓" if stock["dividend_consistent"] else "⚠"
            } |
| Payout Ratio | {f"{stock['payout_ratio']:.1f}%" if stock["payout_ratio"] else "N/A"} | {
                "✓ Sustainable"
                if stock["payout_ratio"] and stock["payout_ratio"] < 70
                else "⚠ High"
                if stock["payout_ratio"] and stock["payout_ratio"] < 100
                else "❌ Risk"
                if stock["payout_ratio"]
                else "N/A"
            } |

### Technical Setup

| Metric | Value | Interpretation |
|--------|-------|----------------|
| RSI (14-period) | **{stock["rsi"]:.1f}** | {rsi_interpretation} |
| Entry Timing | {
                "Immediate - Scale in 50%"
                if stock["rsi"] < 30
                else "Good - Full position OK"
                if stock["rsi"] < 35
                else "Conservative - High conviction"
            } | |
| Stop Loss Suggestion | {
                f"{((stock['rsi'] - 30) / 2 + 3):.0f}% below entry"
                if stock["rsi"] >= 30
                else "8% below entry"
            } | |

**RSI Context:** {
                "Extreme oversold reading suggests panic selling or negative news. Wait for RSI to turn up (>30) before entry to confirm stabilization."
                if stock["rsi"] < 30
                else "Strong oversold in uptrend. Normal correction creating entry opportunity. Can initiate position with standard risk management."
                if stock["rsi"] < 35
                else "Early pullback in uptrend. Conservative entry point with lower risk of further decline. Suitable for high-conviction additions."
            }

### Business Fundamentals

| Metric | Value | Status |
|--------|-------|--------|
| Revenue CAGR (3Y) | {
                f"{stock['revenue_cagr_3y']:.2f}%" if stock["revenue_cagr_3y"] else "N/A"
            } | {"✓" if stock["revenue_cagr_3y"] and stock["revenue_cagr_3y"] > 0 else "⚠"} |
| EPS CAGR (3Y) | {f"{stock['eps_cagr_3y']:.2f}%" if stock["eps_cagr_3y"] else "N/A"} | {
                "✓" if stock["eps_cagr_3y"] and stock["eps_cagr_3y"] > 0 else "⚠"
            } |
| ROE | {f"{stock['roe']:.1f}%" if stock["roe"] else "N/A"} | {
                "✓ Excellent"
                if stock["roe"] and stock["roe"] >= 20
                else "✓ Good"
                if stock["roe"] and stock["roe"] >= 15
                else "⚠ Moderate"
                if stock["roe"]
                else "N/A"
            } |
| Net Profit Margin | {f"{stock['profit_margin']:.1f}%" if stock["profit_margin"] else "N/A"} | {
                "✓" if stock["profit_margin"] and stock["profit_margin"] >= 10 else "⚠"
            } |

### Financial Health

| Metric | Value | Status |
|--------|-------|--------|
| Debt-to-Equity | {
                f"{stock['debt_to_equity']:.2f}" if stock["debt_to_equity"] is not None else "N/A"
            } | {
                "✓ Very Low"
                if stock["debt_to_equity"] and stock["debt_to_equity"] < 0.5
                else "✓ Low"
                if stock["debt_to_equity"] and stock["debt_to_equity"] < 1.0
                else "⚠ Moderate"
                if stock["debt_to_equity"]
                else "N/A"
            } |
| Current Ratio | {f"{stock['current_ratio']:.2f}" if stock["current_ratio"] else "N/A"} | {
                "✓ Healthy"
                if stock["current_ratio"] and stock["current_ratio"] > 1.2
                else "⚠ Adequate"
                if stock["current_ratio"]
                else "N/A"
            } |

### Investment Thesis

**10-Year Dividend Projection ({stock["dividend_cagr_3y"]:.0f}% CAGR):**
- Current Yield on Cost: {stock["dividend_yield"]:.2f}%
- Year 5 Yield on Cost: {stock["dividend_yield"] * (1 + stock["dividend_cagr_3y"] / 100) ** 5:.2f}%
- Year 10 Yield on Cost: {stock["dividend_yield"] * (1 + stock["dividend_cagr_3y"] / 100) ** 10:.2f}%

**Entry Strategy:**
{f"- RSI {stock['rsi']:.0f} indicates {rsi_interpretation.lower()} condition"}
- {
                "Scale in with 50% position now, add remaining on RSI >30 confirmation"
                if stock["rsi"] < 30
                else f"Full position acceptable with stop loss {((stock['rsi'] - 30) / 2 + 3):.0f}% below entry"
                if stock["rsi"] < 35
                else "Conservative entry for high-conviction add with 3-5% stop loss"
            }
- Time horizon: 6-12 months minimum (long-term dividend growth play)

**Risk Factors:**
{
                f"- Payout ratio {stock['payout_ratio']:.0f}% limits dividend growth runway"
                if stock["payout_ratio"] and stock["payout_ratio"] > 70
                else "- Monitor payout ratio sustainability"
            }
{
                f"- Debt-to-equity {stock['debt_to_equity']:.1f} requires monitoring"
                if stock["debt_to_equity"] and stock["debt_to_equity"] > 1.0
                else ""
            }
- RSI can remain oversold in downtrends - watch for reversal confirmation
- Dividend growth may slow if business growth moderates

---

"""

    report += f"""
## Methodology

This screening combines fundamental dividend analysis with technical timing indicators:

1. **Fundamental Filter:** Dividend yield ≥{criteria["dividend_yield_min"]}%, dividend CAGR ≥{criteria["dividend_cagr_min"]}%, positive business growth
2. **Technical Filter:** RSI ≤{criteria["rsi_max"]} identifies temporary pullbacks in quality stocks
3. **Quality Filter:** Financial health checks (debt, liquidity, profitability)
4. **Ranking:** Composite score balancing dividend growth (40%), quality (30%), technical setup (20%), valuation (10%)

**Investment Philosophy:**
High dividend growth stocks (12%+ CAGR) compound wealth through rising dividends rather than high current yield. A 1.5% yielding stock growing dividends at 15%/year becomes a 4% yielder in 6 years and 9% yielder in 12 years - far superior to a 4% yielder growing at 3%/year. Buying during RSI oversold conditions (≤40) enhances returns by entering at technical support levels.

---

**Disclaimer:** This report is for informational purposes only. Past dividend growth does not guarantee future performance. RSI oversold conditions do not guarantee price reversals. Conduct thorough due diligence and consult a financial advisor before making investment decisions.

**Report Generated:** {datetime.utcnow().isoformat()}Z
"""

    # Write report
    with open(output_path, "w") as f:
        f.write(report)

    print(f"✅ Markdown report saved: {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Screen dividend growth stocks with RSI oversold using the TradingView data layer (no API key required)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # S&P 500 universe via TradingView (default, no API key)
  python3 screen_dividend_growth_rsi.py

  # Two-stage screening: FINVIZ pre-screen + TradingView detailed analysis
  python3 screen_dividend_growth_rsi.py --use-finviz

  # Custom parameters
  python3 screen_dividend_growth_rsi.py --min-yield 2.0 --min-div-growth 15.0 --rsi-max 35

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
        "--min-yield", type=float, default=1.5, help="Minimum dividend yield %% (default: 1.5)"
    )
    parser.add_argument(
        "--min-div-growth",
        type=float,
        default=12.0,
        help="Minimum 3-year dividend CAGR %% (default: 12.0)",
    )
    parser.add_argument(
        "--rsi-max", type=float, default=40.0, help="Maximum RSI value (default: 40.0)"
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Maximum candidates to analyze (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory for JSON/Markdown outputs (default: <repo>/reports)",
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

        print(f"\n{'=' * 80}", file=sys.stderr)
        print("DIVIDEND GROWTH PULLBACK SCREENER (TWO-STAGE)", file=sys.stderr)
        print(f"{'=' * 80}\n", file=sys.stderr)

        finviz_client = FINVIZClient(finviz_api_key)
        finviz_symbols = finviz_client.screen_stocks()

        if not finviz_symbols:
            print("ERROR: No stocks found in FINVIZ pre-screening", file=sys.stderr)
            sys.exit(1)

        print(f"\n{'=' * 80}\n", file=sys.stderr)

    # Run screening
    results = screen_dividend_growth_pullbacks(
        min_yield=args.min_yield,
        min_div_growth=args.min_div_growth,
        rsi_max=args.rsi_max,
        max_candidates=args.max_candidates,
        finviz_symbols=finviz_symbols,
    )

    # Prepare metadata
    criteria = {
        "dividend_yield_min": args.min_yield,
        "dividend_cagr_min": args.min_div_growth,
        "rsi_max": args.rsi_max,
        "revenue_trend": "positive over 3 years",
        "eps_trend": "positive over 3 years",
    }

    # Generate outputs
    today = date.today().isoformat()

    # Determine output directory (repo reports/ by convention, or --output-dir)
    if args.output_dir:
        out_dir = args.output_dir
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Navigate from skills/dividend-growth-pullback-screener/scripts to project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
        out_dir = os.path.join(project_root, "reports")
    os.makedirs(out_dir, exist_ok=True)

    # JSON output
    json_output = {
        "metadata": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "criteria": criteria,
            "total_results": len(results),
        },
        "stocks": results,
    }

    json_path = os.path.join(out_dir, f"dividend_growth_pullback_results_{today}.json")
    with open(json_path, "w") as f:
        json.dump(json_output, f, indent=2)

    print(f"✅ JSON results saved: {json_path}", file=sys.stderr)

    # Markdown report
    md_path = os.path.join(out_dir, f"dividend_growth_pullback_screening_{today}.md")
    generate_markdown_report(results, criteria, md_path)

    print(f"\n{'=' * 80}", file=sys.stderr)
    print(f"Screening complete! Found {len(results)} qualified stocks.", file=sys.stderr)
    print(f"{'=' * 80}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
