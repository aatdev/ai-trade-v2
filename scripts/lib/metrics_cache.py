#!/usr/bin/env python3
"""
Reader for the per-ticker metrics cache written by scripts/collect_russell.js.

Skills use this as a fast path: a fresh metrics snapshot serves quote,
fundamentals, indicators and price stats without driving the live chart. Once a
snapshot is older than STALE_HOURS (default 3h, override with
METRICS_STALE_HOURS) it is considered stale and callers fall back to a live
fetch.

Source order: OpenSearch first (indices my_tw_metrics + my_tw_candles_1d, see
scripts/lib/opensearch.js), then the local file (state/metrics/TICKER/) when
OpenSearch is unreachable or has no document. Disable the OpenSearch path with
METRICS_OPENSEARCH=0; override the URL with OPENSEARCH_URL.

The two `cached_*` helpers below return data already shaped like the FMP/scanner
payloads the screeners' tv_client expects, so wiring is a one-line cache check.
"""

import http.client
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# The local-file fallback lives under the vendored bridge's state/metrics. The
# OpenSearch backend below is the primary source and is path-independent, so this
# only matters when OpenSearch is unreachable. Override the repo with TV_MCP_REPO;
# defaults to the in-repo vendored copy at <repo>/vendor/tradingview-mcp.
_REPO_ROOT = os.environ.get("TV_MCP_REPO") or str(
    Path(__file__).resolve().parents[2] / "vendor" / "tradingview-mcp"
)
METRICS_DIR = os.path.join(_REPO_ROOT, "state", "metrics")

# Freshness window for the cache fast path. A snapshot is only trusted if it was
# collected within the last STALE_HOURS; anything older falls through to a live
# chart fetch. Expressed in hours (override with METRICS_STALE_HOURS) and kept as
# fractional days in STALE_DAYS because age_days()/is_fresh() work in days.
try:
    STALE_HOURS = float(os.environ.get("METRICS_STALE_HOURS", "3"))
except ValueError:
    STALE_HOURS = 3.0
STALE_DAYS = STALE_HOURS / 24.0

# ─── OpenSearch backend ──────────────────────────────────────────────────────
# Mirrors scripts/lib/opensearch.js: read OpenSearch first, fall back to files.

OS_BASE = os.environ.get("OPENSEARCH_URL", "http://tw.spitch-dev.ai:9200").rstrip("/")
OS_ENABLED = os.environ.get("METRICS_OPENSEARCH", "1") != "0"
IDX_METRICS = "my_tw_metrics"
IDX_CANDLES = "my_tw_candles_1d"
_OS_TIMEOUT = 4.0
_MAX_CANDLE_HITS = 2000

# Process-wide circuit breaker: once a request fails to connect we stop trying so
# a 2000-ticker scan doesn't pay the timeout on every ticker.
_os_down = False

# Persistent keep-alive connection. The TCP handshake to the OpenSearch host can
# cost ~1s; with urlopen() opening (and closing) a socket per request, EVERY
# cached_* lookup paid that handshake — the dominant cost of a cache-backed scan.
# Reusing one connection pays the handshake once, then each request is ~10ms.
# Guarded by a lock so concurrent callers don't interleave on the shared socket.
_conn: Optional[http.client.HTTPConnection] = None
_conn_lock = threading.Lock()


def _os_active() -> bool:
    return OS_ENABLED and not _os_down


def _make_conn() -> http.client.HTTPConnection:
    """Open a fresh keep-alive connection to the OpenSearch host."""
    u = urlparse(OS_BASE)
    host = u.hostname or "localhost"
    if u.scheme == "https":
        port = u.port or 443
        return http.client.HTTPSConnection(host, port, timeout=_OS_TIMEOUT)
    port = u.port or 80
    return http.client.HTTPConnection(host, port, timeout=_OS_TIMEOUT)


def _reset_backend_state() -> None:
    """Drop the persistent connection, reset the breaker and the read memo.

    Used by tests and available to long-running callers that want to force a
    fresh connection (e.g. after a known network blip)."""
    global _conn, _os_down
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except OSError:
                pass
        _conn = None
    _os_down = False
    _metrics_memo.clear()


def _os_request(method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    """Low-level OpenSearch request over a persistent keep-alive connection.

    Returns parsed JSON, or None on failure. A single transport error (e.g. the
    server closed an idle keep-alive socket) reconnects and retries once; a
    second failure trips the process-wide breaker. JSON/decode errors return
    None without reconnecting or tripping the breaker."""
    global _os_down, _conn
    if not _os_active():
        return None
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}

    with _conn_lock:
        raw: Optional[bytes] = None
        status: Optional[int] = None
        for attempt in range(2):
            try:
                if _conn is None:
                    _conn = _make_conn()
                _conn.request(method, path, body=data, headers=headers)
                resp = _conn.getresponse()
                raw = resp.read()
                status = resp.status
                break
            except (http.client.HTTPException, OSError):
                # Socket may be stale (server dropped keep-alive) — discard it
                # and retry on a fresh connection. Trip the breaker only if the
                # reconnect also fails, so one dead socket ≠ "OpenSearch down".
                if _conn is not None:
                    try:
                        _conn.close()
                    except OSError:
                        pass
                _conn = None
                if attempt == 1:
                    _os_down = True
                    return None

        if status is None:
            return None
        # 404 (missing doc/index) carries a normal body; other 4xx/5xx are errors.
        if status >= 400 and status != 404:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


