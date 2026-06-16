---
name: breakout-trade-planner
description: Generate Minervini-style breakout trade plans from VCP screener output with worst-case risk calculation, portfolio heat management, and broker order templates — Alpaca-shaped bracket JSON plus Interactive Brokers MCP place_order leg sequences (stop-limit/limit for pre-placement/post-confirmation). Use when user has VCP screener results and wants actionable trade plans with entry/stop/target levels and position sizing.
---

# Breakout Trade Planner

Generate trade plans from VCP screener output following Mark Minervini's breakout methodology. Calculate position sizes using worst-case entry prices, enforce portfolio risk limits, and output broker order templates — Alpaca-shaped bracket JSON and/or Interactive Brokers MCP `place_order` leg sequences (select with `--broker alpaca|ib|both`).

## When to Use

- User has VCP screener JSON output and wants trade plans
- User asks for breakout entry/stop/target calculation
- User wants broker-ready order templates (Alpaca or Interactive Brokers) for VCP breakout candidates
- User needs position sizing with portfolio heat management

## Prerequisites

- VCP screener JSON output with `schema_version: "1.0"`
- No API keys required (works with local JSON files)
- `--earnings-gate-days` needs network access to the public TradingView scanner
  (`scanner.tradingview.com`) — same keyless endpoint as tradingview-screener
- No external skill dependencies (position sizing is built-in)

## Workflow

### Step 1: Generate Trade Plans

Run the planner with VCP screener output:

```bash
python3 skills/breakout-trade-planner/scripts/plan_breakout_trades.py \
  --input reports/vcp_screener_YYYY-MM-DD.json \
  --account-size 100000 \
  --risk-pct 0.5 \
  --earnings-gate-days 10 \
  --time-stop-trading-days 15 \
  --output-dir reports/
```

With a parameter profile (recommended for a fixed personal risk setup; explicit
CLI flags override profile values):

```bash
python3 skills/breakout-trade-planner/scripts/plan_breakout_trades.py \
  --input reports/vcp_screener_YYYY-MM-DD.json \
  --profile trading_profile.json \
  --output-dir reports/
```

### Step 2: Review Output

Read the generated JSON and Markdown reports. Present:

1. **Actionable Orders** — Pre-breakout candidates with order templates
2. **Revalidation** — Breakout-state candidates needing live confirmation
3. **Watchlist** — Developing VCP candidates to monitor
4. **Blocked (earnings gate)** — Plans suppressed because earnings are within the gate window
5. **Blocked (fundamental floor)** — Plans suppressed: latest quarter unprofitable, or EPS and revenue both shrinking YoY
6. **Rejected/Deferred/Constrained** — Candidates filtered by Gate or portfolio limits
### Step 3: Explain Trade Plans

For each actionable order, explain:
- Entry levels (signal vs worst-case) and stop-loss placement
- R-multiple targets and reward-risk ratio
- Two execution modes: pre_place (stop-limit) vs post_confirm (limit after 5min confirmation)
- Broker output (`--broker alpaca|ib|both`, default both): Alpaca bracket JSON (`pre_place`/`post_confirm`) and/or Interactive Brokers MCP `place_order` leg sequences (`pre_place_ib`/`post_confirm_ib`: entry → stop_loss → take_profit, placed as a manual OCO since the IB MCP has no native bracket/OCA/stop-limit)
- Portfolio risk contribution and cumulative heat

## Minervini Gate (Filtering Criteria)

Candidates must pass ALL conditions:

| Condition | Pre-breakout | Breakout |
|-----------|-------------|----------|
| valid_vcp | True | True |
| rating_band | good/strong/textbook | good/strong/textbook |
| risk_pct_worst | <= 8.0% | <= 8.0% |
| breakout_volume | — | True |
| distance_from_pivot | — | <= max_chase_pct |
| current_price | — | <= worst_entry |

