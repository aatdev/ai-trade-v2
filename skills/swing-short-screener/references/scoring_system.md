# Swing Short Screener Scoring System

## 5-Factor Weighted Weakness Score

| Factor | Weight | Source |
|--------|--------|--------|
| Trend Structure (Stage 4) | 30% | MA50/MA200 position, death cross, MA50 slope |
| Relative Strength (inverted) | 25% | Underperformance vs SPY over the lookback |
| Base Breakdown on Volume | 20% | Prior 20-day support break + volume expansion |
| Lower-Highs Structure | 15% | Recent 20d swing high vs prior 20d swing high |
| Liquidity / Borrow Suitability | 10% | Avg daily dollar volume + share price |

Composite = Σ(factor × weight), range 0-100. Higher = more confirmed weakness.

## Factor Scoring Details

### 1. Trend Structure (0-100)
Additive points:

| Condition | Points |
|-----------|--------|
| Price below MA200 | 40 |
| Death cross (MA50 < MA200) | 25 |
| Price below MA50 | 20 |
| MA50 falling (vs ~10 sessions ago) | 15 |

A perfect Stage 4 structure scores 100.

### 2. Relative Strength (0-100)
`rel = stock_return − index_return` over the RS lookback (default 63 sessions).

- `rel ≤ −20%` (20pts+ underperformance) → 100
- `rel ≥ 0%` (matches or beats index) → 0
- Linear in between: `score = clamp(−rel / 0.20 × 100, 0, 100)`

### 3. Base Breakdown on Volume (0-100)
- Support break (latest close below the prior 20-session low): **+50**
- Volume expansion: `clamp((vol_ratio − 1) × 50, 0, 50)` where `vol_ratio` is
  today's volume ÷ trailing 20-day average. A 2× spike adds the full 50.

A clean break on heavy volume scores 100.

### 4. Lower-Highs Structure (0-100)
`pct = (prior_20d_high − recent_20d_high) / prior_20d_high`. A 10%+ lower high
maxes the factor: `score = clamp(pct / 0.10 × 100, 0, 100)`.

### 5. Liquidity / Borrow Suitability (0-100)

| Avg daily dollar volume | Score |
|-------------------------|-------|
| ≥ $50M | 100 |
| ≥ $10M | 60 |
| ≥ $3M | 30 |
| < $3M | 10 |

Price < $5 halves the score (low-float squeeze risk).

## Grade Bands

| Composite | Grade | Guidance |
|-----------|-------|----------|
| 80-100 | A | Clean Stage 4 weakness — prime swing-short candidate |
| 65-79 | B | Strong weakness — tradable on a confirmed break |
| 50-64 | C | Developing weakness — watchlist |
| <50 | D | Weak signal — skip (dropped unless `--min-grade D`) |

## Hard Invalidation (pre-score reject)

A name is rejected before scoring if any of:

- **Insufficient history** (< 200 sessions — cannot compute MA200)
- **Above MA200** (`above_ma200_not_stage4`) — not a Stage 4 decline
- **Price < `--min-price`** (default $5, `price_below_min`)
- **Avg dollar volume < `--min-dollar-vol`** (default $3M, `illiquid_squeeze_risk`)

## State Cap — Oversold / Extended

If `RSI(14) < 25` OR price is `> 20%` below MA50, the move is overextended and
prone to a mean-reversion bounce. The grade is **capped at C** (an A/B raw grade
becomes C) and flagged ★. `raw_grade` is preserved in the JSON so you can see
the uncapped structural quality. The better play is a lower-high retest entry,
not chasing the breakdown.

## Short Trade Levels (per candidate)

- **Entry** — current price (breakdown level)
- **Stop** — recent 20-session swing high (`recent_high_20`)
- **Risk** — `stop − entry`
- **Target (2R)** — `entry − 2 × risk`

These are suggestions for review, not orders. Confirm borrow/locate and SSR at
the broker before acting.
