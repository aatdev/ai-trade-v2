---
name: bottom-flow-divergence-screener
description: Screen the US market for "bottom + flow divergence" — stocks whose PRICE is on the floor (near the 52-week low, deep below the 52-week high) while a FLOW signal refuses to confirm that floor. Two layers: fundamental (TTM revenue still growing + positive operating cash flow — the business never broke, HOOD-type) and accumulation (Chaikin Money Flow / Money Flow Index positive — smart money buying the lows, the contrarian MRNA-type). Assigns A / B-accum / B-fund grades with turning/recovering/survivability tags and emits a JSON + Markdown watchlist. Use when the user wants beaten-down reversal candidates, bottom-fishing with positive divergence, "stocks at the bottom but cash flows recovering", or "акции на дне, но потоки не падали / восстанавливаются". No API key. Detection-only.
---

# Bottom Flow Divergence Screener

Find stocks on the floor where the flows disagree with the price. Price is near
its 52-week low and deep below its 52-week high, but a flow signal is *not*
confirming the bottom — either the fundamentals never broke (revenue still
growing, operating cash flow positive) or the tape is being accumulated (Chaikin
Money Flow / Money Flow Index positive). This is the classic "fundamental
bottoming / positive divergence" setup (e.g. HOOD before its recovery; the
accumulation layer is the MRNA-type contrarian bet).