## CLI Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| --account-size | (required*) | Account equity in dollars (*or via --profile) |
| --profile | $TRADING_PROFILE | JSON parameter profile; explicit CLI flags override it |
| --risk-pct | 0.5 | Base risk % per trade |
| --max-position-pct | 10.0 | Max single position % |
| --max-sector-pct | 30.0 | Max sector exposure % |
| --max-portfolio-heat-pct | 6.0 | Max total open risk % |
| --target-r-multiple | 2.0 | Take-profit R-multiple |
| --stop-buffer-pct | 1.0 | Stop buffer below contraction low |
| --max-chase-pct | 2.0 | Max chase above pivot |
| --pivot-buffer-pct | 0.1 | Pivot buffer for buy-stop trigger |
| --earnings-gate-days | 0 (off) | Block plans with earnings within N trading days (public TradingView scanner; no key) |
| --fundamental-gate | 0 (off) | Soft quality-floor on longs: drop negative latest-quarter EPS or both EPS & revenue shrinking YoY; annotate the rest with CANSLIM C/A (TradingView fundamentals; no key) |
| --time-stop-trading-days | 0 (off) | Annotate plans: exit if < +1R after N trading days from entry |
| --current-exposure-json | None | Existing portfolio exposure (e.g. trader-memory-core `heat` output) |

## Earnings Gate

With `--earnings-gate-days N`, the planner queries the public TradingView
scanner (`earnings_release_next_date`, one POST for the whole batch, no API
key) and annotates every actionable, revalidation, and watchlist entry with
`earnings_date` (US-Eastern calendar date), `days_to_earnings` (weekday count,
holidays ignored — errs on the blocking side), and `earnings_gate`
(`pass` / `blocked` / `unknown`). Actionable and revalidation plans with a
report within N trading days (inclusive) move to the `blocked_earnings`
section and never consume portfolio heat. If the scanner is unreachable, plans
stay live with `earnings_gate: "unknown"` and an `EARNINGS_GATE_DEGRADED`
warning — verify dates manually before entry.

## Fundamental Gate

With `--fundamental-gate 1`, the planner pulls quarterly and annual income
statements from the shared TradingView data layer (no API key) and computes the
CANSLIM C (quarterly EPS/revenue growth) and A (annual EPS CAGR) components. A
**soft quality-floor** drops a long only on clear deterioration — the latest
quarter EPS is negative, or BOTH EPS and revenue are shrinking year-over-year;
everything else is kept and annotated with `fundamental_gate`
(`pass`/`blocked`/`unknown`), `c_score`, `a_score`, `eps_growth_yoy`, and
`revenue_growth_yoy`. Ranking and sizing stay driven by the VCP composite score
— the floor never reshapes them. Blocked actionable / revalidation plans move to
the `blocked_fundamental` section and never consume heat. Missing data fails
open (`unknown`); a total fetch failure emits a `FUNDAMENTAL_GATE_DEGRADED`
warning. The floor is intentionally lighter than the full canslim-screener,
which a wide S&P 500 universe of quality leaders would fail on O'Neil's 18%/25%
momentum thresholds.

## Parameter Profile

`--profile` (or `$TRADING_PROFILE`) points at a JSON file of personal defaults
shared by the trading scripts (see `trading_profile.example.json` at the repo
root; copy to a gitignored `trading_profile.json`). Recognized keys here:
`account_size`, `risk_pct`, `max_position_pct`, `max_sector_pct`,
`max_portfolio_heat_pct`, `target_r_multiple`, `stop_buffer_pct`,
`max_chase_pct`, `pivot_buffer_pct`, `earnings_gate_days`,
`time_stop_trading_days`, `fundamental_gate`. Keys used by sibling scripts (e.g. `atr_multiplier`,
`max_positions`) are skipped silently; unknown keys warn to stderr.

## Output

- `breakout_trade_plan_YYYY-MM-DD_HHMMSS.json` — Structured plans with order templates
- `breakout_trade_plan_YYYY-MM-DD_HHMMSS.md` — Human-readable report

## Resources

- `references/minervini_entry_rules.md` — Entry methodology and rules
