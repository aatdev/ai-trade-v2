# Stage 4 Weakness Methodology

This screener identifies **Stage 4 decline** stocks — the mirror image of the
Stage 2 advance that `vcp-screener` hunts. The framework borrows from Stan
Weinstein's stage analysis and Mark Minervini's trend template, inverted for the
short side.

## The Four Stages (Weinstein)

| Stage | Description | Action |
|-------|-------------|--------|
| 1 — Basing | Sideways after a decline, MAs flattening | Watch |
| 2 — Advancing | Uptrend above rising 30-week MA | Long (VCP territory) |
| 3 — Topping | Sideways after an advance, MAs flattening | Reduce |
| **4 — Declining** | **Downtrend below falling MAs** | **Short territory** |

A swing short wants a confirmed Stage 4: price below both the 50- and 200-day
moving averages, a death cross (MA50 below MA200), and a 50-day MA that is
actively falling. Anything still above the 200-day MA is not Stage 4 and is
hard-rejected.

## Why Each Factor Matters

### 1. Trend Structure (heaviest weight)
The non-negotiable backbone. Price below MA200 confirms the long-term trend is
down; the death cross confirms the intermediate trend has rolled under the
long-term trend; a falling MA50 confirms the decline is current, not stale. A
stock failing the MA200 test cannot be a clean swing short regardless of other
factors.

### 2. Relative Strength (inverted)
Minervini's RS line, inverted. A stock falling *faster than the index* is where
institutional distribution concentrates — the laggards lead lower. Relative
underperformance over the lookback (default 63 sessions ≈ one quarter) is the
short-side analog of the RS leadership VCP demands on the long side.

### 3. Base Breakdown on Volume
The trigger. A clean swing short fires when price breaks below a multi-week
support shelf, and the break is confirmed by *expanding* volume (institutions
exiting, not a quiet drift). A break on a 2×+ volume spike is the highest-quality
distribution signal.

### 4. Lower-Highs Structure
Trend persistence. A series of descending swing highs proves each rally is being
sold. The screener compares the recent 20-session swing high to the prior 20-day
swing high — a lower high means rallies are failing at progressively lower levels.

### 5. Liquidity / Borrow Suitability
The practical filter. Short selling requires a locatable borrow and enough
liquidity to enter and exit without slippage. Thin, low-float, sub-$5 names are
prime short-squeeze fuel and frequently hard-to-borrow — the screener penalizes
or rejects them.

## The Falling-Knife Cap

A stock can be structurally weak yet **too extended to short here**. When RSI(14)
is below 25 or price is more than 20% below its MA50, the move is stretched and
prone to a sharp mean-reversion bounce — exactly when a late short gets squeezed.
Such names have their grade capped at **C** and are flagged ★. The better entry
is a *lower-high retest* (a bounce into the falling MA50 that fails), not chasing
the breakdown.

## The Squeeze Cap

The opposite extreme is just as dangerous: a structurally weak name that is
**being run in right now**. With no free short-interest feed, the screener
approximates squeeze pressure from price action — a single-day close-to-close
pop of ≥ 10% in the last 10 sessions, or price ≥ 15% above its 20-session low (a
large bounce off the lows). Either condition caps the grade at **C** (flagged ★)
and records `squeeze_risk` + `squeeze_reason`. Heavily-shorted, low-float names
squeeze hardest; absent an interest feed the price-action proxy is the no-API
guard. The better entry, again, is a failed lower-high retest once the pop fades
— not adding short risk into an active bounce.

## The Counter-Sector Cap

Don't fight the group. A structurally weak stock whose **sector is leading the
market** — its SPDR Select Sector ETF (XLK, XLV, XLF, …) outperforming SPY by
≥ 5% over the trailing ~3 months — is a poor short: sector inflows lift even the
laggards. Such names have their grade capped at **C** (flagged ★) and record
`sector_fight` + `sector_etf` / `sector_rs` / `sector_leadership`. The mirror
holds for the long side (vcp-screener caps a long in a *lagging* sector). Sector
RS uses the same TradingView data layer as the index benchmark — no API key, no
short-interest feed. Prefer shorts in lagging groups, where the tide is already
out.

## How This Differs From parabolic-short-trade-planner

| | swing-short-screener | parabolic-short-trade-planner |
|---|---|---|
| Pattern | Stage 4 weakness (already broken down) | Parabolic exhaustion (extended *up*, about to break) |
| Horizon | Swing (days–weeks) | Intraday → few days |
| Trigger | Support break confirmed on the daily | Intraday 5-min ORL / first-red / VWAP fail |
| Use together | Screen weak names here, then hand a parabolic name to the planner for an intraday trigger |

## Workflow Integration

This screener is the candidate-finding step of `short-opportunity-daily`. It runs
only after `exposure-coach` confirms a short-favorable posture, and its
`short_candidates` output feeds `technical-analyst` for chart validation, then
`position-sizer` and `trader-memory-core` downstream.