Data comes from one POST to the public `scanner.tradingview.com` endpoint (the
same "All Stocks" screener TradingView's web UI uses) — **no API key, no auth, no
TradingView Desktop**. Detection-only: emits JSON + Markdown a human reviews
against a broker before any entry.

## When to Use

- User wants beaten-down reversal candidates / bottom-fishing ideas with a
  quality filter ("stocks at the bottom but flows are recovering or never fell")
- User describes the HOOD/MRNA pattern: crashed price, intact or improving
  business / cash flow, or smart-money accumulation at the lows
- User wants to separate genuine bottoming from value traps via flow divergence

Do NOT use for:

- Long-side momentum / breakout screening near highs — use `vcp-screener`,
  `canslim-screener`, or the `tradingview-screener` momentum presets
- Short-side Stage 4 weakness — use `swing-short-screener`
- Arbitrary fundamental/technical filtering — use `tradingview-screener`
- Order execution — this skill is detection-only

## Prerequisites

- Python 3.9+ (standard library only — `urllib`, no third-party deps)
- Network access to `scanner.tradingview.com` for live scans
- No data layer / API key needed for `--fixture` offline replay (testing)

## Workflow

### Step 1: Execute Screening

```bash
# Live scan: beaten-down liquid US common stocks, all grades, top 40
python3 skills/bottom-flow-divergence-screener/scripts/screen_bottom_flow.py \
  --output-dir reports/

# Clean dual-divergence only: graded A, already turning up, can survive
python3 skills/bottom-flow-divergence-screener/scripts/screen_bottom_flow.py \
  --grades A --require-turn --require-survivable --output-dir reports/

# Contrarian accumulation layer (the "MRNA-type" — flows weak, tape accumulating)
python3 skills/bottom-flow-divergence-screener/scripts/screen_bottom_flow.py \
  --grades B-accum --output-dir reports/

# Offline replay from the bundled fixture (no network)
python3 skills/bottom-flow-divergence-screener/scripts/screen_bottom_flow.py \
  --fixture skills/bottom-flow-divergence-screener/scripts/tests/fixtures/sample.json \
  --as-of 2026-06-17 --output-dir reports/

# Inspect the scanner payload without calling the network
python3 skills/bottom-flow-divergence-screener/scripts/screen_bottom_flow.py --dry-run
```

Tuning flags:

| Flag | Default | Effect |
|------|---------|--------|
| `--near-low-pct` | 25.0 | Max % above the 52w low to count as "on the floor" |
| `--min-drawdown-pct` | 35.0 | Min % below the 52w high to count as "beaten down" |
| `--rev-ttm-min` | 0.0 | TTM revenue-growth floor for the fundamental layer |
| `--mfi-min` | 50.0 | Money Flow Index accumulation threshold |
| `--grades` | A,B-accum,B-fund | Comma list of grades to keep |
| `--require-turn` | off | Drop names still falling (keep Perf.3M≥0 or close>SMA50) |
| `--require-survivable` | off | Drop names failing the survivability check |
| `--max-perf-1y` | -10.0 | Server pre-filter: require Perf.Y below this |
| `--min-cap` | 1B | Min market cap (USD) |
| `--min-avg-vol` | 500K | Min 30-day average volume (shares) |
| `--min-price` | 5.0 | Min close price |
| `--top` | 40 | Max candidates in the report (0 = all) |
| `--universe` | common | `common` (common stocks) or `all` (+preferred) |

### Step 2: Apply the Divergence Model

The screener applies a hard **bottom gate** first (near the 52w low AND deep
below the 52w high), then grades survivors on two divergence layers (load
`references/divergence_methodology.md` for the full rubric):

1. **Fundamental flow** — `total_revenue_yoy_growth_ttm > 0` AND
   `cash_f_operating_activities_ttm > 0` (the business never broke)
2. **Accumulation flow** — `ChaikinMoneyFlow > 0` OR `MoneyFlow (MFI) ≥ 50`
   (smart money buying the lows)

Grades:

- **A** — bottom + BOTH layers (flows healthy AND tape accumulating)
- **B-accum** — bottom + accumulation only (flows weak/negative — contrarian,
  speculative; the "MRNA-type")
- **B-fund** — bottom + fundamentals only (intact business, no tape accumulation
  yet)

Every candidate carries informational tags: **▲turning / ▽falling** (early
reversal confirmation), **recovering** (QoQ revenue re-accelerating) vs
**resilient** (high steady TTM growth), **⚠M&A?** (suspiciously high growth —
likely inorganic, verify manually), and survivability **risk flags**
(`unprofitable`, `fcf_negative`, `low_altman_z`). The two optional hard gates
(`--require-turn`, `--require-survivable`) drop names that fail those checks.

### Step 3: Present the Watchlist

For each top candidate, present:

- **Grade** (A / B-accum / B-fund) and composite score
- Proximity to the floor (% above 52w low, % below 52w high) and 1y/3m perf
- Which divergence is firing (fundamental, accumulation, or both) and the tags
- Caveats: ⚠M&A? growth needs a manual organic-vs-inorganic check; `low_altman_z`
  is unreliable for financials/REITs (the formula doesn't fit them)

### Step 4: Provide Actionable Guidance

**By Grade:**
- **A:** Cleanest setup — price on the floor, business healthy, tape accumulating.
  Confirm a base on the chart (`technical-analyst`) before entry.
- **B-accum:** Contrarian — only the tape is positive; the fundamentals are weak
  or negative. Higher risk; size down and define invalidation tightly.
- **B-fund:** Business is fine but the tape hasn't turned. Watchlist until
  accumulation appears or the price confirms a base.

**Always remind the user (this skill cannot verify):**
- A near-low name can keep falling — the bottom gate finds candidates, not bottoms.
  Use `--require-turn` and chart confirmation to avoid catching a falling knife.
- High growth may be acquisition-driven (⚠M&A?) — confirm it is organic.
- Beaten-down clusters in one sector usually fell for one macro reason; they may
  revert together on a macro trigger, not company by company.

## Output Format

- `bottom_flow_divergence_YYYY-MM-DD_HHMMSS.json` — structured results
  (`meta` + `candidates[]` with grade, score, flow_profile, tags, risk_flags,
  and the full metric set)
- `bottom_flow_divergence_YYYY-MM-DD_HHMMSS.md` — ranked watchlist grouped by grade

## Resources

- `references/divergence_methodology.md` — the bottom + divergence framework,
  field definitions, scoring rationale, and data caveats
- `scripts/screen_bottom_flow.py` — main entry point (scan + pipeline + CLI)
- `scripts/scorer.py` — pure bottom-gate + grading + scoring logic
- `scripts/report_generator.py` — JSON + Markdown rendering
- `scripts/tests/` — offline pytest suite + replay fixture

## Combining with Other Skills

1. **bottom-flow-divergence-screener** → graded reversal candidates
2. **technical-analyst** → confirm a base / reversal structure on the chart
3. **us-stock-analysis** → validate the fundamental thesis (and the organic-growth check)
4. **position-sizer** → risk-based share count (size B-accum down)
5. **trader-memory-core** → register the surviving names as IDEA theses