def _safe_token(ticker: str) -> str:
    """OpenSearch _id token, matching opensearch.js safeToken() / _safe_name()."""
    return _safe_name(ticker)


def os_read_metrics(ticker: str) -> Optional[dict]:
    """Metrics snapshot for a ticker from OpenSearch, or None."""
    r = _os_request("GET", f"/{IDX_METRICS}/_doc/{_safe_token(ticker)}")
    return r.get("_source") if r and r.get("found") else None


def os_read_ohlcv(ticker: str) -> Optional[dict]:
    """Reconstruct the ohlcv.json-shaped doc from candle docs, or None.

    Bars are OLDEST-FIRST, mirroring the local file; collected_at is the latest
    across candles so is_fresh() works the same as on the file doc."""
    r = _os_request(
        "POST",
        f"/{IDX_CANDLES}/_search",
        {
            "size": _MAX_CANDLE_HITS,
            "query": {"term": {"ticker": ticker}},
            "sort": [{"time": "asc"}],
            "_source": ["time", "date", "open", "high", "low", "close", "volume", "collected_at"],
        },
    )
    hits = (r or {}).get("hits", {}).get("hits")
    if not hits:
        return None
    collected_at = None
    bars = []
    for h in hits:
        s = h.get("_source", {})
        ca = s.get("collected_at")
        if ca and (collected_at is None or ca > collected_at):
            collected_at = ca
        # Stored as ms (legacy schema) → UNIX seconds to mirror ohlcv.json.
        t = s.get("time")
        bars.append({
            "time": int(t) // 1000 if t is not None else None,
            "date": s.get("date"),
            "open": s.get("open"),
            "high": s.get("high"),
            "low": s.get("low"),
            "close": s.get("close"),
            "volume": s.get("volume") or 0,
        })
    last = bars[-1] if bars else None
    return {
        "ticker": ticker,
        "collected_at": collected_at,
        "as_of_date": last["date"] if last else None,
        "count": len(bars),
        "bars": bars,
    }


def os_list_tickers() -> Optional[list]:
    """Distinct tickers that have a metrics doc, or None on failure."""
    r = _os_request(
        "POST",
        f"/{IDX_METRICS}/_search",
        {"size": 10000, "_source": ["ticker"], "query": {"match_all": {}}},
    )
    hits = (r or {}).get("hits", {}).get("hits")
    if hits is None:
        return None
    return [h.get("_source", {}).get("ticker") for h in hits if h.get("_source", {}).get("ticker")]


