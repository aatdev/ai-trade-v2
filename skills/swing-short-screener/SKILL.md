---
name: swing-short-screener
description: Screen S&P 500 (or a custom universe) for Stage 4 downtrend weakness — the short-side mirror of vcp-screener. Scores each name on a 5-factor weighted weakness model (trend structure, relative strength, base breakdown on volume, lower-highs structure, liquidity/borrow suitability), assigns A/B/C/D grades, and emits a JSON + Markdown short-side watchlist. Use when the user wants swing-short candidates, Stage 4 weakness scanning, short watchlists, breakdown screening, or stocks weak relative to the index. Detection-only — never sends orders.
---

# Swing Short Screener — Stage 4 Weakness Scanner

Screen a universe for Stage 4 downtrend weakness and produce a graded short-side
watchlist. This is the inverted mirror of `vcp-screener`: where VCP finds Stage 2
strength near a breakout, this finds Stage 4 weakness breaking down. Detection-only —
it emits JSON + Markdown that a human reviews against their broker before any entry.

## When to Use

- User asks for swing-short candidates, a short watchlist, or "what's weak right now"
- User wants Stage 4 downtrend / breakdown screening
- User wants names underperforming the index (weak relative strength)
- As the candidate-finding step of the `short-opportunity-daily` workflow, after a
  short-favorable posture is confirmed by `exposure-coach`

Do NOT use for:

- Long-side momentum screening — use `vcp-screener` or `canslim-screener`
- Intraday parabolic-exhaustion shorts — use `parabolic-short-trade-planner`
- Order execution — this skill is detection-only

## Prerequisites

- Python 3.9+
- **Data layer:** the live path uses the shared, vendored **TradingView** data
  layer (`scripts/lib/tv_client.py` — the same `FMPClient` drop-in `vcp-screener`
  uses). It needs the vendored bridge under `vendor/tradingview-mcp`
  (`npm install` once; override location with `TV_MCP_REPO`). The interface is
  FMP-compatible, so `--api-key` / `FMP_API_KEY` is accepted for parity but the
  TradingView bridge does not require it.
- No data layer needed for `--fixture` offline mode (testing / replay)

## Workflow

### Step 1: Execute Screening

Run the screener script. Default output goes to `reports/`.

```bash
# Custom universe (no API key — TradingView data layer)
python3 skills/swing-short-screener/scripts/screen_short.py \
  --universe TSLA NFLX PYPL ROKU --output-dir reports/

# Full S&P 500 (slower: ~500 chart pulls via the TV layer)
python3 skills/swing-short-screener/scripts/screen_short.py \
  --full-sp500 --output-dir reports/

# Offline replay from the bundled fixture (no API key)
python3 skills/swing-short-screener/scripts/screen_short.py \
  --fixture skills/swing-short-screener/scripts/tests/fixtures/sample.json \
  --as-of 2026-04-30 --output-dir reports/
```

Tuning flags:

| Flag | Default | Effect |
|------|---------|--------|
| `--rs-lookback` | 63 | Sessions for the relative-strength comparison vs SPY |
| `--min-grade` | C | Drop candidates below this grade (A/B/C/D) |
| `--top` | 25 | Max candidates in the report (0 = all) |
| `--min-price` | 5.0 | Reject sub-price names (squeeze risk) |
| `--min-dollar-vol` | 3,000,000 | Reject illiquid names (borrow / locate risk) |
| `--max-candidates` | 100 | Cap universe size in live S&P 500 mode |

### Step 2: Apply the Weakness Model

The screener hard-invalidates non-candidates first (price above MA200, sub-$5,
illiquid), then scores survivors on five factors (load `references/scoring_system.md`
for the full rubric):

1. **Trend Structure (30%)** — below MA50 & MA200, death cross, MA50 falling
2. **Relative Strength (25%)** — underperformance vs the index over the lookback
3. **Base Breakdown (20%)** — support broken on expanding volume
4. **Lower Highs (15%)** — descending swing-high structure
5. **Liquidity / Borrow (10%)** — tradable, borrowable, low squeeze risk

A name that is RSI-oversold or >20% below its MA50 gets its grade **capped at C**
(falling-knife / bounce risk) — flagged with ★.

### Step 3: Present the Watchlist

For each top candidate, present:

- **Grade** (A/B/C/D) and composite score
- **Strongest weakness signal** (which factor drives the score)
- Short trade levels: entry (current price), stop (most recent lower high within
  20 sessions + 0.5×ATR buffer; falls back to the 20-session max), 2R target.
  Candidates whose stop distance falls outside 2–10% of entry are rejected
  (`--min-stop-pct` / `--max-stop-pct`): below the floor the stop sits in daily
  noise, above the ceiling the geometry is post-crash junk
- ★ marker if the oversold/extended state cap was applied
- Relative strength vs the index and volume ratio on the breakdown

Read `references/weakness_methodology.md` for Stage 4 interpretation context.

### Step 4: Provide Actionable Guidance

**By Grade:**
- **A (80+):** Clean Stage 4 weakness — prime swing-short candidate
- **B (65-79):** Strong weakness — tradable on a confirmed break
- **C (50-64):** Developing weakness — watchlist, wait for a cleaner break or a
  lower-high retest entry
- **D (<50):** Weak signal — skip

**Always remind the user (this skill cannot verify):**
- Confirm a hard-to-borrow locate exists at the broker
- Check SSR (SEC Rule 201) status
- Avoid heavily-shorted names with pending bullish catalysts (squeeze risk)

## Output Format

- `swing_short_screener_YYYY-MM-DD[_HHMMSS].json` — structured results
  (`meta` + `candidates[]` with components, grade, metrics, trade levels)
- `swing_short_screener_YYYY-MM-DD[_HHMMSS].md` — ranked human-readable watchlist

## Resources

- `references/weakness_methodology.md` — Stage 4 theory and factor rationale
- `references/scoring_system.md` — 5-factor weights, grade bands, state cap
- `references/fmp_api_endpoints.md` — FMP endpoints and rate limits
- `scripts/screen_short.py` — main entry point
- `scripts/weakness_metrics.py` — pure metric calculations
- `scripts/scorer.py` — 5-factor weakness scoring engine
- `scripts/report_generator.py` — JSON + Markdown rendering
- Data layer: the shared vendored TradingView client at `scripts/lib/tv_client.py`
  (FMP-compatible `FMPClient` drop-in), loaded at runtime via `sys.path`
