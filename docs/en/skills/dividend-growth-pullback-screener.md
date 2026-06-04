---
layout: default
title: "Dividend Growth Pullback Screener"
grand_parent: English
parent: Skill Guides
nav_order: 13
lang_peer: /ja/skills/dividend-growth-pullback-screener/
permalink: /en/skills/dividend-growth-pullback-screener/
---

# Dividend Growth Pullback Screener
{: .no_toc }

Use this skill to find high-quality dividend growth stocks (12%+ annual dividend growth, 1.5%+ yield) that are experiencing temporary pullbacks, identified by RSI oversold conditions (RSI ≤40). This skill combines fundamental dividend analysis with technical timing indicators to identify buying opportunities in strong dividend growers during short-term weakness.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API Key</span> <span class="badge badge-optional">FINVIZ Optional</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/dividend-growth-pullback-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/dividend-growth-pullback-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

This skill screens for dividend growth stocks that exhibit strong fundamental characteristics but are experiencing temporary technical weakness. It targets stocks with exceptional dividend growth rates (12%+ CAGR) that have pulled back to RSI oversold levels (≤40), creating potential entry opportunities for long-term dividend growth investors.

**Investment Thesis:** High-quality dividend growth stocks (often yielding 1-2.5%) compound wealth through dividend increases rather than high current yield. Buying these stocks during temporary pullbacks (RSI ≤40) can enhance total returns by combining strong fundamental growth with favorable technical entry timing.

---

## 2. When to Use

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

---

## 3. Prerequisites

- **TradingView data layer** required: a running TradingView Desktop chart (CDP on :9222) or a fresh `state/metrics` snapshot cache — **no API key, no request quota**
- **FINVIZ Elite** optional (widens the universe beyond the S&P 500)
- TradingView for analysis; FINVIZ for RSI pre-screening
- Python 3.9+ recommended; `requests` needed only for the FINVIZ pre-screen
- Legacy `FMP_API_KEY` / `--fmp-api-key` inputs are accepted but ignored

---

## 4. Quick Start

```bash
# S&P 500 universe via TradingView (default, no API key)
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py

# Two-stage screening with FINVIZ pre-screen (wider universe)
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py --use-finviz

# Custom RSI threshold and dividend growth requirements
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py \
  --rsi-max 35 \
  --min-div-growth 15
```

---

## 5. Workflow

### Step 1: Choose the Universe

#### S&P 500 via TradingView (default)

No setup needed beyond a running TradingView Desktop chart. The screener walks the committed S&P 500 constituents list and reads everything (annual DPS history, fundamentals, daily bars) from the TradingView scanner.

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

# Limit candidates / change output location
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

---

## 6. Resources

**References:**

- `skills/dividend-growth-pullback-screener/references/dividend_growth_compounding.md`
- `skills/dividend-growth-pullback-screener/references/fmp_api_guide.md`
- `skills/dividend-growth-pullback-screener/references/rsi_oversold_strategy.md`

**Scripts:**

- `skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py`
