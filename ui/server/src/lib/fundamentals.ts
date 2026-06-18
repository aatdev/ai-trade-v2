import fs from 'node:fs';
import type { CompanyFundamentals, ExtendedQuote, FundamentalsResponse } from '@shared/types';

/**
 * Fetches company profile + key metrics for the candle-chart modal.
 *
 * Unlike OHLCV bars (which require the authenticated TradingView Desktop chart
 * session), the snapshot fundamentals served by `scanner.tradingview.com/symbol`
 * are available over a plain server-side fetch — no CDP, no cookies — as long as
 * the symbol is exchange-qualified ("NASDAQ:AAPL") and a browser User-Agent is
 * sent. A bare ticker ("AAPL") returns JSON `null`, so we first resolve the
 * exchange via TradingView's symbol-search endpoint (also plain HTTP). We never
 * throw: an unreachable scanner surfaces as `{ ok:false, error }`, mirroring
 * fetchOhlcv()/fetchIbSnapshot() so the UI renders the reason.
 *
 * Testing / offline dev: set `TRADING_UI_FUNDAMENTALS_FIXTURE` to a file holding
 * a raw scanner `/symbol` JSON object and it is parsed straight from disk.
 *
 * NB: TradingView's scanner exposes no free-text business description and does
 * not localize `sector`/`industry`/`country` (lang=ru is a no-op here), so we
 * return the raw EN strings and localize them in the client. The "what the
 * company does" blurb is scraped separately from the public symbol page's
 * JSON-LD (see fetchDescription); it is also EN-only.
 */

const DEFAULT_TIMEOUT_MS = 12_000;
const SCANNER_URL = 'https://scanner.tradingview.com/symbol';
const SEARCH_URL = 'https://symbol-search.tradingview.com/symbol_search/';
const SYMBOL_PAGE_URL = 'https://www.tradingview.com/symbols/';
const USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';

// symbol-search guards against non-browser callers; these headers satisfy it.
const TV_HEADERS = {
  'User-Agent': USER_AGENT,
  Accept: 'application/json',
  Origin: 'https://www.tradingview.com',
  Referer: 'https://www.tradingview.com/',
};

/** Scanner field names, in request order. `Perf.*` keys carry a dot verbatim. */
const FIELDS = [
  'description',
  'sector',
  'industry',
  'country',
  'number_of_employees',
  'market_cap_basic',
  'price_earnings_ttm',
  'earnings_per_share_diluted_ttm',
  'price_sales_current',
  'price_book_fq',
  'dividends_yield_current',
  'close',
  'price_52_week_high',
  'price_52_week_low',
  'Perf.W',
  'Perf.1M',
  'Perf.3M',
  'Perf.YTD',
  'Perf.Y',
  // Live extended-hours quote for the chart header pill.
  'premarket_close',
  'premarket_change',
  'premarket_volume',
  'postmarket_close',
  'postmarket_change',
  'postmarket_volume',
];

