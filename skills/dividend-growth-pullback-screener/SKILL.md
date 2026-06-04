---
name: dividend-growth-pullback-screener
description: Use this skill to find high-quality dividend growth stocks (12%+ annual dividend growth, 1.5%+ yield) that are experiencing temporary pullbacks, identified by RSI oversold conditions (RSI ≤40). This skill combines fundamental dividend analysis with technical timing indicators to identify buying opportunities in strong dividend growers during short-term weakness.
---

# Dividend Growth Pullback Screener

## Overview

This skill screens for dividend growth stocks that exhibit strong fundamental characteristics but are experiencing temporary technical weakness. It targets stocks with exceptional dividend growth rates (12%+ CAGR) that have pulled back to RSI oversold levels (≤40), creating potential entry opportunities for long-term dividend growth investors.

**Investment Thesis:** High-quality dividend growth stocks (often yielding 1-2.5%) compound wealth through dividend increases rather than high current yield. Buying these stocks during temporary pullbacks (RSI ≤40) can enhance total returns by combining strong fundamental growth with favorable technical entry timing.

## When to Use This Skill

Use this skill when:
- Looking for dividend growth stocks with exceptional compounding potential (12%+ dividend CAGR)
- Seeking entry opportunities in quality stocks during temporary market weakness
- Willing to accept lower current yields (1.5-3%) for higher dividend growth
- Focusing on total return over 5-10 years rather than current income
- Market conditions show sector rotations or broad pullbacks affecting quality names

**Do NOT use when:**
- Seeking high current income (use value-dividend-screener instead)
- Requiring immediate dividend yields >3%
- Looking for deep value plays with strict P/E or P/B requirements
- Short-term trading focus (<6 months)

## Prerequisites

