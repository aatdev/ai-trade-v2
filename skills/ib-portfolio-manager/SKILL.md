---
name: ib-portfolio-manager
description: Comprehensive portfolio analysis using the Interactive Brokers MCP Server (interactive-brokers-mcp / bundled IB Gateway) to fetch live holdings, account balances, and orders, then analyze asset allocation, risk metrics, individual positions, diversification, and generate rebalancing recommendations. Use when an Interactive Brokers user requests portfolio review, position analysis, risk assessment, performance evaluation, or rebalancing suggestions. IB-broker counterpart of the Alpaca-based portfolio-manager skill.
---

# IB Portfolio Manager

## Overview

Analyze and manage Interactive Brokers (IBKR) portfolios by integrating with the Interactive Brokers MCP Server to fetch live holdings data, then performing comprehensive analysis covering asset allocation, diversification, risk metrics, individual position evaluation, and rebalancing recommendations. Generate detailed portfolio reports with actionable insights.

This skill uses the unofficial `interactive-brokers-mcp` server, which bundles IB Gateway and a Java runtime and talks to the IBKR Client Portal API. Analysis is therefore based on actual current positions rather than manually entered data. A vendored, security-hardened copy ships in this repo under `vendor/interactive-brokers-mcp/` (see its `VENDORING.md` / `SECURITY.md`); prefer it over `npx`.

This is the Interactive Brokers counterpart of the `portfolio-manager` skill (which targets Alpaca). The two skills share the same analysis frameworks; only the data-fetch layer differs. Use this skill when the brokerage account is at Interactive Brokers.

## When to Use

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

## Prerequisites

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

**Order placement is out of scope for this skill.** `place_order` exists on the server but this skill is analysis-only: it produces rebalancing *recommendations*, never auto-executes. Run the MCP server with `IB_READ_ONLY_MODE=true` so `place_order` is disabled at the server level while analysis runs.

If the Interactive Brokers MCP Server is not connected, inform the user and provide setup instructions from `references/ib-mcp-setup.md`.

## Workflow

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
## Asset Allocation

### Current Allocation vs Target
| Asset Class | Current | Target | Variance |
|-------------|---------|--------|----------|
| US Equities | XX.X% | YY.Y% | +/- Z.Z% |
| ... |

### Sector Breakdown
[Table with sector percentages]

### Top 10 Holdings
| Rank | Symbol | % of Portfolio | Asset Class | Sector |
|------|--------|----------------|-------------|--------|
| 1 | AAPL | X.X% | STK | Technology |
| ... |
```

#### 3.2 Diversification Analysis

**Read references/diversification-principles.md** for diversification theory

Evaluate portfolio diversification quality:

**Position Concentration:**
- Identify top holdings and their aggregate weight
- Flag if any single position exceeds 10-15% of portfolio
- Calculate Herfindahl-Hirschman Index (HHI) for concentration measurement

**Sector Concentration:**
- Identify dominant sectors
- Flag if any sector exceeds 30-40% of portfolio
- Compare to benchmark sector diversity

**Correlation Analysis:**
- Estimate correlation between major positions
- Identify highly correlated holdings (potential redundancy)
- Assess true diversification benefit

**Number of Positions:**
- Optimal range: 15-30 stocks for individual portfolios
- Flag if under-diversified (<10 stocks) or over-diversified (>50 stocks)

**Output:**
```markdown
## Diversification Assessment

**Concentration Risk:** [Low / Medium / High]
- Top 5 holdings represent XX% of portfolio
- Largest single position: [SYMBOL] at XX%

**Sector Diversification:** [Excellent / Good / Fair / Poor]
- Dominant sector: [Sector Name] at XX%

**Position Count:** [Optimal / Under-diversified / Over-diversified]
- Total positions: XX

**Correlation Concerns:**
- [List any highly correlated position pairs]
```

#### 3.3 Risk Analysis

**Read references/portfolio-risk-metrics.md** for risk measurement frameworks

Calculate and interpret key risk metrics:

**Volatility Measures:**
- Estimated portfolio beta (weighted average of position betas)
- Individual position volatilities
- Portfolio standard deviation (if historical data available via Flex)

**Downside Risk:**
- Maximum drawdown (from Flex Query history, if available)
- Current drawdown from peak
- Positions with significant unrealized losses

**Risk Concentration:**
- Percentage in high-volatility stocks (beta > 1.5)
- Percentage in speculative/unprofitable companies
- Margin / leverage usage (IBKR margin accounts — check buying power vs net liquidation)
- Options and futures exposure (notional and delta-adjusted)

**Tail Risk:**
- Exposure to potential black swan events
- Single-stock concentration risk
- Sector-specific event risk

**Output:**
```markdown
## Risk Assessment

