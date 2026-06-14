# IB Portfolio Manager

Comprehensive portfolio analysis and management skill that integrates with the
**Interactive Brokers MCP Server** (`interactive-brokers-mcp`, with a bundled IB
Gateway) to fetch live holdings data and generate detailed portfolio reports with
rebalancing recommendations.

This is the Interactive Brokers counterpart of the `portfolio-manager` skill
(which targets Alpaca). Both share the same analysis frameworks; only the
data-fetch layer differs. Use this skill when your brokerage account is at
Interactive Brokers.

## Overview

IB Portfolio Manager analyzes your IBKR portfolio across multiple dimensions:

- **Asset Allocation** — stocks, bonds, cash, multi-currency distribution vs target
- **Diversification** — sector breakdown, position concentration, correlation, HHI
- **Risk Assessment** — beta, volatility, drawdown, leverage/margin, options/futures exposure
- **Performance Review** — winners/losers, absolute and (via Flex) time-weighted returns
- **Position Analysis** — HOLD / ADD / TRIM / SELL recommendations per holding
- **Rebalancing Plan** — prioritized actions to optimize allocation

## Features

✅ **Interactive Brokers integration** — live positions, balances, and orders via MCP
✅ **Multi-asset / multi-currency aware** — handles STK / OPT / FUT / CASH / BOND
✅ **Read-only by design** — analysis only; never places orders (`IB_READ_ONLY_MODE` recommended)
✅ **Multi-dimensional analysis** — asset class, sector, geography, market cap, style
✅ **Risk metrics** — beta, volatility, drawdown, concentration, HHI, leverage
✅ **Rebalancing recommendations** — prioritized TRIM / ADD / HOLD / SELL actions
✅ **Comprehensive reports** — dated markdown reports saved to `reports/`

## Prerequisites

### Required: Interactive Brokers account + MCP Server

1. **Interactive Brokers account** (paper or live) — https://www.interactivebrokers.com/
2. **Interactive Brokers MCP Server** configured in Claude — the unofficial
   [`interactive-brokers-mcp`](https://github.com/code-rabi/interactive-brokers-mcp)
   package (Node.js 18+, bundled IB Gateway). Setup guide:
   `references/ib-mcp-setup.md`.
3. **Recommended environment:**
   ```bash
   export IB_PAPER_TRADING="true"     # or "false" for live
   export IB_READ_ONLY_MODE="true"    # disables place_order — analysis only
   # Optional, for historical performance via Flex Queries:
   export IB_FLEX_TOKEN="your_flex_web_service_token"
   ```

### Optional: Manual data entry

If the MCP server is unavailable, provide a CSV:

```csv
symbol,quantity,cost_basis,current_price
AAPL,100,150.00,175.50
MSFT,50,280.00,310.25
```

## Usage

Ask Claude to analyze your portfolio:

```
"Analyze my portfolio"
"Review my IBKR positions"
"What's my asset allocation?"
"Should I rebalance?"
"What are my biggest risks?"
```

The skill will: fetch positions and balances via the IB MCP → enrich with market
data → analyze allocation, diversification, risk, and performance → evaluate
individual positions → produce prioritized rebalancing recommendations → write a
dated report to `reports/`.

## Testing the connection

```bash
python3 skills/ib-portfolio-manager/scripts/check_ib_connection.py
```

The script reports your configured mode, locates the IB Gateway runtime session,
and probes the Client Portal auth-status endpoint. See `references/ib-mcp-setup.md`
for troubleshooting.

## Reference Materials

- **`references/ib-mcp-setup.md`** — IB MCP Server setup, auth modes, Flex tokens, troubleshooting
- **`references/asset-allocation.md`** — Asset allocation theory and frameworks
- **`references/diversification-principles.md`** — Diversification concepts and metrics
- **`references/portfolio-risk-metrics.md`** — Risk measurement and interpretation
- **`references/position-evaluation.md`** — Position analysis framework
- **`references/rebalancing-strategies.md`** — Rebalancing methodologies
- **`references/target-allocations.md`** — Model portfolios by risk profile
- **`references/risk-profile-questionnaire.md`** — Risk tolerance assessment

## How this differs from `portfolio-manager` (Alpaca)

| Aspect | `portfolio-manager` (Alpaca) | `ib-portfolio-manager` (this skill) |
|--------|------------------------------|-------------------------------------|
| Broker | Alpaca | Interactive Brokers |
| MCP tools | `mcp__alpaca__*` | `mcp__interactive-brokers__*` |
| Auth | API key + secret | IB Gateway (browser OAuth or headless creds) |
| Historical perf | `get_portfolio_history` | Flex Queries (`IB_FLEX_TOKEN`) |
| Asset classes | equities (+crypto) | equities, options, futures, forex, bonds |
| Analysis frameworks | shared | shared |

## Limitations and Disclaimers

- **Not financial advice** — informational analysis only.
- **Unofficial software** — `interactive-brokers-mcp` is alpha-stage and not
  affiliated with Interactive Brokers. Test with paper trading first, run
  locally only, and never commit IBKR credentials.
- **Analysis only** — this skill produces recommendations; it does not place orders.
- **Data accuracy** depends on the IBKR Client Portal API and third-party market data.
- **Tax estimates** are approximate; consult a tax professional.

## Related Skills

- **Portfolio Manager** — the Alpaca-broker counterpart
- **US Stock Analysis** — deep dive into individual positions
- **Value Dividend Screener** — find replacement stocks for rebalancing
- **Position Sizer** — risk-based sizing for new entries
- **Market News Analyst** — recent market-moving events
