---
layout: default
title: "Value Dividend Screener"
grand_parent: English
parent: Skill Guides
nav_order: 44
lang_peer: /ja/skills/value-dividend-screener/
permalink: /en/skills/value-dividend-screener/
---

# Value Dividend Screener
{: .no_toc }

Screen US stocks for high-quality dividend opportunities combining value characteristics (P/E ratio under 20, P/B ratio under 2), attractive yields (3% or higher), and consistent growth (dividend/revenue/EPS trending up over 3 years). Uses the local TradingView data layer (no API key); optional FINVIZ Elite pre-screen widens the universe beyond the S&P 500. Use when user requests dividend stock screening, income portfolio ideas, or quality value stocks with strong fundamentals.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API Key</span> <span class="badge badge-optional">FINVIZ Optional</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/value-dividend-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/value-dividend-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

This skill identifies high-quality dividend stocks that combine value characteristics, attractive income generation, and consistent growth.

Two screening modes:

1. **TradingView data layer (default)**: Walks the S&P 500 universe reading fundamentals, annual DPS history, and daily bars from a live TradingView Desktop chart via the shared `tv_client` data layer — **no API key, no request quota**.
2. **FINVIZ Elite pre-screen (optional)**: A single FINVIZ Elite API call pre-filters the whole US market (mid-cap+) with value/dividend criteria, then TradingView supplies the detailed analysis. Widens the universe beyond the S&P 500.

Screen US equities based on quantitative criteria including valuation ratios, dividend metrics, financial health, and profitability. Generate comprehensive reports ranking stocks by composite quality scores; oversold names (RSI ≤ 40) are preferred in the final ranking.

---

## 2. When to Use

Invoke this skill when the user requests:
- "Find high-quality dividend stocks"
- "Screen for value dividend opportunities"
- "Show me stocks with strong dividend growth"
- "Find income stocks trading at reasonable valuations"
- "Screen for sustainable high-yield stocks"
- Any request combining dividend yield, valuation metrics, and fundamental analysis

---

## 3. Prerequisites

- **TradingView data layer** required: a running TradingView Desktop chart (CDP on :9222) or a fresh `state/metrics` snapshot cache — **no API key, no request quota**
- **FINVIZ Elite** optional (widens the universe beyond the S&P 500)
- Python 3.9+ recommended; `requests` needed only for the FINVIZ pre-screen
- Legacy `FMP_API_KEY` / `--fmp-api-key` inputs are accepted but ignored

---

## 4. Quick Start

```bash
# S&P 500 universe via TradingView (default, no API key)
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py

# Two-stage screening with FINVIZ pre-screen (wider universe)
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py --use-finviz

# Custom parameters
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py \
  --top 50 \
  --output-dir reports/
```

---

## 5. Workflow

### Step 1: Choose the Universe

#### S&P 500 via TradingView (default)

No setup needed beyond a running TradingView Desktop chart. The screener walks the committed S&P 500 constituents list and reads everything (fundamentals, annual DPS history, daily bars) from the TradingView scanner.

#### FINVIZ Pre-Screen (optional, wider universe)

```bash
export FINVIZ_API_KEY=your_finviz_key_here
```

**Why FINVIZ?**
- Pre-screens the whole US market (mid-cap+) with value/dividend filters in 1 API call
- TradingView then supplies the detailed analysis for the pre-screened candidates

**FINVIZ Elite API Key:**
- Requires FINVIZ Elite subscription (~$40/month or ~$330/year)
- Provides access to CSV export of pre-screened results

### Step 2: Execute Screening

**Default S&P 500 screening:**

```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py
```

**Two-Stage Screening (FINVIZ + TradingView):**

```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py --use-finviz
```

FINVIZ filters applied in one call: Market cap mid+, Dividend yield 3%+, Dividend growth (3Y) 5%+, EPS growth (3Y) positive, P/B under 2, P/E under 20, Sales growth (3Y) positive, USA.

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
   - Financial health (snapshot debt-to-equity, current ratio)
   - Quality scoring (ROE, net profit margin)
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
- Sustainability: `payout_ratio`, `fcf_payout_ratio`, `dividend_sustainable`
- Financial health: `debt_to_equity`, `current_ratio`, `financially_healthy`
- Quality: `roe`, `profit_margin`, `quality_score`
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
```

---

## 6. Resources

**References:**

- `skills/value-dividend-screener/references/screening_methodology.md`
- `skills/value-dividend-screener/references/fmp_api_guide.md` (legacy, historical)

**Scripts:**

- `skills/value-dividend-screener/scripts/screen_dividend_stocks.py`