def _safe_name(ticker: str) -> str:
    """Filesystem-safe token, matching metrics_store.js safeToken()."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in ticker)


def ticker_dir(ticker: str) -> str:
    return os.path.join(METRICS_DIR, _safe_name(ticker))


def metrics_path(ticker: str) -> str:
    return os.path.join(ticker_dir(ticker), "metrics.json")


def ohlcv_path(ticker: str) -> str:
    return os.path.join(ticker_dir(ticker), "ohlcv.json")


def _read_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# Per-ticker memo for the metrics doc. cached_quote / cached_fundamentals /
# cached_indicators each call read_metrics for the SAME ticker (e.g.
# get_company_profile needs both the quote and fundamentals projection), so
# without memoization one profile lookup fetched the same `_doc` 2-3 times.
# Snapshots are a daily collection, so caching for the process lifetime is safe
# and matches the client-level cache that already pins shaped results per symbol.
_metrics_memo: dict = {}


def read_metrics(ticker: str) -> Optional[dict]:
    """Metrics snapshot — OpenSearch first, then the local file. None if neither.

    Memoizes a found doc per ticker so repeat lookups within a run don't re-hit
    the backend; misses are NOT memoized so a transient failure stays retryable.
    """
    if ticker in _metrics_memo:
        return _metrics_memo[ticker]
    m = os_read_metrics(ticker)
    if m is None:
        m = _read_json(metrics_path(ticker))
    if m is not None:
        _metrics_memo[ticker] = m
    return m


def read_ohlcv(ticker: str) -> Optional[dict]:
    """Raw OHLCV doc { ticker, collected_at, as_of_date, count, bars } —
    OpenSearch first, then the local file. None if neither."""
    doc = os_read_ohlcv(ticker)
    if doc is not None:
        return doc
    return _read_json(ohlcv_path(ticker))


def list_tickers() -> list:
    """Tickers known to the cache — from OpenSearch, falling back to the local
    state/metrics/ directory listing when OpenSearch is unreachable."""
    osl = os_list_tickers()
    if osl is not None:
        return osl
    if os.path.isdir(METRICS_DIR):
        return [
            name
            for name in os.listdir(METRICS_DIR)
            if os.path.exists(os.path.join(METRICS_DIR, name, "ohlcv.json"))
        ]
    return []


def age_days(metrics: Optional[dict]) -> float:
    """Age of the snapshot in days; inf if missing/invalid."""
    if not metrics or "collected_at" not in metrics:
        return float("inf")
    try:
        ts = datetime.fromisoformat(metrics["collected_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0


def is_fresh(metrics: Optional[dict], stale_days: float = STALE_DAYS) -> bool:
    """True when the snapshot exists and is younger than `stale_days`."""
    return age_days(metrics) <= stale_days


def fresh_metrics(ticker: str, stale_days: float = STALE_DAYS) -> Optional[dict]:
    """Return the snapshot only if present AND fresh, else None."""
    m = read_metrics(ticker)
    return m if (m and is_fresh(m, stale_days)) else None


# ─── FMP-shaped projections (drop-in for tv_client) ──────────────────────────


def cached_quote(ticker: str, stale_days: float = STALE_DAYS) -> Optional[dict]:
    """FMP-shaped quote dict from a fresh snapshot, else None.

    Fields match tv_client.get_quote: price, yearHigh, yearLow, avgVolume,
    volume, marketCap, symbol, name. marketCap comes from cached fundamentals
    (unavailable from bare chart bars), so the cache is strictly richer here.
    """
    m = fresh_metrics(ticker, stale_days)
    if not m:
        return None
    price = m.get("price") or {}
    quote = m.get("quote") or {}
    fund = m.get("fundamentals") or {}
    market_cap = (fund.get("valuation") or {}).get("market_cap_basic", 0)
    return {
        "symbol": ticker,
        "name": m.get("name", ticker),
        "price": price.get("last_close") or quote.get("last") or 0,
        "yearHigh": price.get("year_high") or 0,
        "yearLow": price.get("year_low") or 0,
        "avgVolume": price.get("avg_volume_50d") or 0,
        "volume": quote.get("volume") or 0,
        "marketCap": market_cap or 0,
    }


def cached_fundamentals(ticker: str, stale_days: float = STALE_DAYS) -> Optional[dict]:
    """Scanner-shaped fundamentals payload from a fresh snapshot, else None.

    Mirrors the `tv fundamentals --history` CLI result the canslim tv_client
    consumes (success flag + name + field groups + history)."""
    m = fresh_metrics(ticker, stale_days)
    if not m or not m.get("fundamentals"):
        return None
    out = {"success": True, "symbol": ticker, "name": m.get("name", ticker)}
    out.update(m["fundamentals"])
    return out


def cached_indicators(ticker: str, stale_days: float = STALE_DAYS) -> Optional[dict]:
    """Latest indicator block (ema/sma/rsi/macd/stoch/bb/atr/returns), else None."""
    m = fresh_metrics(ticker, stale_days)
    return m.get("indicators") if m else None


def cached_ohlcv(
    ticker: str, min_bars: int = 1, stale_days: float = STALE_DAYS
) -> Optional[list]:
    """FMP-shaped daily bars from a fresh OHLCV file, NEWEST-FIRST, else None.

    Each bar: {date, open, high, low, close, adjClose, volume} — matches what
    tv_client.get_historical_prices returns, so it's a drop-in for the live
    chart pull. Returns None when the file is missing, stale, or shorter than
    `min_bars` (so the caller falls back to a live fetch that may reach further
    back, e.g. a recent IPO with sparse cache history)."""
    doc = read_ohlcv(ticker)
    if not doc or not doc.get("bars"):
        return None
    if not is_fresh(doc, stale_days):  # ohlcv.json carries its own collected_at
        return None
    bars = doc["bars"]  # stored OLDEST-FIRST
    if len(bars) < min_bars:
        return None
    out = []
    for b in reversed(bars):  # → NEWEST-FIRST
        close = b.get("close", 0)
        out.append(
            {
                "date": b.get("date", ""),
                "open": b.get("open", 0),
                "high": b.get("high", 0),
                "low": b.get("low", 0),
                "close": close,
                "adjClose": close,
                "volume": b.get("volume", 0) or 0,
            }
        )
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python metrics_cache.py <TICKER>", file=sys.stderr)
        sys.exit(2)
    t = sys.argv[1]
    snap = read_metrics(t)
    print(
        json.dumps(
            {
                "found": snap is not None,
                "fresh": is_fresh(snap),
                "age_hours": None if snap is None else round(age_days(snap) * 24, 2),
                "stale_hours": STALE_HOURS,
                "metrics": snap,
            },
            indent=2,
        )
    )
    sys.exit(0 if is_fresh(snap) else 3)