- **TradingView data layer** (required): a running TradingView Desktop chart (CDP on :9222) or a fresh `state/metrics` snapshot cache. The screener routes all data (annual DPS history, fundamentals, daily bars for RSI) through the shared `scripts/lib/tv_client.py` — **no API key and no request quota**.
- **FINVIZ Elite API key** (optional): Set `FINVIZ_API_KEY` environment variable or pass `--finviz-api-key`. Widens the screening universe beyond the S&P 500 with a pre-filtered candidate list. [Sign up](https://elite.finviz.com/).
- Python 3.8+. The `requests` library is needed only for the optional FINVIZ pre-screen.

> Legacy `FMP_API_KEY` / `--fmp-api-key` inputs are accepted but ignored — the FMP data path was replaced by TradingView.

## Screening Workflow

### Step 1: Choose the Universe

#### S&P 500 via TradingView (default)

No setup needed beyond a running TradingView Desktop chart. The screener walks the committed S&P 500 constituents list and reads everything from the TradingView scanner + chart bars.

#### FINVIZ Pre-Screen (optional, wider universe)

```bash
export FINVIZ_API_KEY=your_finviz_key_here
```

**Why FINVIZ?**
- Pre-screens the whole US market (mid-cap+) with dividend-growth and RSI filters in 1 API call
- TradingView then supplies the detailed analysis for the ~10-50 pre-screened candidates

### Step 2: Execute Screening

**Default S&P 500 screening:**

```bash
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py
```

This executes:
1. Cheap pre-filter on scanner current yield, then annual-DPS dividend CAGR verification (12%+)
2. 14-period RSI from daily bars; oversold filter (RSI ≤40)
3. Revenue/EPS trend, financial health, and payout sustainability checks

**Two-Stage Screening (FINVIZ + TradingView):**

```bash
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py --use-finviz
```

1. FINVIZ pre-screen: Dividend yield 0.5-3%, Dividend growth 10%+, EPS growth 5%+, Sales growth 5%+, RSI <40
2. TradingView detailed analysis: Verify 12%+ dividend CAGR, calculate exact RSI, analyze fundamentals

**Customization Options:**

```bash
# Custom thresholds
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py \
  --min-yield 2.0 --min-div-growth 15.0 --rsi-max 35

# Limit the number of analyzed candidates / change output location
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py \
  --max-candidates 100 --output-dir reports/
```

### Step 3: Review Results

The script generates two outputs:

1. **JSON file:** `dividend_growth_pullback_results_YYYY-MM-DD.json`
   - Structured data with all metrics for further analysis
   - Includes dividend growth rates, RSI values, financial health metrics

2. **Markdown report:** `dividend_growth_pullback_screening_YYYY-MM-DD.md`
   - Human-readable analysis with stock profiles
   - Scenario-based probability assessments
   - Entry timing recommendations

### Step 4: Analyze Qualified Stocks

For each qualified stock, the report includes:

**Dividend Growth Profile:**
- Current yield and annual dividend
- 3-year dividend CAGR and consistency
- Payout ratio and sustainability assessment

**Technical Timing:**
- Current RSI value (≤40 = oversold)
- RSI context (extreme oversold <30 vs. early pullback 30-40)
- Price action relative to recent trend

**Quality Metrics:**
- Revenue and EPS growth (confirms business momentum)
- Financial health (debt levels, liquidity ratios)
- Profitability (ROE, profit margins)

**Investment Recommendation:**
- Entry timing assessment (immediate vs. wait for confirmation)
- Risk factors specific to the stock
- Upside scenarios based on dividend growth compounding

## Output

The script saves two files to the repository `reports/` directory (or `--output-dir` if specified):

| File | Description |
|---|---|
| `dividend_growth_pullback_results_YYYY-MM-DD.json` | Structured data with all metrics (yield, dividend CAGR, RSI, composite score, etc.) |
| `dividend_growth_pullback_screening_YYYY-MM-DD.md` | Human-readable report with stock profiles, entry timing, and investment recommendations |

**Report structure (Markdown):**
- Executive summary (number of candidates, market conditions)
- Ranked stock profiles with dividend growth profile, technical timing, and quality metrics
- Entry recommendations based on RSI zone (extreme oversold / strong oversold / early pullback)
- Disclaimers

## Screening Criteria Details

### Phase 1: Fundamental Screening (TradingView scanner)

**Initial Filter:**
- Dividend Yield ≥ 1.5% (verified against the last completed fiscal-year DPS / current price)
- Market Cap ≥ $2 billion (liquidity and stability)
- Universe: S&P 500 constituents (or FINVIZ pre-screened symbols with `--use-finviz`)

**Dividend Growth Analysis:**
- 3-Year Dividend CAGR ≥ 12% (doubles dividend in 6 years), computed from the scanner's annual DPS history (~20 fiscal years)
- Dividend Consistency: No cuts in past 4 years
- Payout Ratio < 100% (sustainability check; REITs use an OCF≈FFO proxy)

**Financial Health:**
- Positive revenue growth over 3 years
- Positive EPS growth over 3 years
- Debt-to-Equity < 2.0 (manageable leverage)
- Current Ratio > 1.0 (liquidity)

### Phase 2: Technical Screening (RSI Calculation)

**RSI Calculation:**
- 14-period RSI using daily closing prices
- Formula: RSI = 100 - (100 / (1 + RS))
  - RS = Average Gain / Average Loss over 14 periods
- Data source: TradingView daily bars (most recent ~30 sessions)

**RSI Filter:**
- RSI ≤ 40 (oversold/pullback condition)
- RSI interpretation:
  - < 30: Extreme oversold (potential reversal)
  - 30-40: Early pullback (uptrend correction)
  - > 40: Not oversold (excluded)

### Phase 3: Ranking and Output

**Composite Scoring (0-100):**
- Dividend Growth (40%): Reward higher CAGR and consistency
- Financial Quality (30%): ROE, profit margins, debt levels
- Technical Setup (20%): Lower RSI = better entry opportunity
- Valuation (10%): P/E and P/B for context (not exclusionary)

Stocks ranked by composite score. Top scorers combine exceptional dividend growth with attractive technical entry points.

## Understanding the Results

### Interpreting RSI Levels

**RSI 25-30 (Extreme Oversold):**
- Often indicates panic selling or negative news
- Higher risk but potentially highest reward
- Recommended: Wait for RSI to turn up (sign of stabilization)
- Entry: Scale in with 50% position, add on RSI >30

**RSI 30-35 (Strong Oversold):**
- Normal correction in strong uptrend
- Lower risk than extreme oversold
- Recommended: Can initiate position immediately
- Entry: Full position acceptable, set stop loss 5-8% below

**RSI 35-40 (Early Pullback):**
- Mild weakness in uptrend
- Lowest risk of further decline
- Recommended: Conservative entry for high conviction stocks
- Entry: Full position, tight stop loss 3-5% below

### Dividend Growth Compounding Examples

**12% Dividend CAGR (Minimum Threshold):**
- Starting Yield: 1.5%
- Year 6: 2.96% yield on cost (doubled)
- Year 12: 5.85% yield on cost (4x)
- Example: Visa (V), Mastercard (MA) historical profile

**15% Dividend CAGR (Excellent):**
- Starting Yield: 1.8%
- Year 6: 4.08% yield on cost (2.3x)
- Year 12: 9.22% yield on cost (5.1x)
- Example: Microsoft (MSFT) 2010-2020 period

**20% Dividend CAGR (Exceptional):**
- Starting Yield: 2.0%
- Year 6: 6.00% yield on cost (3x)
- Year 12: 18.0% yield on cost (9x)
- Example: Apple (AAPL) 2012-2020 period

**Key Insight:** Lower starting yield + high growth > high starting yield + low growth over 10+ years.

## Troubleshooting

### No Results Found

**Possible Causes:**
1. **Market conditions:** Strong bull market with few oversold stocks
2. **Criteria too strict:** 12% dividend growth is rare (5-10 stocks typically qualify)
3. **RSI threshold too low:** Consider raising to RSI ≤45 for more candidates

**Solutions:**
- Relax RSI threshold: `--rsi-max 45` (early pullback phase)
- Lower dividend growth: `--min-div-growth 10.0` (still excellent growth)
- Lower minimum yield: `--min-yield 1.0` (capture more growth stocks)

### TradingView Data Layer Unavailable

**Symptoms:** `tv CLI not found`, repeated "No profile data" / "No dividend history", or a hung run.

**Possible Causes:**
1. TradingView Desktop is not running, or remote debugging (CDP on :9222) is off
2. The `tv` CLI is not resolvable (no global `tv`, no `TV_CLI`, vendored copy not installed)
3. The metrics snapshot cache is stale and the live chart path is unreachable

**Solutions:**
- Start TradingView Desktop with remote debugging enabled (see `vendor/tradingview-mcp/scripts/launch_tv_debug_mac.sh`)
- Run `npm install` in `vendor/tradingview-mcp`, or set `TV_CLI` / `TV_MCP_REPO`
- Refresh the snapshot cache: `node vendor/tradingview-mcp/scripts/collect_russell.js --source snp500 --update`

**Performance note:** with a fresh metrics cache most symbols are served without touching the chart; a cold cache falls back to live chart reads (~2s per symbol that passes the yield pre-filter).

### RSI Calculation Errors

**Issue:** "Insufficient price data for RSI calculation"

**Cause:** Stock has less than 30 days of trading history (IPO or inactive)

**Solution:** Script automatically skips stocks with insufficient data. No action needed.

## Combining with Other Skills

**Pre-Screening Context:**
1. **Market News Analyst** → Identify sector rotations or market pullbacks
2. **Breadth Chart Analyst** → Confirm broader market oversold conditions
3. **Economic Calendar Fetcher** → Check for upcoming rate decisions or macro events

**Post-Screening Analysis:**
1. **Technical Analyst** → Analyze individual stock charts for qualified candidates
2. **US Stock Analysis** → Deep dive on specific stocks before entry
3. **Backtest Expert** → Validate RSI + dividend growth strategy historically

**Example Workflow:**
```
1. Market News Analyst: "Market pulled back 5% this week on Fed hawkish comments"
2. Breadth Chart Analyst: Confirms market oversold (S&P breadth weak)
3. Dividend Growth Pullback Screener: Finds 8 quality dividend growers with RSI <35
4. Technical Analyst: Analyze top 3 candidates for support levels and entry timing
5. Execute: Enter scaled positions with 6-12 month time horizon
```

## Resources

### scripts/

**screen_dividend_growth_rsi.py** - Main screening script
- Reads fundamentals and annual DPS history from the TradingView scanner via the shared `tv_client` data layer (no API key)
- Calculates 14-period RSI from TradingView daily bars
- Applies multi-phase filtering and ranking
- Outputs JSON and markdown reports

### references/

**rsi_oversold_strategy.md** - RSI indicator explanation
- How RSI identifies oversold conditions
- Difference between extreme oversold (<30) vs. early pullback (30-40)
- Combining RSI with fundamental analysis
- False positive management and risk mitigation

**dividend_growth_compounding.md** - Dividend growth mathematics
- Power of 12%+ dividend CAGR over time
- Yield vs. growth trade-offs
- Historical examples (MSFT, V, MA, AAPL)
- Quality characteristics of dividend growth stocks

**fmp_api_guide.md** - Legacy FMP API documentation (historical)
- Kept for reference; the screener now sources all data from TradingView
- Relevant only if reverting to an FMP-based data layer

---

**Disclaimer:** This screening tool is for informational purposes only. Past dividend growth does not guarantee future performance. Conduct thorough due diligence before making investment decisions. RSI oversold conditions do not guarantee price reversals - stocks can remain oversold for extended periods.