**Overall Risk Profile:** [Conservative / Moderate / Aggressive]

**Portfolio Beta:** X.XX (vs market at 1.00)

**Maximum Drawdown:** -XX.X% (if Flex history available)

**Leverage:** [None / X.Xx] (net liquidation vs gross position value)

**High-Risk Positions:**
| Symbol | % of Portfolio | Beta | Risk Factor |
|--------|----------------|------|-------------|
| [TICKER] | XX% | X.XX | [High volatility / Recent loss / etc] |

**Risk Score:** XX/100 ([Low/Medium/High] risk)
```

#### 3.4 Performance Analysis

Evaluate portfolio performance using available data:

**Absolute Returns:**
- Overall portfolio unrealized P&L ($ and %)
- Best performing positions (top 5 by % gain)
- Worst performing positions (bottom 5 by % loss)

**Time-Weighted Returns (if Flex history available):**
- YTD return
- 1-year, 3-year, 5-year annualized returns
- Compare to benchmark (S&P 500, relevant index)
- Realized P&L and dividend income from Flex statements

**Position-Level Performance:**
- Winners vs Losers ratio
- Average gain on winning positions / average loss on losing positions
- Positions near 52-week highs/lows

**Output:**
```markdown
## Performance Review

**Net Liquidation Value:** $XXX,XXX
**Total Unrealized P&L:** $XX,XXX (+XX.X%)
**Cash Balance:** $XX,XXX (XX% of portfolio)

**Best Performers:**
| Symbol | Gain | Position Value |
|--------|------|----------------|
| [TICKER] | +XX.X% | $XX,XXX |

**Worst Performers:**
| Symbol | Loss | Position Value |
|--------|------|----------------|
| [TICKER] | -XX.X% | $XX,XXX |
```

### Step 4: Individual Position Analysis

For key positions (top 10-15 by portfolio weight), perform detailed analysis.

**Read references/position-evaluation.md** for position analysis framework

For each significant position evaluate: current thesis validation, valuation assessment, technical health, position sizing, and a clear action recommendation (**HOLD / ADD / TRIM / SELL**).

**Output per position:**
```markdown
### [SYMBOL] - [Company Name] (XX.X% of portfolio)

**Position Details:**
- Asset Class: STK | Shares: XXX
- Avg Cost: $XX.XX | Current Price: $XX.XX
- Market Value: $XX,XXX | Unrealized P/L: $X,XXX (+XX.X%)

**Fundamental Snapshot:** Sector, Market Cap, P/E, Dividend Yield, recent developments

**Technical Status:** Trend, price vs 50-day MA, support/resistance

**Position Assessment:** Thesis status, valuation, position sizing

**Recommendation:** [HOLD / ADD / TRIM / SELL]
**Rationale:** [1-2 sentence explanation]
```

### Step 5: Rebalancing Recommendations

**Read references/rebalancing-strategies.md** for rebalancing approaches

Generate specific rebalancing recommendations:
- **Identify triggers:** positions drifted from target, sector/asset-class drift, overweight positions to trim, underweight areas to add, tax considerations, working orders already in the market
- **Develop the plan:** positions to TRIM, positions to ADD, cash deployment
- **Prioritize:** Immediate (risk reduction) → High (major drift >10%) → Medium (5-10%) → Low (fine-tuning)

**Output:**
```markdown
## Rebalancing Recommendations

### Summary
- **Rebalancing Needed:** [Yes / No / Optional]
- **Primary Reason:** [Concentration risk / Sector drift / Cash deployment]
- **Estimated Trades:** X sell, Y buy

### Recommended Actions

#### HIGH PRIORITY: Risk Reduction
**TRIM [SYMBOL]** from XX% to YY%
- Shares to Sell: XX (~$XX,XXX)
- Rationale / Tax Impact: [...]

#### MEDIUM PRIORITY: Asset Allocation
**ADD [Sector/Asset Class]** exposure — target, suggested names, amount

#### CASH DEPLOYMENT
Current cash $XX,XXX (XX%) — recommendation and suggested allocation

### Implementation Plan
1. [Highest priority action]
2. ...
```

> Rebalancing output is a plan for the user to review and execute. This skill does **not** place orders. If the user wants order placement, that is a separate, explicitly-authorized step at the broker.

### Step 6: Generate Portfolio Report

Create a comprehensive markdown report. **Save it to the `reports/` directory** (create it if it does not exist).

**Filename:** `reports/ib_portfolio_analysis_YYYY-MM-DD.md`

**Report Structure:**

```markdown
# Interactive Brokers Portfolio Analysis Report

