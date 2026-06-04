---
name: value-dividend-screener
description: Screen US stocks for high-quality dividend opportunities combining value characteristics (P/E ratio under 20, P/B ratio under 2), attractive yields (3% or higher), and consistent growth (dividend/revenue/EPS trending up over 3 years). Uses the local TradingView data layer (no API key); optional FINVIZ Elite pre-screen widens the universe beyond the S&P 500. Use when user requests dividend stock screening, income portfolio ideas, or quality value stocks with strong fundamentals.
---

# Value Dividend Screener

## Overview

This skill identifies high-quality dividend stocks that combine value characteristics, attractive income generation, and consistent growth.

Two screening modes:

1. **TradingView data layer (default)**: Walks the S&P 500 universe reading fundamentals, annual DPS history, and daily bars from a live TradingView Desktop chart via the shared `scripts/lib/tv_client.py` — **no API key, no request quota**.
2. **FINVIZ Elite pre-screen (optional)**: A single FINVIZ Elite API call pre-filters the whole US market (mid-cap+) with value/dividend criteria, then TradingView supplies the detailed analysis. Widens the universe beyond the S&P 500.

Screen US equities based on quantitative criteria including valuation ratios, dividend metrics, financial health, and profitability. Generate comprehensive reports ranking stocks by composite quality scores; oversold names (RSI ≤ 40) are preferred in the final ranking.

## When to Use

Invoke this skill when the user requests:
- "Find high-quality dividend stocks"
- "Screen for value dividend opportunities"
- "Show me stocks with strong dividend growth"
- "Find income stocks trading at reasonable valuations"
- "Screen for sustainable high-yield stocks"
- Any request combining dividend yield, valuation metrics, and fundamental analysis

## Prerequisites

