# TradingView News Flow Integration

How to read the **TradingView News Flow** tab as a primary, structured news source for market-news analysis, including how to apply news filters. This is **Source A** in the Step 1 collection workflow (WebSearch/WebFetch is Source B). It is an optional enhancement: when the TradingView MCP is unavailable, skip it and rely on Source B.

## What the News Flow Tab Is

The News Flow is TradingView's real-time, aggregated headline feed. Each item ("card") carries:
- A headline (full title + truncated display title)
- A provider/source (Reuters, Dow Jones, GuruFocus, Stocktwits, PR Newswire, Quartr, ACCESS Newswire/Refinitiv, etc.)
- A precise UTC publish timestamp
- A linked story id (`/news/<urn>`)

It surfaces breaking items before web-search engines index them, is already de-duplicated by TradingView, and can be scoped with native filters. That makes it a strong backbone for "what actually crossed the wire and when," which WebSearch then enriches with market-reaction context (price moves, follow-through, analyst framing).

It appears in two places in TradingView Desktop:
1. **Dedicated News Flow tab/page** — URL pattern `https://www.tradingview.com/news-flow/<id>` (title like "News Flow: Market News Customized"). This is a full feed with its own filter pills. Prefer this for broad market-news collection.
2. **Chart right-sidebar news widget** — scoped to the active chart symbol. Prefer this for single-ticker news (set the chart symbol, then read).

## Prerequisites

- TradingView Desktop running with Chrome DevTools Protocol exposed on `localhost:9222` (the same session the rest of the TradingView skills use).
- TradingView MCP connected, exposing `mcp__tradingview__*` tools (`ui_evaluate`, `ui_scroll`, `ui_keyboard`, `ui_find_element`, `ui_click`).
- A News Flow tab open (dedicated page preferred). If none is open, fall back to the chart symbol's news widget or to Source B.

## Reading the Feed (verified selectors)

Class names on TradingView are content-hashed and **change every build** (e.g. `card-fWZnz9qv`, `provider-fWZnz9qv`). Never select on exact hashed classes. The stable, QA-stable hooks are the `data-qa-id` attributes and the `relative-time` custom element.

| Field | Stable selector | Notes |
|-------|-----------------|-------|
| Card (container) | `[data-qa-id="news-headline-card"]` | The `<article>`; its `closest('a')` holds the link + id |
| Headline | `[data-qa-id="news-headline-title"]` | Full text in `data-overflow-tooltip-text`; visible text may be truncated |
| Provider | `[class*="provider-"]` | Prefix match on the hashed class |
| Timestamp | `relative-time` (attr `event-time`) | RFC-1123 UTC string; the `title` attr gives a human UTC label |
| Story id | `a[href^="/news/"]` → `data-id` | URN, e.g. `tag:reuters.com,2026-06-08:newsml_...` |
| Link | `a[href^="/news/"]` → `href` | Prefix with `https://www.tradingview.com` |

Filter controls (dedicated News Flow page only):

| Control | Stable selector | Purpose |
|---------|-----------------|---------|
| Show/hide filters | `[data-qa-id="hide-filters"]` | Toggles the filter pill row |
| Instrument filter | `[data-qa-id="filter-pill-symbol"]` | Scope to a ticker/instrument |
| Country/market filter | `[data-qa-id="filter-pill-market_country"]` | Scope to a market/country |
| Column header | `[data-qa-id="news-headlines-table-head"]` | Time / Instrument / Headline / Provider |

### Scrape expression

Pass this to `mcp__tradingview__ui_evaluate` (the tool wraps it and returns the JSON value):

```javascript
(function () {
  const cards = Array.from(document.querySelectorAll('[data-qa-id="news-headline-card"]'));
  return cards.map((art) => {
    const a = art.closest('a');
    const titleEl = art.querySelector('[data-qa-id="news-headline-title"]');
    const provEl = art.querySelector('[class*="provider-"]');
    const timeEl = art.querySelector('relative-time');
    return {
      id: a ? a.getAttribute('data-id') : null,
      title: titleEl ? (titleEl.getAttribute('data-overflow-tooltip-text') || titleEl.textContent.trim()) : null,
      provider: provEl ? provEl.textContent.trim() : null,
      published_utc: timeEl ? timeEl.getAttribute('event-time') : null,
      link: a ? ('https://www.tradingview.com' + a.getAttribute('href')) : null,
    };
  }).filter((x) => x.title);
})()
```

Each row is one headline. Example output:

