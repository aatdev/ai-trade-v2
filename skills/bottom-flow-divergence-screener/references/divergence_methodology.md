# Bottom + Flow Divergence — Methodology

## The Core Idea

A stock makes a low in *price* long before — or sometimes without — a low in the
*business* behind it, or in the *flow of money* into the tape. When those
diverge, the price low is suspect. This screener looks for exactly that
disagreement:

> **Price is on the floor, but a flow signal refuses to confirm the floor.**

Two archetypes motivate the design:

- **HOOD (fundamental recovery):** crashed ~80% off its IPO highs, traded near
  multi-year lows — yet transaction + net-interest revenue and operating cash
  flow inflected back up while the price was still depressed. The business
  bottomed before the chart did.
- **MRNA-type (accumulation despite weak fundamentals):** revenue falling post
  catalyst, still unprofitable — yet the tape shows persistent net buying
  (positive money flow) at the lows. A contrarian, higher-risk bet that smart
  money is positioning ahead of a turn.

These are different signals, so the screener treats them as two separate layers
and grades a name by which (or both) it satisfies.

## Step 1 — The Bottom Gate (hard, applied first)

A name must genuinely be *on the floor*. Both conditions are required:

| Condition | Field | Default |
|---|---|---|
| Near the 52-week low | `(close − low_52w) / low_52w` | ≤ 25% above the low |
| Deep below the 52-week high | `(high_52w − close) / high_52w` | ≥ 35% below the high |

The TradingView scanner API does **not** support arithmetic on the right-hand
side of a filter, so "within X% of the 52-week low" cannot be expressed as a
server-side filter. Proximity is therefore computed **client-side** from the
`close`, `price_52_week_low`, and `price_52_week_high` columns after the fetch.
A loose server pre-filter (`Perf.Y < −10%`, plus liquidity floors) keeps the
fetched set focused; the real gate runs locally.

The "deep below the high" leg matters: a name can sit near its 52-week low while
that low is only 13% below the high (a tight, shallow range) — that is not a
beaten-down bottom, so it is rejected (`not_deep_enough`).

## Step 2 — The Two Divergence Layers

### Fundamental flow (the business never broke)

```
total_revenue_yoy_growth_ttm > 0   (TTM revenue still growing — "didn't fall")
AND cash_f_operating_activities_ttm > 0   (positive operating cash flow)
```

Positive operating cash flow is itself a survivability signal, so fundamental-OK
names are almost always survivable.

### Accumulation flow (smart money buying the lows)

```
ChaikinMoneyFlow > 0   (Chaikin Money Flow accumulation, −1..+1)
OR MoneyFlow ≥ 50      (Money Flow Index buying pressure, 0..100)
```

This is a tape signal, independent of the fundamentals — it can fire on a name
whose revenue is falling (the contrarian layer).

## Step 3 — Grades

| Grade | Bottom gate | Fundamental | Accumulation | Read |
|---|---|---|---|---|
| **A** | ✓ | ✓ | ✓ | Dual divergence — flows healthy AND tape accumulating |
| **B-accum** | ✓ | ✗ | ✓ | Only the tape is positive (contrarian / MRNA-type, speculative) |
| **B-fund** | ✓ | ✓ | ✗ | Business intact, tape hasn't turned yet |
| rejected | ✗ or neither layer | — | — | Not on the floor, or no divergence |

## Step 4 — Tags (informational, do not gate by default)

- **▲turning / ▽falling** — `Perf.3M ≥ 0` OR `close > SMA50`. Separates a base
  forming from a name still bleeding toward the floor. (`--require-turn` makes
  this a hard gate.)
- **recovering** — `total_revenue_qoq_growth_fq ≥ 5%`. Sequential
  re-acceleration ("recovering"), distinct from **resilient** —
  `total_revenue_yoy_growth_ttm ≥ 15%`, steady high growth that simply never
  fell. A name can be both. This is the axis that distinguishes the user's
  "восстанавливаются" (recovering) from "не падали вовсе" (resilient).
- **⚠M&A?** — `revTTM > 50%` OR `revQoQ > 40%`. Growth this large is usually
  acquisition-driven, not organic; the divergence may be an artifact. **Verify
  manually** (e.g. against the latest 10-Q/press release) before trusting it.
- **Risk flags** — `unprofitable` (net income ≤ 0), `fcf_negative`
  (free cash flow ≤ 0), `low_altman_z` (Altman Z < 3).

## Survivability

A beaten-down name must survive long enough to revert. Survivability is true if
**any** of:

```
net_income_ttm > 0  OR  free_cash_flow_ttm > 0
OR altman_z_score_ttm > 3  OR  current_ratio_fq > 1.5
```

OR-logic is deliberate: the **Altman Z-score is unreliable for financials, REITs,
and asset managers** (the formula assumes an industrial balance sheet), so a
`low_altman_z` flag on a bank or a KKR-type name is usually noise — those names
still pass survivability via positive net income or current ratio.

`--require-survivable` turns this into a hard gate. It is **off by default** so
the B-accum (contrarian) layer — which is frequently unprofitable by nature —
is not silently gutted.

## Composite Score (ranking within a grade)

```
score =  min(max(revTTM, 0), 60) * 0.6        # flow strength, capped (M&A outliers)
       + max(revQoQ, 0)          * 1.2         # recent re-acceleration weighted up
       + max(fcf_margin, 0)      * 0.5         # cash-generative quality
       + 25 * max(CMF, 0)                       # Chaikin accumulation (−1..1 scaled)
       + max(MFI − 50, 0)        * 0.6         # MFI buying pressure above neutral
       + max(Perf.3M − Perf.6M, 0) * 0.3       # decline decelerating = bottoming
       + 10  if survivable
       + 5   if turning
```

The score ranks *within* a grade; it is not comparable across the bottom gate
(a rejected name has no score). Weights bias toward recent re-acceleration
(QoQ) and genuine accumulation (CMF/MFI) over backward-looking TTM levels.

## Known Limitations

- **A snapshot, not a timing tool.** The bottom gate finds candidates near a
  low; it cannot confirm the low is *in*. Pair with `--require-turn` and chart
  confirmation (`technical-analyst`).
- **Sector clustering.** Beaten-down A-grade names often cluster in one sector
  (e.g. software/SaaS/fintech derating). They fell for one macro reason and may
  revert together on a macro trigger — treat the cluster as one bet, not N
  independent ones.
- **M&A distortion.** TTM/QoQ growth does not distinguish organic from
  acquired revenue; the ⚠M&A? flag is a heuristic, not a determination.
- **No short-interest / borrow data.** Unlike a short screener, this is long-
  biased and does not model squeeze dynamics.
- **Point-in-time fundamentals.** The scanner returns the latest reported
  figures; it does not reconstruct what the data looked like on a past date, so
  historical replay (`--fixture`) only reflects the fixture's stored values.
