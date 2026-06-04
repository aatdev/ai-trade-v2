# Data Layer & Endpoints — Swing Short Screener

> **Runtime data layer:** the live path uses the shared, vendored **TradingView**
> client (`scripts/lib/tv_client.py`), an FMP-compatible `FMPClient` drop-in
> backed by the `vendor/tradingview-mcp` bridge. No FMP key is required (the
> `api_key` argument is accepted only for interface parity). The FMP request
> shapes below document the *interface contract* the data layer satisfies
> (`get_historical_prices` → `{symbol, historical}`, `get_sp500_constituents`,
> `get_quote`) and remain valid if you swap back to a real FMP backend.

The screener consumes end-of-day OHLCV history and the S&P 500 constituent list
through that FMP-compatible interface (`stable` → `v3` fallback, rate limiting,
retries, and caching are handled by the client).

## Endpoints Used

| Purpose | Endpoint (stable) | Fallback (v3) |
|---------|-------------------|---------------|
| EOD history | `stable/historical-price-eod/full?symbol=…` | `api/v3/historical-price-full/{sym}` |
| Real-time quote | `stable/quote?symbol=…` | `api/v3/quote/{sym}` |
| S&P 500 list | `api/v3/sp500_constituent` | — |

The client normalizes the new `stable` flat-list EOD shape into the legacy
`{"symbol", "historical": [...]}` dict the screener expects.

## API Call Budget

Per run:
- 1 call — SPY history (RS benchmark)
- 1 call — S&P 500 constituents (only when `--full-sp500` / no `--universe`)
- 1 call per symbol — EOD history (260 sessions)

So a custom universe of N tickers costs ≈ N + 1 calls. The default
`--max-candidates 100` caps a live S&P 500 run at ≈ 102 calls — within the FMP
free tier's 250/day. A full S&P 500 scan (`--full-sp500`) costs ≈ 500 calls and
needs a paid tier.

## Authentication

```bash
export FMP_API_KEY=your_key_here
# or
python3 skills/swing-short-screener/scripts/screen_short.py --api-key YOUR_KEY ...
```

If no key is found, live mode exits 1 with a clear error. Use `--fixture` for
offline runs that need no key.

## Rate Limiting & Resilience

- 300 ms between requests (`RATE_LIMIT_DELAY`)
- One automatic retry with a 60 s wait on HTTP 429, then `rate_limit_reached`
  stops the run gracefully (partial results still reported)
- Per-endpoint circuit breaker disables an endpoint after 3 consecutive failures
- HTTP 402 (subscription-gated symbol) skips the symbol without poisoning the
  circuit breaker

## Pricing

- **Free:** 250 calls/day — fine for a custom universe or capped S&P 500 run
- **Starter ($29.99/mo):** 750 calls/day — comfortable for daily full S&P 500
- Sign up: https://site.financialmodelingprep.com/developer/docs