```json
{
  "id": "gurufocus:ce1fbe302094b:0",
  "title": "Intel Leads Chip Rally on AI Foundry Hopes",
  "provider": "GuruFocus",
  "published_utc": "Mon, 08 Jun 2026 19:19:42 GMT",
  "link": "https://www.tradingview.com/news/gurufocus:ce1fbe302094b:0-intel-leads-chip-rally-on-ai-foundry-hopes/"
}
```

To read a full story body, fetch the `link` with WebFetch, or open it in TradingView and scrape the article view.

### Reaching the 10-day window (lazy load)

The feed renders ~30 cards initially and lazy-loads more on scroll. To cover the full analysis window:

1. Scrape once and note the oldest `published_utc`.
2. Scroll the feed: `mcp__tradingview__ui_scroll` `{direction: "down", amount: 1200}` (or `mcp__tradingview__ui_keyboard` `{key: "End"}`).
3. Re-scrape. Repeat until the oldest `published_utc` predates the target start date, or until ids/count stop changing (end of feed).
4. De-duplicate accumulated rows by `id`.

Keep total reads bounded (a handful of scrolls is usually enough for 10 days of major headlines). Do not loop indefinitely.

## Applying News Filters

"Use filters if necessary" — pick the lightest sufficient method:

1. **Already-curated feed (default):** the dedicated News Flow page is typically a saved, customized feed ("Market News Customized"). For broad market analysis, scrape it as-is.
2. **Symbol scope via the chart widget (most reliable):** set the chart symbol (`mcp__tradingview__chart_set_symbol`) and read its right-sidebar news widget — TradingView scopes news to that instrument automatically. Best for single-ticker requests.
3. **Native filter pills:** click `[data-qa-id="hide-filters"]` to reveal pills, then `[data-qa-id="filter-pill-symbol"]` (Instrument) or `[data-qa-id="filter-pill-market_country"]` (Country) and select a value via the popup. Use only when the curated feed is too broad and the chart-widget route is unavailable. Verify the result by re-scraping; popup internals are less stable than the pill anchors.
4. **Post-scrape filtering (always available):** filter the scraped JSON in-analysis by `provider`, `published_utc` recency, ticker mentioned in `title`, or keyword. This is the most robust filter for "only Fed news," "only the last 48h," "only Reuters/Dow Jones," etc.

Prefer post-scrape filtering and the chart-widget route over driving filter-popup UI, which is the most fragile path.

## Provider → Credibility Tier

Map News Flow providers onto the tiers in `trusted_news_sources.md` so impact scoring weights sources correctly:

| News Flow provider | Tier (per trusted_news_sources.md) |
|--------------------|------------------------------------|
| Reuters, Dow Jones, Refinitiv | Tier 1 (major financial news) |
| MarketWatch, Barron's, CNBC | Tier 2 (specialized / real-time) |
| GuruFocus, Quartr, Benzinga, Zacks | Tier 3/4 (analysis/aggregation — corroborate) |
| Stocktwits, generic PR Newswire / ACCESS Newswire / Business Wire | Lower-trust / promotional — treat headlines as leads, verify before scoring |

Press-release wires (PR Newswire, Business Wire, GlobeNewswire, ACCESS Newswire) carry company-issued copy: useful for *what was announced*, not for *independent assessment*. Corroborate market impact against a Tier 1/2 source before assigning a high impact score.

## De-duplication vs WebSearch (Source B)

- De-duplicate by **event**, not by headline string — the same event recurs across providers and wordings. Match on entity + topic + date.
- News Flow contributes timestamps, breaking-first ordering, and provider attribution. WebSearch/WebFetch contributes market-reaction context the raw feed lacks.
- On factual disagreement, prefer the higher-tier source and note the discrepancy in the report.

## Failure Modes & Fallback

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `ui_evaluate` errors / MCP tool missing | TradingView MCP not connected | Skip Source A; use Source B |
| Scrape returns `[]` | No News Flow tab open, or feed not loaded | Open the News Flow tab or chart news widget; if still empty, fall back |
| Selectors return null fields | TradingView changed `data-qa-id` hooks | Re-probe with `mcp__tradingview__ui_find_element` for `news-headline`; report drift; fall back |
| Only ~30 items, need more | Lazy load not triggered | Scroll and re-scrape (see "Reaching the 10-day window") |

Source A is **never** a hard dependency. If anything above fails, proceed with WebSearch/WebFetch and note in the report that News Flow was unavailable.

## Compliance

The TradingView MCP / `tv` data layer is an unofficial tool not affiliated with TradingView Inc. Reading the News Flow you already have access to in your own Desktop session is for personal analysis; ensure usage complies with TradingView's Terms of Use. Do not redistribute scraped feeds.