- **TradingView data layer** (required): a running TradingView Desktop chart (CDP on :9222) or a fresh `state/metrics` snapshot cache. All stock data (fundamentals, annual DPS history, daily bars) flows through the shared `scripts/lib/tv_client.py` — **no API key and no request quota**.
- **FINVIZ Elite API key** (optional): Set `FINVIZ_API_KEY` environment variable or pass `--finviz-api-key`. Widens the screening universe beyond the S&P 500 with a pre-filtered candidate list. Requires FINVIZ Elite subscription (~$40/month or ~$330/year). [Sign up](https://elite.finviz.com/).
- Python 3.8+. The `requests` library is needed only for the optional FINVIZ pre-screen.

> Legacy `FMP_API_KEY` / `--fmp-api-key` inputs are accepted but ignored — the FMP data path was replaced by TradingView.

## Workflow

### Step 1: Choose the Universe

#### S&P 500 via TradingView (default)

No setup needed beyond a running TradingView Desktop chart. The screener walks the committed S&P 500 constituents list and reads everything from the TradingView scanner + chart bars.

#### FINVIZ Pre-Screen (optional, wider universe)

```bash
export FINVIZ_API_KEY=your_finviz_key_here
```

FINVIZ filters applied in one API call: Market cap mid+, Dividend yield 3%+, Dividend growth (3Y) 5%+, EPS growth (3Y) positive, P/B < 2, P/E < 20, Sales growth (3Y) positive, USA.

### Step 2: Execute Screening Script

**Default S&P 500 screening (no API key):**
```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py
```

**Two-stage screening (FINVIZ + TradingView):**
```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py --use-finviz
```

**Custom top N / output location / candidate cap:**
```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py \
  --top 50 --output-dir reports/ --max-candidates 200
```

**Script behavior:**
1. Universe: S&P 500 constituents (default) or FINVIZ pre-screened symbols (`--use-finviz`)
2. Per-symbol detailed analysis via TradingView:
   - Market cap ≥ $2B; valuation filters P/E ≤ 20, P/B ≤ 2 (scanner snapshot)
   - Dividend yield ≥ 3.0% (verified as last completed fiscal-year DPS / current price)
   - Dividend growth rate calculation (3-year CAGR ≥ 4%) from the scanner's annual DPS history
   - Dividend stability check (volatility, consecutive years of growth)
   - Revenue and EPS trend analysis (annual fiscal-year series, positive over 3 years)
   - Dividend sustainability assessment (scanner payout ratio; DPS×shares / FCF coverage; REITs use OCF≈FFO proxy)
   - Financial health (snapshot debt-to-equity < 2.0, current ratio > 1.0)
   - Quality scoring (ROE, net profit margin — percent scale)
   - 14-period RSI from daily bars (oversold RSI ≤ 40 preferred in final ranking)
3. Composite scoring and ranking
4. Output top N stocks to JSON file in `reports/`

**Expected runtime:** with a fresh metrics cache most symbols are served without touching the chart (seconds); a cold cache falls back to live chart reads (~2s per symbol that passes the cheap pre-filters).

### Step 3: Parse and Analyze Results

Read the generated JSON file:

```python
import json

with open('reports/value_dividend_results_YYYY-MM-DD.json', 'r') as f:
    data = json.load(f)

metadata = data['metadata']
stocks = data['stocks']
```

**Key data points per stock:**
- Basic info: `symbol`, `company_name`, `sector`, `market_cap`, `price`
- Valuation: `dividend_yield`, `pe_ratio`, `pb_ratio`
- Technical: `rsi`
- Growth metrics: `dividend_cagr_3y`, `revenue_cagr_3y`, `eps_cagr_3y`
- Stability: `dividend_stable`, `dividend_growing`, `dividend_volatility_pct`, `dividend_years_of_growth`, `stability_score`
- Sustainability: `payout_ratio`, `fcf_payout_ratio`, `dividend_sustainable`
- Financial health: `debt_to_equity`, `current_ratio`, `financially_healthy`
- Quality: `roe`, `profit_margin`, `quality_score` (ROE / margin are PERCENT values)
- Overall ranking: `composite_score`

### Step 4: Generate Markdown Report

Create structured markdown report for user with following sections:

#### Report Structure

```markdown
# Value Dividend Stock Screening Report

**Generated:** [Timestamp]
**Data Source:** TradingView data layer (no API key)
**Screening Criteria:**
- Dividend Yield: >= 3.0%
- P/E Ratio: <= 20
- P/B Ratio: <= 2
- Dividend Growth (3Y CAGR): >= 4%
- Revenue Trend: Positive over 3 years
- EPS Trend: Positive over 3 years

**Total Results:** [N] stocks

---

## Top 20 Stocks Ranked by Composite Score

| Rank | Symbol | Company | Yield | P/E | Div Growth | RSI | Score |
|------|--------|---------|-------|-----|------------|-----|-------|
| 1 | [TICKER] | [Name] | [%] | [X.X] | [%] | [XX] | [XX.X] |
| ... |

---

## Detailed Analysis

### 1. [SYMBOL] - [Company Name] (Score: XX.X)

**Sector:** [Sector Name]
**Market Cap:** $[X.XX]B
**Current Price:** $[XX.XX]

**Valuation Metrics:**
- Dividend Yield: [X.X]%
- P/E Ratio: [XX.X]
- P/B Ratio: [X.X]
- RSI (14): [XX] [Oversold / Neutral]

**Growth Profile (3-Year):**
- Dividend CAGR: [X.X]% [✓ Consistent / ⚠ One cut]
- Revenue CAGR: [X.X]%
- EPS CAGR: [X.X]%

**Dividend Sustainability:**
- Payout Ratio: [XX]%
- FCF Payout Ratio: [XX]%
- Status: [✓ Sustainable / ⚠ Monitor / ❌ Risk]

**Financial Health:**
- Debt-to-Equity: [X.XX]
- Current Ratio: [X.XX]
- Status: [✓ Healthy / ⚠ Caution]

**Quality Metrics:**
- ROE: [XX]%
- Net Profit Margin: [XX]%
- Quality Score: [XX]/100

**Investment Considerations:**
- [Key strength 1]
- [Key strength 2]
- [Risk factor or consideration]

---

[Repeat for other top stocks]

---

## Portfolio Construction Guidance

**Diversification Recommendations:**
- Sector breakdown of top 20 results
- Suggested allocation strategy
- Concentration risk warnings

**Monitoring Recommendations:**
- Key metrics to track quarterly
- Warning signs for each position
- Rebalancing triggers

**Risk Considerations:**
- Market cap concentration
- Sector biases in results
- Economic sensitivity warnings
```

### Step 5: Provide Context and Methodology

Reference screening methodology when explaining results:

**Key concepts to explain:**
- Why these specific thresholds (3% yield, P/E 20, P/B 2)
- Importance of dividend growth vs. static high yield
- How composite score balances value, growth, and quality
- Dividend sustainability vs. dividend trap distinction
- Financial health metrics significance
- Why oversold (RSI ≤ 40) names rank first

Load `references/screening_methodology.md` to provide detailed explanations of:
- Phase 1: Initial quantitative filters
- Phase 2: Growth quality filters
- Phase 3: Sustainability and quality analysis
- Composite scoring system
- Investment philosophy and limitations

### Step 6: Answer Follow-up Questions

Anticipate common user questions:

**"Why did [stock] not make the list?"**
- Check which criteria it failed (yield, valuation, growth, sustainability)
- Explain the specific filter that excluded it

**"Can I screen for specific sectors?"**
- Filter the constituents list before analysis, or post-filter the JSON output by `sector`

**"What if I want higher/lower yield threshold?"**
- `screen_value_dividend_stocks()` accepts `min_yield`, `pe_max`, `pb_max`, `min_div_growth` parameters
- Trade-offs between yield and growth
- Recommend re-screening with new parameters

**"How often should I re-run this screen?"**
- Quarterly recommended (aligns with earnings cycles)
- Semi-annually sufficient for long-term holders
- Market conditions may warrant more frequent checks

**"How many stocks should I buy?"**
- Diversification guidance: minimum 10-15 for dividend portfolio
- Sector balance considerations
- Position sizing based on risk tolerance

## Resources

### scripts/screen_dividend_stocks.py

Comprehensive screening script that:
- Reads fundamentals, annual DPS history, and daily bars from the TradingView scanner via the shared `tv_client` data layer (no API key)
- Implements multi-phase filtering logic (valuation → yield → dividend growth → trends → health)
- Calculates growth rates (CAGR) over 3-year periods
- Evaluates dividend sustainability via scanner payout ratio and DPS×shares / FCF coverage (REITs: OCF≈FFO proxy)
- Assesses financial health (snapshot debt-to-equity, current ratio)
- Computes quality scores (ROE, profit margins — percent scale)
- Calculates 14-period RSI and prefers oversold names in the final ranking
- Ranks stocks by composite scoring system
- Outputs structured JSON results to `reports/`

**Dependencies:** none for the default path; `requests` only for the optional FINVIZ pre-screen

**Error handling:** graceful per-symbol skips for missing data; too-short price histories skipped cleanly

### references/screening_methodology.md

Comprehensive documentation of screening approach:

**Phase 1: Initial Quantitative Filters**
- Dividend yield >= 3.0% rationale and calculation
- P/E ratio <= 20 threshold justification
- P/B ratio <= 2 valuation logic

**Phase 2: Growth Quality Filters**
- Dividend growth (3-year CAGR >= 4%)
- Revenue positive trend analysis
- EPS positive trend analysis

**Phase 3: Quality & Sustainability Analysis**
- Dividend sustainability metrics (payout ratios, FCF coverage)
- Financial health indicators (D/E, current ratio)
- Quality scoring methodology (ROE, profit margins)

**Composite Scoring System (0-100 points)**
- Score component breakdown and weighting
- Interpretation guidelines

**Investment Philosophy**
- Why this approach works
- What this strategy avoids (dividend traps, value traps)
- Ideal candidate profile

**Usage Notes & Limitations**
- Best practices for portfolio construction
- When to sell criteria
- Historical context for threshold selection

### references/fmp_api_guide.md

Legacy FMP API documentation (historical):
- Kept for reference; the screener now sources all data from TradingView
- Relevant only if reverting to an FMP-based data layer

## Advanced Usage

### Customizing Screening Criteria

`screen_value_dividend_stocks()` exposes the thresholds as parameters:

```python
results = screen_value_dividend_stocks(
    top_n=20,
    min_yield=3.0,        # Minimum dividend yield %
    pe_max=20.0,          # Maximum P/E
    pb_max=2.0,           # Maximum P/B
    min_div_growth=4.0,   # Minimum 3Y dividend CAGR %
    max_candidates=None,  # Cap on analyzed symbols
)
```

### Sector-Specific Screening

Filter results after screening:

```python
target_sectors = ['Consumer Defensive', 'Utilities', 'Healthcare']
filtered = [s for s in stocks if s.get('sector') in target_sectors]
```

### Excluding REITs and Financials

REITs and financial stocks have different dividend characteristics (higher payouts, different metrics):

```python
exclude_sectors = ['Real Estate', 'Financial Services']
filtered = [s for s in stocks if s.get('sector') not in exclude_sectors]
```

### Exporting to CSV

Convert JSON results to CSV for Excel analysis:

```python
import json
import csv

with open('reports/value_dividend_results_YYYY-MM-DD.json', 'r') as f:
    data = json.load(f)

stocks = data['stocks']

with open('screening_results.csv', 'w', newline='') as csvfile:
    if stocks:
        fieldnames = stocks[0].keys()
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(stocks)
```

## Troubleshooting

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

### "ERROR: FINVIZ API key required when using --use-finviz"

**Solution:** Set environment variable or provide via command-line
```bash
export FINVIZ_API_KEY=your_key_here
# OR
python3 scripts/screen_dividend_stocks.py --use-finviz --finviz-api-key your_key_here
```

**Note:** FINVIZ Elite subscription required (~$40/month or ~$330/year)

### "ERROR: FINVIZ API authentication failed"

**Possible causes:**
1. Invalid FINVIZ API key
2. FINVIZ Elite subscription expired
3. API key format incorrect

**Solution:**
- Verify FINVIZ Elite subscription is active
- Check API key for typos (should be alphanumeric string)
- Log into FINVIZ Elite account and verify API key in settings
- Try accessing FINVIZ Elite screener manually to confirm subscription

### "ERROR: FINVIZ pre-screening failed or returned no results"

**Possible causes:**
1. FINVIZ API connection issue
2. Screening criteria too restrictive (no stocks match)
3. Market conditions (bear market may yield fewer results)

**Solution:**
- Check internet connection
- Verify FINVIZ Elite website is accessible
- Use the default TradingView S&P 500 mode as fallback:
  ```bash
  python3 scripts/screen_dividend_stocks.py
  ```

### "No stocks found matching all criteria"

**Solution:** Criteria may be too restrictive
- Relax P/E threshold (increase from 20)
- Lower dividend yield requirement (decrease from 3.0%)
- Reduce dividend growth requirement (decrease from 4%)
- Check market conditions (bull markets may have fewer cheap qualifiers)

## Version History

- **v2.0** (June 2026): Migrated data layer from FMP API to TradingView (`tv_client`); no API key required, S&P 500 default universe, RSI-aware ranking; FINVIZ pre-screen kept as optional universe widener
- **v1.1** (November 2025): Added FINVIZ Elite integration for two-stage screening
- **v1.0** (November 2025): Initial release with comprehensive multi-phase screening