**Account:** [Account id / paper vs live] | **Base Currency:** [USD]
**Report Date:** YYYY-MM-DD
**Net Liquidation Value:** $XXX,XXX
**Total P&L:** $XX,XXX (+XX.X%)

---

## Executive Summary
[3-5 bullet points: health, strengths, key risks, primary recommendations]

## Holdings Overview
[Summary table of all positions]

## Asset Allocation
[Section from Step 3.1]

## Diversification Analysis
[Section from Step 3.2]

## Risk Assessment
[Section from Step 3.3]

## Performance Review
[Section from Step 3.4]

## Position Analysis
[Detailed analysis of top 10-15 positions from Step 4]

## Rebalancing Recommendations
[Section from Step 5]

## Action Items
**Immediate:** - [ ] ...
**Medium-Term:** - [ ] ...
**Monitoring:** - [ ] ...

## Appendix: Full Holdings
[Complete table with all positions and metrics]
```

### Step 7: Interactive Follow-up

Be prepared to answer follow-up questions: why to sell/trim a position, what to buy instead, biggest risk, comparison to a benchmark, whether to rebalance now or wait, and deep-dives on specific positions (use the `us-stock-analysis` skill for detailed single-name work and fold findings back into portfolio context).

## Analysis Frameworks

### Target Allocation Templates

**Read references/target-allocations.md** for model portfolios: Conservative / Moderate / Growth / Aggressive — each with asset-class targets, sector guidelines, market-cap distribution, geographic allocation, and position-sizing rules. Use these as comparison benchmarks when the user has not specified an allocation strategy.

### Risk Profile Assessment

If the user's target allocation is unknown, infer an appropriate risk profile from age, timeline, current allocation, and position types. **Read references/risk-profile-questionnaire.md** for the assessment framework.

## Output Guidelines

- Objective, analytical tone; actionable recommendations with clear rationale; quantify whenever possible
- Tables for comparisons and metrics; percentages for allocations/returns; dollar amounts for absolute values
- Explicit action verbs (TRIM/ADD/HOLD/SELL), specific quantities, priority levels, supporting rationale
- All reports saved to `reports/`, dated, English, with probability assessments and trigger levels where applicable

## Reference Files

Load these references as needed during analysis:

**references/ib-mcp-setup.md** — Interactive Brokers MCP Server setup: building the vendored server (`vendor/interactive-brokers-mcp/`), browser vs headless authentication, environment variables, read-only mode, Flex Query token setup, gateway lifecycle, and troubleshooting.

**references/asset-allocation.md** — Asset allocation theory, optimal allocation by risk profile, sector guidelines, rebalancing triggers.

**references/diversification-principles.md** — Modern portfolio theory, correlation, optimal position count, concentration thresholds, diversification metrics.

**references/portfolio-risk-metrics.md** — Beta, standard deviation, Sharpe ratio, maximum drawdown, VaR, risk-adjusted return metrics.

**references/position-evaluation.md** — Position analysis framework, thesis validation, position sizing, sell discipline.

**references/rebalancing-strategies.md** — Calendar/threshold/tactical rebalancing, tax optimization, transaction-cost and timing considerations.

**references/target-allocations.md** — Model portfolios for conservative/moderate/growth/aggressive investors.

**references/risk-profile-questionnaire.md** — Risk tolerance assessment questions and scoring.

## Error Handling

**If the Interactive Brokers MCP Server is not connected:**
1. Inform the user that the Interactive Brokers MCP integration is required
2. Provide setup instructions from `references/ib-mcp-setup.md`
3. Offer the fallback: manual CSV data entry (`symbol,quantity,cost_basis,current_price`) — less ideal; no live or historical data

**If the gateway is reachable but not authenticated:**
- The IBKR session may have expired (sessions time out) or 2FA was not completed
- Ask the user to complete the login / 2FA prompt, or re-run with headless credentials, then retry
- Run `python3 skills/ib-portfolio-manager/scripts/check_ib_connection.py` to diagnose

**If position data seems stale or incomplete:**
- Proceed with available data, note the limitation in the report, recommend a refresh
- For options/futures, do not silently fold notional into equity exposure — call it out

**If the user has no positions:**
- Acknowledge the empty portfolio and offer construction guidance (`value-dividend-screener`, `us-stock-analysis`)

## Limitations and Disclaimers

*This analysis is for informational purposes only and does not constitute financial advice. Investment decisions should be made based on individual circumstances, risk tolerance, and financial goals. Past performance does not guarantee future results.*

*`interactive-brokers-mcp` is unofficial, alpha-stage software not affiliated with Interactive Brokers. Test with a paper-trading account first, run it locally only, and never commit IBKR credentials. Data accuracy depends on the IBKR Client Portal API and third-party market data. Tax implications are estimates only; consult a tax professional.*
