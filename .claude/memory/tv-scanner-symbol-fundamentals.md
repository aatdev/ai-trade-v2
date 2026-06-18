---
name: tv-scanner-symbol-fundamentals
description: "What scanner.tradingview.com/symbol returns for one ticker — fields, no description text, no RU localization"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 82ddebce-32c3-44e3-8280-9ef10cc0463f
---

`https://scanner.tradingview.com/symbol?symbol=EXCHANGE:TICKER&fields=...&no_404=true` (public, no auth/CDP) for single-ticker fundamentals:

- Works **server-side over plain `fetch`** with a browser `User-Agent` header (no cookies needed). Symbol MUST be exchange-qualified — bare `AAPL` returns JSON `null`.
- Exposes profile (`description`=company name like "Apple Inc.", `sector`, `industry`, `country`, `number_of_employees`), valuation (`market_cap_basic`, `price_earnings_ttm`, `earnings_per_share_diluted_ttm`, `price_sales_current`, `price_book_fq`, `dividends_yield_current`, `close`), 52w (`price_52_week_high/low`), and **performance** (`Perf.W`, `Perf.1M`, `Perf.3M`, `Perf.YTD`, `Perf.Y`).
- Performance + 52w are available on `/symbol` directly (no need for the heavier `/scan` screener endpoint), even unauthenticated.
- **No free-text company description**: `business_description`/`short_description` are null even via the authenticated CDP session. `lang=ru` is a **no-op** — sector/industry/country stay English. So "company info in Russian" = Russian labels + value maps (EN fallback), not TV-sourced RU text.
- `tv fundamentals` CLI uses the same endpoint but its FIELD_GROUPS omit Perf.*/52w (see `vendor/tradingview-mcp/src/core/fundamentals.js`).

Used by UI `/api/fundamentals/:symbol` → `CompanyInfoBar` above the candle chart. RU value maps live in `ui/client/src/lib/tvLocale.ts`. Symbol resolution: reuse `data.resolved` from the OHLCV call. Related: [[vendored-tv-data-layer]].
