---
layout: default
title: "Tradingview Screener"
grand_parent: English
parent: Skill Guides
nav_order: 60
lang_peer: /ja/skills/tradingview-screener/
permalink: /en/skills/tradingview-screener/
generated: true
---

# Tradingview Screener
{: .no_toc }

Screen stocks via the TradingView Stock Screener (All Stocks tab) scanner API from natural language requests. No API key required. Use when user wants to screen stocks with TradingView, filter by fundamentals/technicals/dividends across 238 TV screener filters, or asks for a TV screener scan (e.g., "screen TV for oversold large caps", "найди дивидендные акции через TradingView", "高配当株をTradingViewでスクリーニング").
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/tradingview-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/tradingview-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Translate natural-language stock screening requests into TradingView scanner
API filter expressions, run the scan against `scanner.tradingview.com` (the
same endpoint the TradingView "All stocks" screener tab uses), and produce a
ranked results table plus markdown/JSON reports. **No API key, no
authentication, no TradingView Desktop required.**

**Key features:**

- Natural language → filter token mapping (English / Russian / Japanese)
- 238 UI filters across 8 categories (Security info, Market data, Technicals,
  Financials, Valuation, Growth, Margins & Ratios, Dividends), all mapped to
  scanner fields in the reference
- Exact "All Stocks" tab universe semantics (common + preferred stocks, DRs,
  non-ETF funds, no pre-IPO, primary listings only)
- Field-to-field comparisons (e.g. price above EMA200: `close>EMA200`)
- Index membership (S&P 500, NASDAQ 100, Russell 2000, …), sector/industry/
  country/exchange multiselects, analyst & technical rating envelopes
- Column presets mirroring TV screener tabs (overview, performance, valuation,
  dividends, profitability, income, balance, cashflow, technicals)
- Markdown + JSON reports saved to `reports/`

---

---

## 2. When to Use

**Explicit triggers:**

- "Screen TradingView for stocks with P/E under 15 and dividend yield over 4%"
- "Найди через TV перепроданные акции выше 200-дневной средней"
- "TradingViewで時価総額100億ドル以上の高配当株を探して"
- "Run a TV screener scan for S&P 500 financials yielding over 4%"
- "Find small caps with RSI below 30 on TradingView"

**Implicit triggers:**

- User describes screening criteria and prefers TradingView data, or no paid
  API key (FMP/FINVIZ) is available
- User asks which stocks currently match technical conditions (RSI, MACD,
  moving averages, candlestick patterns) across the whole market

**When NOT to use:**

- FinViz-specific screening or opening the FinViz UI (use finviz-screener)
- Deep single-stock analysis (use us-stock-analysis / ticker-analysis)
- VCP / CANSLIM / PEAD specialized methodology scans (use the dedicated skills)
- Chart image analysis (use technical-analyst)

---

---

## 3. Prerequisites

- Public scanner.tradingview.com endpoint (All Stocks tab); no API key or auth required
- Python 3.9+ recommended

---

## 4. Quick Start

```bash
Skip confirmation only when the request is fully unambiguous.

### Step 4: Execute Script
```

---

## 5. Workflow

### Step 1: Load Filter Reference

Read `references/tradingview_screener_filters.md` for the complete filter
catalog (238 UI filters → scanner fields), operations, enum values, index
IDs, and the All Stocks universe definition.

### Step 2: Interpret User Request

Map the request to filter tokens. Common concept mapping:

| User concept (EN / RU / JP) | Filter tokens |
|---|---|
| Large cap / крупные компании / 大型株 | `mkt_cap>10B` |
| Mid cap / средние / 中型株 | `mkt_cap=2B..10B` |
| Small cap / малые / 小型株 | `mkt_cap=300M..2B` |
| High dividend / высокие дивиденды / 高配当 | `div_yield>3` (cap traps: `div_yield=3..8`) |
| Cheap & value / недооценённые / 割安 | `pe<20,pb<2` |
| Growth / растущие / 成長株 | `revenue_growth>15,eps_growth>25` |
| Oversold / перепроданные / 売られすぎ | `rsi<30` |
| Overbought / перекупленные / 買われすぎ | `rsi>70` |
| Uptrend / восходящий тренд | `close>SMA50,close>SMA200` |
| Above 200-day MA / выше 200-дневной | `close>SMA200` (or `close>EMA200`) |
| New 52-week high / новый годовой максимум / 52週高値 | `close>=price_52_week_high` (closes at the high; no arithmetic in API — for "near high" use `close>SMA50,perf_ytd>0` and sort `-Perf.Y`) |
| Profitable / прибыльные / 黒字 | `net_income>0` or `pe>0` |
| High ROE / высокий ROE | `roe>15` |
| Low debt / низкий долг | `debt_to_equity<0.5` |
| Quality balance sheet | `current_ratio>1.5,altman_z>3` |
| Dividend growers / растущие дивиденды / 増配 | `div_growth_years>=5,dps_growth>5` |
| Liquid / ликвидные | `avg_volume>500K` or `volume>1M` |
| High relative volume / всплеск объёма | `rel_volume>1.5` |
| Momentum / моментум | `perf_3m>10,close>SMA50,close>SMA200` |
| Low volatility / защитные | `beta<0.8` |
| Earnings soon / скоро отчёт | sort by `earnings_release_next_date` ascending |
| Hammer candle / молот | `Candle.Hammer=1` |
| Strong buy rating (technical) | `--technical-rating strong_buy` |
| Strong buy rating (analysts) | `--analyst-rating strong_buy,buy` |
| S&P 500 only | `--index sp500` |
| Tech sector / технологии / テック | `--sectors "Electronic Technology,Technology Services"` |
| Financials / финансы | `--sectors "Finance"` |
| Healthcare / здравоохранение | `--sectors "Health Technology,Health Services"` |
| Energy / энергетика | `--sectors "Energy Minerals,Industrial Services"` |