export function errorFundamentals(
  symbol: string,
  error: string,
  source: 'live' | 'fixture' = 'live',
): FundamentalsResponse {
  return {
    ok: false,
    symbol,
    data: null,
    premarket: null,
    postmarket: null,
    error,
    source,
    generated_at: new Date().toISOString(),
  };
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Build an extended-hours quote, or null when no extended price is present. */
function extQuote(close: unknown, change: unknown, volume: unknown): ExtendedQuote | null {
  const price = num(close);
  if (price == null) return null;
  return { price, changePct: num(change), volume: num(volume) };
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

/**
 * Map a raw scanner `/symbol` payload into a FundamentalsResponse. Returns a
 * structured error (never throws) when the payload is missing/empty — e.g. the
 * scanner returns a bare `null` for an unqualified symbol like "AAPL".
 */
export function parseFundamentals(
  raw: unknown,
  symbol: string,
  source: 'live' | 'fixture',
): FundamentalsResponse {
  if (raw == null || typeof raw !== 'object' || Array.isArray(raw)) {
    return errorFundamentals(
      symbol,
      'TradingView returned no fundamentals (need an EXCHANGE:TICKER symbol)',
      source,
    );
  }
  const r = raw as Record<string, unknown>;
  const data: CompanyFundamentals = {
    name: str(r.description) ?? str(r.name),
    // Live scanner returns null for every description field; populated from the
    // symbol page in fetchFundamentals(). A fixture may carry one to test the path.
    description: str(r.business_description) ?? str(r.long_description),
    sector: str(r.sector),
    industry: str(r.industry),
    country: str(r.country),
    employees: num(r.number_of_employees),
    marketCap: num(r.market_cap_basic),
    peTtm: num(r.price_earnings_ttm),
    epsTtm: num(r.earnings_per_share_diluted_ttm),
    priceToSales: num(r.price_sales_current),
    priceToBook: num(r.price_book_fq),
    dividendYield: num(r.dividends_yield_current),
    price: num(r.close),
    high52w: num(r.price_52_week_high),
    low52w: num(r.price_52_week_low),
    perfW: num(r['Perf.W']),
    perfM: num(r['Perf.1M']),
    perf3M: num(r['Perf.3M']),
    perfYtd: num(r['Perf.YTD']),
    perfY: num(r['Perf.Y']),
  };
  if (!Object.values(data).some((v) => v != null)) {
    return errorFundamentals(symbol, 'TradingView returned an empty fundamentals payload', source);
  }
  const premarket = extQuote(r.premarket_close, r.premarket_change, r.premarket_volume);
  const postmarket = extQuote(r.postmarket_close, r.postmarket_change, r.postmarket_volume);
  return {
    ok: true,
    symbol,
    data,
    premarket,
    postmarket,
    error: null,
    source,
    generated_at: new Date().toISOString(),
  };
}

function readFixture(file: string, symbol: string): FundamentalsResponse {
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    return errorFundamentals(symbol, `fundamentals fixture not found: ${(e as Error).message}`, 'fixture');
  }
  try {
    return parseFundamentals(JSON.parse(text), symbol, 'fixture');
  } catch (e) {
    return errorFundamentals(symbol, `fundamentals fixture is not valid JSON: ${(e as Error).message}`, 'fixture');
  }
}

/**
 * Resolve a bare ticker to an EXCHANGE:TICKER the scanner accepts (the `tv bars`
 * CLI echoes back the bare symbol, so the chart can't supply this). Returns null
 * on any failure; callers degrade gracefully. Best-effort: prefers an exact
 * symbol match, falls back to the top result.
 */
export async function resolveExchange(
  symbol: string,
  timeoutMs: number,
): Promise<string | null> {
  const url = `${SEARCH_URL}?text=${encodeURIComponent(symbol)}`;
  let res: Response;
  try {
    res = await fetch(url, { headers: TV_HEADERS, signal: AbortSignal.timeout(timeoutMs) });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  let arr: unknown;
  try {
    arr = await res.json();
  } catch {
    return null;
  }
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const want = symbol.toUpperCase();
  const rows = arr as Record<string, unknown>[];
  const pick = rows.find((e) => str(e.symbol)?.toUpperCase() === want) ?? rows[0];
  const exchange = str(pick.prefix) ?? str(pick.exchange);
  const sym = str(pick.symbol) ?? symbol;
  return exchange ? `${exchange}:${sym}`.toUpperCase() : null;
}

/**
 * Pull the business description ("what the company does") out of a TradingView
 * symbol page. The scanner has no such field, but the public page embeds it in a
 * `<script type="application/ld+json">` schema.org `FinancialProduct` block, so
 * we parse that rather than the volatile hashed React markup. Returns null when
 * no such block carries a description (e.g. many non-US listings).
 */
const LD_JSON_RE = /<script type="application\/ld\+json">([\s\S]*?)<\/script>/g;

export function parseDescriptionHtml(html: string): string | null {
  for (const m of html.matchAll(LD_JSON_RE)) {
    let obj: unknown;
    try {
      obj = JSON.parse(m[1]);
    } catch {
      continue;
    }
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const o = obj as Record<string, unknown>;
      if (o['@type'] === 'FinancialProduct') {
        const desc = str(o.description);
        if (desc) return desc;
      }
    }
  }
  return null;
}

/**
 * Fetch the symbol page and extract its business description. Best-effort and
 * never throws — any failure (page down, no JSON-LD, timeout) returns null and
 * the caller simply renders without a description.
 */
export async function fetchDescription(
  qualified: string,
  timeoutMs: number,
): Promise<string | null> {
  // EXCHANGE:TICKER -> the page slug EXCHANGE-TICKER (e.g. "NASDAQ-AAPL").
  const slug = qualified.replace(':', '-');
  const url = `${SYMBOL_PAGE_URL}${encodeURIComponent(slug)}/`;
  try {
    const res = await fetch(url, {
      headers: { ...TV_HEADERS, Accept: 'text/html' },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return null;
    return parseDescriptionHtml(await res.text());
  } catch {
    return null;
  }
}

/** Fetch the snapshot fundamentals from the scanner `/symbol` endpoint. */
async function fetchScannerSnapshot(
  qualified: string,
  timeoutMs: number,
): Promise<FundamentalsResponse> {
  const url =
    `${SCANNER_URL}?symbol=${encodeURIComponent(qualified)}` +
    `&fields=${FIELDS.map(encodeURIComponent).join(',')}&no_404=true`;
  try {
    const res = await fetch(url, { headers: TV_HEADERS, signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return errorFundamentals(qualified, `scanner returned HTTP ${res.status}`);
    const json = (await res.json()) as unknown;
    return parseFundamentals(json, qualified, 'live');
  } catch (e) {
    const msg = (e as Error).name === 'TimeoutError'
      ? `scanner request timed out after ${timeoutMs}ms`
      : `fundamentals fetch failed: ${(e as Error).message}`;
    return errorFundamentals(qualified, msg);
  }
}

export async function fetchFundamentals(
  symbol: string,
  opts: { timeoutMs?: number } = {},
): Promise<FundamentalsResponse> {
  const fixture = process.env.TRADING_UI_FUNDAMENTALS_FIXTURE;
  if (fixture) return readFixture(fixture, symbol);

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  // The scanner only answers for EXCHANGE:TICKER; resolve a bare ticker first.
  let qualified = symbol;
  if (!symbol.includes(':')) {
    const resolved = await resolveExchange(symbol, timeoutMs);
    if (!resolved) return errorFundamentals(symbol, `could not resolve exchange for ${symbol}`);
    qualified = resolved;
  }

  // The metrics (scanner) and the description (symbol page) are independent
  // endpoints — fetch both at once so the description never adds latency.
  const [snapshot, description] = await Promise.all([
    fetchScannerSnapshot(qualified, timeoutMs),
    fetchDescription(qualified, timeoutMs),
  ]);
  if (snapshot.ok && snapshot.data && description && !snapshot.data.description) {
    snapshot.data.description = description;
  }
  return snapshot;
}
