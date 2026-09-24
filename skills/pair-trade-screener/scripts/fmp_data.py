#!/usr/bin/env python3
"""FMP data helpers for the pair-trade screener (no heavy deps, unit-tested).

FMP retired the ``/api/v3`` endpoints for keys created after 2025-08-31
("Legacy Endpoint" 403) and the old stable path ``historical-price-full``
returns 404. Current endpoints:

* prices: ``/stable/historical-price-eod/dividend-adjusted?symbol=X`` — a flat
  most-recent-first list with ``adjClose`` (normalized here to the v3-style
  ``{"symbol", "historical": [...]}`` dict the analysis code expects);
* sector universe: ``/stable/company-screener`` (paid plans). On a plan where
  it is restricted, the universe comes from the public TradingView scanner
  (no key), whose sector taxonomy is mapped from the GICS name the user gives.

Legacy v3 keys still work through the v3 fallback.
"""

from __future__ import annotations

import requests

FMP = "https://financialmodelingprep.com"
STABLE_ADJ_EOD = f"{FMP}/stable/historical-price-eod/dividend-adjusted"
LEGACY_HIST = f"{FMP}/api/v3/historical-price-full"
STABLE_SCREENER = f"{FMP}/stable/company-screener"
TV_SCAN = "https://scanner.tradingview.com/america/scan"

# GICS sector (what users type) -> TradingView scanner sectors.
GICS_TO_TV_SECTORS = {
    "technology": ["Electronic Technology", "Technology Services"],
    "information technology": ["Electronic Technology", "Technology Services"],
    "healthcare": ["Health Technology", "Health Services"],
    "health care": ["Health Technology", "Health Services"],
    "financial services": ["Finance"],
    "financials": ["Finance"],
    "financial": ["Finance"],
    "consumer cyclical": ["Retail Trade", "Consumer Services", "Consumer Durables"],
    "consumer discretionary": ["Retail Trade", "Consumer Services", "Consumer Durables"],
    "consumer defensive": ["Consumer Non-Durables"],
    "consumer staples": ["Consumer Non-Durables"],
    "industrials": [
        "Producer Manufacturing",
        "Industrial Services",
        "Transportation",
        "Commercial Services",
        "Distribution Services",
    ],
    "energy": ["Energy Minerals"],
    "utilities": ["Utilities"],
    "basic materials": ["Non-Energy Minerals", "Process Industries"],
    "materials": ["Non-Energy Minerals", "Process Industries"],
    "communication services": ["Communications"],
    "communications": ["Communications"],
    "real estate": ["Finance"],
}

_BREAKER_THRESHOLD = 3
_endpoint_failures: dict[str, int] = {}


def _normalize_flat(data, symbol: str) -> dict | None:
    """Flat stable list -> {"symbol", "historical": [{date, adjClose, close, volume}]}."""
    if not isinstance(data, list) or not data:
        return None
    hist = []
    for row in data:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        adj = row.get("adjClose", row.get("close"))
        hist.append(
            {
                "date": row["date"],
                "adjClose": adj,
                "close": row.get("close", adj),
                "volume": row.get("volume"),
            }
        )
    return {"symbol": symbol, "historical": hist} if hist else None


def fetch_raw_historical(symbol: str, api_key: str, params: dict | None = None, *, session=None):
    """Dividend-adjusted daily history (most-recent-first) or None."""
    http = session or requests
    attempts = [
        (STABLE_ADJ_EOD, {**(params or {}), "symbol": symbol}, True),
        (f"{LEGACY_HIST}/{symbol}", dict(params or {}), False),
    ]
    for url, req_params, is_stable in attempts:
        base = url.split("?")[0] if is_stable else LEGACY_HIST
        if _endpoint_failures.get(base, 0) >= _BREAKER_THRESHOLD and session is None:
            continue
        try:
            resp = http.get(url, params=req_params, headers={"apikey": api_key}, timeout=30)
        except requests.exceptions.RequestException:
            _endpoint_failures[base] = _endpoint_failures.get(base, 0) + 1
            continue
        if resp.status_code != 200:
            _endpoint_failures[base] = _endpoint_failures.get(base, 0) + 1
            continue
        data = resp.json()
        if is_stable:
            out = _normalize_flat(data, symbol)
        elif isinstance(data, dict) and "historical" in data:
            out = data
        else:
            out = None
        if out:
            _endpoint_failures[base] = 0
            return out
        _endpoint_failures[base] = _endpoint_failures.get(base, 0) + 1
    return None


def _tv_sector_stocks(sector: str, min_market_cap: float, *, session=None) -> list[dict]:
    tv_sectors = GICS_TO_TV_SECTORS.get(str(sector).strip().lower())
    if not tv_sectors:
        return []
    http = session or requests
    payload = {
        "filter": [
            {"left": "sector", "operation": "in_range", "right": tv_sectors},
            {"left": "market_cap_basic", "operation": "greater", "right": min_market_cap},
            {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "columns": ["name", "description", "market_cap_basic", "sector", "exchange"],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 1000],
    }
    try:
        resp = http.post(TV_SCAN, json=payload, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    except requests.exceptions.RequestException:
        return []
    if resp.status_code != 200:
        return []
    stocks = []
    for item in (resp.json() or {}).get("data") or []:
        d = item.get("d") or []
        if len(d) < 5:
            continue
        stocks.append(
            {
                "symbol": d[0],
                "name": d[1] or d[0],
                "marketCap": d[2] or 0,
                "sector": sector,
                "exchange": d[4] or "",
            }
        )
    return stocks


def fetch_sector_stocks(
    sector: str, api_key: str, min_market_cap: float = 2_000_000_000, *, session=None
) -> list[dict]:
    """Sector universe [{symbol, name, marketCap, sector, exchange}] (may be [])."""
    http = session or requests
    try:
        resp = http.get(
            STABLE_SCREENER,
            params={"sector": sector, "marketCapMoreThan": min_market_cap, "limit": 1000},
            headers={"apikey": api_key},
            timeout=30,
        )
        data = resp.json() if resp.status_code == 200 else None
    except (requests.exceptions.RequestException, ValueError):
        data = None
    if isinstance(data, list) and data:
        return [
            {
                "symbol": item["symbol"],
                "name": item.get("companyName", ""),
                "marketCap": item.get("marketCap", 0),
                "sector": item.get("sector", sector),
                "exchange": item.get("exchangeShortName", item.get("exchange", "")),
            }
            for item in data
            if item.get("symbol") and item.get("isActivelyTrading", True)
        ]
    # Restricted on this FMP plan (or empty): public TradingView scanner, no key.
    return _tv_sector_stocks(sector, min_market_cap, session=session)
