---
layout: default
title: "IB Portfolio Manager"
grand_parent: English
parent: Skill Guides
nav_order: 30
lang_peer: /ja/skills/ib-portfolio-manager/
permalink: /en/skills/ib-portfolio-manager/
generated: true
---

# IB Portfolio Manager
{: .no_toc }

Comprehensive portfolio analysis using the Interactive Brokers MCP Server (interactive-brokers-mcp / bundled IB Gateway) to fetch live holdings, account balances, and orders, then analyze asset allocation, risk metrics, individual positions, diversification, and generate rebalancing recommendations. Use when an Interactive Brokers user requests portfolio review, position analysis, risk assessment, performance evaluation, or rebalancing suggestions. IB-broker counterpart of the Alpaca-based portfolio-manager skill.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/ib-portfolio-manager.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/ib-portfolio-manager){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Analyze and manage Interactive Brokers (IBKR) portfolios by integrating with the Interactive Brokers MCP Server to fetch live holdings data, then performing comprehensive analysis covering asset allocation, diversification, risk metrics, individual position evaluation, and rebalancing recommendations. Generate detailed portfolio reports with actionable insights.

This skill uses the unofficial `interactive-brokers-mcp` server, which bundles IB Gateway and a Java runtime and talks to the IBKR Client Portal API. Analysis is therefore based on actual current positions rather than manually entered data. A vendored, security-hardened copy ships in this repo under `vendor/interactive-brokers-mcp/` (see its `VENDORING.md` / `SECURITY.md`); prefer it over `npx`.

This is the Interactive Brokers counterpart of the `portfolio-manager` skill (which targets Alpaca). The two skills share the same analysis frameworks; only the data-fetch layer differs. Use this skill when the brokerage account is at Interactive Brokers.

---

## 2. When to Use

Invoke this skill when an Interactive Brokers user requests:
- "Analyze my portfolio"
- "Review my current positions" / "Review my IBKR positions"
- "What's my asset allocation?"
- "Check my portfolio risk"
- "Should I rebalance my portfolio?"
- "Evaluate my holdings"
- "Portfolio performance review"
- "What stocks should I buy or sell?"
- Any request involving portfolio-level analysis or management for an Interactive Brokers account

If the user is on Alpaca, use the `portfolio-manager` skill instead.

---

## 3. Prerequisites

### Interactive Brokers MCP Server Setup

This skill requires the Interactive Brokers MCP Server to be configured and connected. The MCP server provides access to:
- Current portfolio positions (stocks, options, futures, forex, bonds)
- Account information and balances (net liquidation, cash, buying power)
- Live and historical orders
- Real-time market data for held instruments
- Historical statements, realized P&L, and dividends via Flex Queries (when `IB_FLEX_TOKEN` is set)

**MCP Server Tools Used (read path):**
- `get_account_info` — account information and balances (net liquidation value, cash, buying power)
- `get_positions` — current positions with quantity, average cost, market price/value, unrealized P&L
- `get_market_data` — real-time market data for specified instruments
- `get_live_orders` — open/working orders (so pending rebalances are not double-counted)
- `get_order_status` — execution status of a specific order
- `get_flex_query` / `list_flex_queries` — historical statements, realized P&L, dividend income (requires `IB_FLEX_TOKEN`)

**Order placement is out of scope for this skill's MCP-driven analysis.** `place_order` exists on the server but this skill is analysis-only: it produces rebalancing *recommendations*, never auto-executes. Run the MCP server with `IB_READ_ONLY_MODE=true` so `place_order` is disabled at the server level while analysis runs.

**Separate, opt-in order-placement helper:** `scripts/place_ib_bracket.py` is a write-side companion to the read-only snapshot. It talks to the IB Gateway Client Portal REST API directly (the same transport as `fetch_ib_snapshot.py`) to place a native bracket (entry buy/sell-stop + protective stop + take-profit via `cOID`/`parentId`). Because the direct REST path bypasses `IB_READ_ONLY_MODE`, it carries its own two-lock guard: it only POSTs when **both** `IB_ALLOW_ORDER_PLACEMENT=true` is set **and** the `--live` flag is passed; otherwise it prints a preview and posts nothing. It is driven by the watchlist-order automation (`scripts/watchlist_orders.py`), not by the normal "analyze my portfolio" flow.

If the Interactive Brokers MCP Server is not connected, inform the user and provide setup instructions from `references/ib-mcp-setup.md`.

---

## 4. Quick Start

```bash
Use mcp__interactive-brokers__get_account_info to fetch:
- Net liquidation value (total portfolio value)
- Total cash balance (and currency breakdown if multi-currency)
- Available funds / buying power
- Account type (paper vs live) and base currency
```

---

## 5. Workflow

### Step 1: Fetch Portfolio Data via Interactive Brokers MCP

Use the Interactive Brokers MCP Server tools to gather current portfolio information:

**1.1 Get Account Information:**
```
Use mcp__interactive-brokers__get_account_info to fetch:
- Net liquidation value (total portfolio value)
- Total cash balance (and currency breakdown if multi-currency)
- Available funds / buying power
- Account type (paper vs live) and base currency
```

**1.2 Get Current Positions:**
```
Use mcp__interactive-brokers__get_positions to fetch all holdings:
- Symbol / local symbol and contract id (conid)
- Asset class (STK / OPT / FUT / CASH / BOND)
- Quantity held
- Average cost (cost basis)
- Current market price
- Current market value
- Unrealized P&L ($ and %)
- Position size as % of net liquidation value
```

**1.3 Account for Working Orders:**
```
Use mcp__interactive-brokers__get_live_orders to list open/working orders so that
pending buys/sells are surfaced and rebalancing recommendations do not conflict
with orders already in the market.
```

**1.4 Get Historical Performance (Optional, requires Flex):**
```
If IB_FLEX_TOKEN is configured, use mcp__interactive-brokers__get_flex_query (or
list_flex_queries to discover saved queries) to retrieve:
- Historical net asset value / equity time series
- Realized P&L
- Dividend and interest income
Interactive Brokers has no single get_portfolio_history call (unlike Alpaca);
historical performance comes from Flex Query statements. If no Flex token is
configured, proceed with a current-snapshot analysis and note the limitation.
```

**Data Validation:**
- Verify all positions have valid symbols / contract ids
- Confirm market values sum to approximately the net liquidation value (minus cash)
- Separate non-equity instruments (options, futures, forex, bonds) and handle them appropriately — do not treat an option's notional as equity exposure without delta adjustment
- Reconcile multi-currency balances into the account base currency
- Check for any stale or zero-quantity positions

### Step 2: Enrich Position Data

For each equity position in the portfolio, gather additional market data and fundamentals:

**2.1 Current Market Data:**
- Real-time or delayed price quotes (via `get_market_data` or WebSearch)
- Daily volume and liquidity metrics
- 52-week range
- Market capitalization

**2.2 Fundamental Data:**
Use WebSearch or available market data APIs to fetch:
- Sector and industry classification
- Key valuation metrics (P/E, P/B, dividend yield)
- Recent earnings and financial health indicators
- Analyst ratings and price targets
- Recent news and material developments

**2.3 Technical Analysis:**
- Price trend (20-day, 50-day, 200-day moving averages)
- Relative strength
- Support and resistance levels
- Momentum indicators (RSI, MACD if available)

### Step 3: Portfolio-Level Analysis

Perform comprehensive portfolio analysis using frameworks from reference files:

#### 3.1 Asset Allocation Analysis

**Read references/asset-allocation.md** for allocation frameworks

Analyze current allocation across multiple dimensions:

**By Asset Class:**
- Equities vs Fixed Income vs Cash vs Alternatives (IBKR accounts frequently hold multiple asset classes)
- Compare to target allocation for the user's risk profile
- Assess if allocation matches investment goals

**By Sector:**
- Technology, Healthcare, Financials, Consumer, etc.
- Identify sector concentration risks
- Compare to benchmark sector weights (e.g., S&P 500)

**By Market Cap:**
- Large-cap vs Mid-cap vs Small-cap distribution
- Concentration in mega-caps
- Market cap diversification score

**By Geography / Currency:**
- US vs International vs Emerging Markets
- Currency exposure (IBKR supports multi-currency accounts) and FX risk
- Domestic concentration risk assessment

**Output Format:**
```markdown

---

## 6. Resources

**References:**

- `skills/ib-portfolio-manager/references/asset-allocation.md`
- `skills/ib-portfolio-manager/references/diversification-principles.md`
- `skills/ib-portfolio-manager/references/ib-mcp-setup.md`
- `skills/ib-portfolio-manager/references/portfolio-risk-metrics.md`
- `skills/ib-portfolio-manager/references/position-evaluation.md`
- `skills/ib-portfolio-manager/references/rebalancing-strategies.md`
- `skills/ib-portfolio-manager/references/risk-profile-questionnaire.md`
- `skills/ib-portfolio-manager/references/target-allocations.md`

**Scripts:**

- `skills/ib-portfolio-manager/scripts/check_ib_connection.py`
- `skills/ib-portfolio-manager/scripts/fetch_ib_snapshot.py`
- `skills/ib-portfolio-manager/scripts/place_ib_bracket.py`