⚠️ US market (`america`) uses the TV sector taxonomy: Electronic Technology,
Technology Services, Finance, Health Technology, Energy Minerals, Consumer
Non-Durables, Retail Trade, Utilities, Transportation, Producer
Manufacturing, Process Industries, Industrial Services, etc. — see the
reference for the full list. Do NOT use "Technology" / "Healthcare" (other
markets' taxonomy).

### Step 3: Present Filter Selection

Before executing, show the planned filters in a table for confirmation:

```markdown
| Type | Token | Meaning |
|---|---|---|
| Filter | mkt_cap>10B | Market cap > $10B |
| Filter | pe<20 | P/E (TTM) < 20 |
| Filter | div_yield=3..8 | Dividend yield 3–8% |
| Index | sp500 | S&P 500 members only |

Columns: dividends · Sort: div_yield desc · Limit: 50
```

Skip confirmation only when the request is fully unambiguous.

### Step 4: Execute Script

```bash
python3 scripts/run_tv_screener.py \
  --filters "mkt_cap>10B,pe<20,div_yield=3..8" \
  --columns dividends \
  --sort=-div_yield \
  --limit 50 \
  --output-dir reports/
```

**Script arguments:**

- `--filters` — comma-separated tokens: `field<op>value` with ops `>`, `>=`,
  `<`, `<=`, `=`, `!=`; ranges `field=lo..hi`; multiselect `field=A|B`;
  value suffixes `K/M/B/T`; right side may be a field name (`close>EMA200`)
- `--filter-preset` — named filter recipe (`midterm-momentum`); `--filters`
  tokens are applied on top; the preset also becomes the default
  `--screen-name` (an explicit `--screen-name` wins)
- `--sectors` / `--industries` / `--countries` / `--exchanges` —
  comma-separated enum values (TV taxonomy)
- `--index` — `sp500`, `nasdaq100`, `dow30`, `russell2000`, `russell1000`,
  `russell3000`, `sp400`, `sp100`, `nasdaqcomposite`, or raw `SYML:...`
- `--analyst-rating` / `--technical-rating` — `strong_buy,buy,neutral,sell,
  strong_sell` (mapped to `recommendation_mark` / `Recommend.All` ranges)
- `--columns` — preset (`overview`, `performance`, `valuation`, `dividends`,
  `profitability`, `income`, `balance`, `cashflow`, `technicals`) or
  comma-separated field list; `--add-columns` appends extras
- `--sort` — field, `-field` or `field:desc` for descending (use the
  `--sort=-field` form: a bare `-field` is eaten by argparse)
- `--limit` — max rows (default 50, max 500)
- `--market` — `america` (default), `global`, or a country market
- `--universe` — `all` (All Stocks tab: common+preferred+DR+funds) or
  `common` (common stocks only); `--include-secondary` keeps secondary
  listings
- `--dry-run` — print the scanner JSON payload without network
- `--output-dir` — default `reports/`; `--screen-name` names the files

Field names: use aliases (`pe`, `mkt_cap`, `div_yield`, `rsi`, `roe`, …) or
any raw scanner field from the reference. Aliases are case-insensitive.

### Step 5: Report Results

The script prints the markdown table and writes
`reports/tradingview_screener_<name>_<timestamp>.md` / `.json`. After running:

1. Summarize the top results and total match count
2. Note any data caveats (nulls, 0 matches → check field names)
3. Suggest refinements (tighten/relax filters, switch column preset)
4. Offer follow-ups: deep-dive a ticker (us-stock-analysis), register a
   thesis (trader-memory-core), or position sizing (position-sizer)

---

---

## 6. Resources

**References:**

- `skills/tradingview-screener/references/tradingview_screener_filters.md`

**Scripts:**

- `skills/tradingview-screener/scripts/run_tv_screener.py`
