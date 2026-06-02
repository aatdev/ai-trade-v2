# TradingView data layer (FMP replacement)

The screeners no longer need an FMP API key. Their data layer is served from a
live **TradingView Desktop** chart (Chrome DevTools Protocol on `localhost:9222`)
via the globally linked `tv` CLI, plus `scanner.tradingview.com` for the
market-wide helpers. FMP's free tier gated most symbols at the API level; the
TradingView source has no per-symbol or per-day cap.

## What changed

There is **one** copy of each shared module, in `scripts/lib/`:

- `tv_client_base.py` — the shared `TVClient`: price layer (quotes, daily bars,
  `get_sp500_constituents`, SMA/EMA), fundamental layer from the TradingView
  scanner (`get_profile`, `get_income_statement`, `get_company_profile(s)`),
  and the macro helpers (`get_vix_term_structure`, `get_treasury_rates`,
  `get_earnings_calendar`).
- `metrics_cache.py` — OpenSearch-first / local-file fallback fast path
  (reused from the tradingview-mcp-jackson repo).
- `tv_client.py` — fixes the shared config and exposes `FMPClient`
  (alias of `TVClient`, price history as the `{symbol, historical}` dict),
  `TVClientListHistory` (same but `get_historical_prices` returns the bare
  `list[dict]` — used only by earnings-trade-analyzer), and
  `ApiCallBudgetExceeded`.

Nothing is copied or symlinked into the skills. Each migrated entry script puts
`scripts/lib/` on `sys.path` (one self-contained line, just before the import)
and imports the shared modules directly from there:

```python
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "scripts", "lib"))
from tv_client import FMPClient
```

So each skill's code change is that one `sys.path` line plus the import swap
`from fmp_client import FMPClient` → `from tv_client import FMPClient`
(earnings-trade-analyzer imports `TVClientListHistory as FMPClient` for its
list-shaped history). The old `fmp_client.py` is kept in place so the existing
unit tests still run against it.

The metrics-cache fast path is toggled globally with `TV_NO_CACHE=1`.

**Packaging caveat:** `scripts/package_skills.py` archives only files under each
`skills/<skill>/` directory, so the shared `scripts/lib/` modules are **not**
bundled into the `.skill` archives. The skills run fine in-place from the repo;
to ship a self-contained `.skill`, either bundle `scripts/lib/` into the archive
in `package_skills.py`, or vendor the three files back into the skill's
`scripts/` dir at package time.

Migrated skills: `vcp-screener`, `canslim-screener`, `ftd-detector`,
`market-top-detector`, `macro-regime-detector`, `pead-screener`,
`parabolic-short-trade-planner`, `earnings-trade-analyzer`,
`ibd-distribution-day-monitor`.

## Prerequisites

1. **TradingView Desktop running with CDP** on `:9222`
   (`./scripts/launch_tv_debug_mac.sh` in the tradingview-mcp-jackson repo, or
   the `tv_launch` MCP tool).
2. **`tv` CLI on PATH** — run `npm link` once inside the
   tradingview-mcp-jackson checkout.
3. **`TV_MCP_REPO`** (optional) — absolute path to that checkout. Used for
   `state/sp500.csv` and `scripts/tv_earnings_calendar.mjs`, and as a fallback
   `tv` CLI. Defaults to `/Users/alex/Projects/Repos/tradingview-mcp-jackson`.
   Override `TV_CLI` to point at a specific CLI entry.

No FMP key is needed. Skills that still *require* a key argument before
constructing the client (`ibd-distribution-day-monitor`,
`parabolic-short-trade-planner`) accept any placeholder: pass `--api-key tv`
(the value is ignored by `TVClient`).

The metrics-cache fast path can be disabled with `TV_NO_CACHE=1`.

## Known limitations / follow-ups

- **`get_earnings_calendar`** uses the TradingView scanner, which stores only the
  *last* and *next* earnings date per symbol. Recent-past windows (PEAD) and
  near-future windows (upcoming) are covered; deep historical earnings calendars
  are not.
- **Profile-heavy skills** (`earnings-trade-analyzer`, `pead-screener`) fetch a
  company profile per earnings-calendar symbol. Cache misses (illiquid small
  caps not in OpenSearch) drive the chart one symbol at a time, so a broad
  universe is slow. Liquid names are served instantly from the cache. A future
  optimization is to batch profiles through one `scanner.tradingview.com` call
  (the same pattern as `tv_earnings_calendar.mjs`).
- The TradingView institutional-holdings data is not exposed by the scanner, so
  CANSLIM's **I** component falls back to its Finviz client (`finviz_stock_client`).
