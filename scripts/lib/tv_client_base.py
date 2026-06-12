#!/usr/bin/env python3
"""
TVClient (base) — drop-in replacement for FMPClient that sources data from a live
TradingView Desktop chart (Chrome DevTools Protocol on :9222) via the globally
linked `tv` CLI, instead of the FMP REST API.

Why this exists: the FMP free tier gates most symbols at the API level, so a real
S&P 500 / Russell scan is impossible. TradingView serves daily bars and scanner
fundamentals for any symbol with no per-symbol or per-day request cap, so the
screeners route their whole data layer through it and need NO FMP key.

This base owns everything skills share:
  - PRICE layer — `tv` CLI plumbing, bar fetching, the metrics-cache fast path,
    and FMP-shaped get_quote / get_historical_prices / get_batch_*. Live
    fetches go through the single-process `tv bars` command (the CLI switches
    the chart and waits for readiness in-process, one spawn per symbol — or
    per BATCH_CHUNK symbols for batch calls — instead of 3-4 spawns plus
    blind settle sleeps); a legacy switch+poll+ohlcv path remains for CLI
    checkouts that predate `tv bars`.
  - FUNDAMENTAL layer — get_profile / get_income_statement / get_company_profile(s)
    from the TradingView scanner (`tv fundamentals`), no FMP quota.
  - MACRO helpers — get_vix_term_structure, get_treasury_rates (from TVC index
    symbols) and get_earnings_calendar (scanner.tradingview.com, via the source
    repo's tv_earnings_calendar.mjs).

Each skill keeps a thin `tv_client.py` subclass that only configures the knobs
(quote shape, index remaps, cache-disable env). There is intentionally no FMP
fallback here — the whole point is to drop FMP.

Environment:
  - TV_MCP_REPO : absolute path to the TradingView bridge checkout. Used to
                  locate scripts/tv_earnings_calendar.mjs and the optional
                  state/ cache, and as the `tv` CLI (node <repo>/src/cli/index.js)
                  when the global `tv` is not on PATH. Defaults to the in-repo
                  vendored copy at <repo>/vendor/tradingview-mcp.
  - TV_CLI      : explicit path to the `tv` CLI entry (overrides discovery).

Data shape contract (matches FMPClient):
  - get_historical_prices -> {"symbol", "historical": [bar, ...]} NEWEST FIRST
  - each bar: {date, open, high, low, close, adjClose, volume}
  - get_quote -> {price, yearHigh, yearLow, avgVolume, volume, marketCap, name}
                 (or [that dict] when quote_as_list=True)
TradingView returns bars OLDEST first, so we reverse them.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Repo root (.../claude-trading-skills), resolved from this file's location so
# the vendored TradingView bridge is found regardless of where a skill imports
# the module from (skills/*/scripts/* symlink to scripts/lib/ → resolve()
# follows the symlink back here).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# The TradingView bridge (node CLI, helper scripts, optional state cache) is
# vendored in-repo under vendor/tradingview-mcp so the repository is
# self-contained. Override with TV_MCP_REPO to point at an external checkout.
TV_MCP_REPO = os.environ.get("TV_MCP_REPO") or str(_REPO_ROOT / "vendor" / "tradingview-mcp")
STATE_DIR = os.path.join(TV_MCP_REPO, "state")
EARNINGS_MJS = os.path.join(TV_MCP_REPO, "scripts", "tv_earnings_calendar.mjs")

# S&P 500 constituents committed in-repo. The vendored copy lives under the
# gitignored state/ tree, so this committed snapshot is the default source and
# keeps the screeners working straight after a clone; falls back to STATE_DIR.
SP500_CSV = str(_REPO_ROOT / "scripts" / "lib" / "data" / "sp500.csv")

# metrics_cache.py is dropped in alongside this module (sibling import).
try:
    import metrics_cache  # noqa: E402
except ImportError:
    metrics_cache = None

# Defaults. 400 daily bars ~= 18 months — comfortably covers a 200-day SMA
# (+slope) and the 1-year (252d) relative-strength window every screener needs.
BARS = 400
# Ceiling (seconds) for how long to wait after switching symbol before giving
# up on the chart becoming ready. Rather than blindly sleeping this whole time,
# the client polls the chart's current symbol every POLL_INTERVAL and proceeds
# the instant the switch registers (typically ~0.2s on a warm chart), only
# spending the full SETTLE on a cold/slow load. A cold chart that times out is
# retried with a longer backoff.
SETTLE = 2.5
# How often to poll `tv symbol` while waiting for a symbol switch to register.
POLL_INTERVAL = 0.1
# Trend/RS calculators need a year of history; a stock with fewer daily bars
# (recent IPO/spin-off) can't be evaluated and is skipped cleanly.
MIN_BARS = 200
# How many symbols to pull per `tv bars` invocation in batch prefetch. One
# process/CDP connection serves the whole chunk (~0.5s/symbol in-process), so
# the ~1s spawn overhead is amortized chunk-wide. Sized so a worst-case chunk
# (every symbol burning the CLI's in-process readiness timeout) still finishes
# within the proportional subprocess timeout below.
BATCH_CHUNK = 20
# Per-symbol worst case inside `tv bars` is its ~8s readiness timeout; base
# covers process spawn + CDP connect retries + JSON payload transfer.
_BARS_TIMEOUT_BASE = 30
_BARS_TIMEOUT_PER_SYMBOL = 8


def _bars_call_timeout(n_symbols: int) -> int:
    return _BARS_TIMEOUT_BASE + _BARS_TIMEOUT_PER_SYMBOL * n_symbols


class ApiCallBudgetExceeded(Exception):
    """Parity with FMPClient's budget exception. TradingView has no per-call
    quota, so this is never raised here — it exists only so skills that do
    `from tv_client import ApiCallBudgetExceeded` keep importing and their
    `except ApiCallBudgetExceeded` blocks stay valid."""


# FMP uses caret index tickers (^GSPC, ^VIX, ^VIX3M) that TradingView doesn't
# recognize — remap them to TradingView index symbols. Real ETF tickers used by
# the skills (SPY, QQQ) are real symbols and pass through unchanged. Shared by
# every skill subclass (harmless for skills that never request these).
DEFAULT_INDEX_REMAP = {
    "^GSPC": "SP:SPX",
    "^VIX": "TVC:VIX",
    "^VIX3M": "CBOE:VIX3M",
}


def _truthy_env(name: str) -> bool:
    return os.environ.get(name) in ("1", "true", "yes")


def _resolve_cli() -> list[str]:
    """The argv prefix for invoking the `tv` CLI.

    Order: explicit TV_CLI → global `tv` on PATH → node <TV_MCP_REPO>/src/cli.
    """
    explicit = os.environ.get("TV_CLI")
    if explicit:
        return [explicit] if explicit.endswith((".mjs", ".js")) is False else ["node", explicit]
    on_path = shutil.which("tv")
    if on_path:
        return [on_path]
    node_cli = os.path.join(TV_MCP_REPO, "src", "cli", "index.js")
    if os.path.exists(node_cli):
        return ["node", node_cli]
    raise ValueError(
        "tv CLI not found: run `npm install` in vendor/tradingview-mcp, "
        "or set TV_CLI / TV_MCP_REPO."
    )


# FMP's set of US exchange short-names — some skills filter on
# FMPClient.US_EXCHANGES, so it must exist as a class attribute here too.
class TVClient:
    US_EXCHANGES = ["NYSE", "NASDAQ", "AMEX", "NYSEArca", "BATS", "NMS", "NGM", "NCM"]

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        max_api_calls: int = 200,
        quote_as_list: bool = False,
        index_remap: Optional[dict] = None,
        cache_disable_env: str = "TV_NO_CACHE",
        min_bars: int = MIN_BARS,
        bars: int = BARS,
        settle: float = SETTLE,
        poll_interval: float = POLL_INTERVAL,
    ):
        # api_key accepted for interface parity with FMPClient; never used —
        # TradingView needs no key. max_api_calls is accepted (some skills pass
        # it) but TradingView has no quota, so it's only echoed in get_api_stats.
        self.max_api_calls = max_api_calls
        self.quote_as_list = quote_as_list
        self.index_remap = index_remap or {}
        self.min_bars = min_bars
        self.bars = bars
        # `settle` is now the *ceiling* the readiness poll waits before giving
        # up (and the backoff for the cold-chart retry), not a blind sleep paid
        # on every symbol. `poll_interval` is how often the chart's current
        # symbol is checked while waiting for the switch to register.
        self.settle = settle
        self.poll_interval = poll_interval
        self._cache_ok = metrics_cache is not None and not _truthy_env(cache_disable_env)

        self.cache: dict = {}
        # symbol -> exchange short-name, populated by get_earnings_calendar so
        # get_company_profile can fill exchangeShortName on the cache fast path.
        self._exchange_map: dict = {}
        self.api_calls_made = 0
        self.rate_limit_reached = False
        self._tf_set = False
        # Tri-state probe for the single-process `tv bars` fast path:
        # None = not probed yet, True = available, False = CLI predates the
        # command (every live fetch uses the legacy switch+poll+ohlcv path).
        self._bars_cmd_ok: Optional[bool] = None
        # Fail fast if the CLI is not reachable.
        self._cli_argv = _resolve_cli()

    # ------------------------------------------------------------------ CLI
    def _cli(self, *args: str, parse: bool = True, timeout: float = 40):
        self.api_calls_made += 1
        try:
            out = subprocess.run(
                [*self._cli_argv, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"  WARN: tv {' '.join(args)} timed out", file=sys.stderr)
            return None
        if out.returncode != 0:
            # A CLI checkout that predates `tv bars` rejects it with a plain
            # "Unknown command" on stderr — remember that so the fast path
            # stops probing and goes straight to the legacy path. Any other
            # failure (connection, timeout) is transient and leaves the
            # tri-state untouched.
            if args and args[0] == "bars" and "Unknown command" in (out.stderr or ""):
                self._bars_cmd_ok = False
            return None
        if not parse:
            return out.stdout
        try:
            return json.loads(out.stdout)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _symbol_ready(tv_symbol: str, current: str) -> bool:
        """True when the chart's reported `current` symbol matches the requested
        `tv_symbol`. The chart returns exchange-qualified symbols ("NASDAQ:AAPL")
        while callers may request a bare ticker ("AAPL") or an already-qualified
        index ("SP:SPX"), so compare the part after the colon on both sides."""
        if not current:
            return False
        cur, tgt = current.upper(), tv_symbol.upper()
        return cur == tgt or cur.split(":")[-1] == tgt.split(":")[-1]

    def _current_symbol(self) -> str:
        data = self._cli("symbol")
        return str(data.get("symbol", "")) if isinstance(data, dict) else ""

    def _switch_symbol(self, tv_symbol: str) -> bool:
        """Switch the chart to `tv_symbol` on the daily timeframe and wait until
        the switch registers, polling the chart's current symbol instead of
        blindly sleeping `settle`. Returns True once ready, False if the switch
        hasn't registered within `settle` (callers still try to read bars and
        fall through to their cold-chart retry)."""
        self._cli("symbol", tv_symbol, "--nowait", parse=False)  # skip ~10s DOM wait; poll below
        if not self._tf_set:
            self._cli("timeframe", "D", parse=False)
            self._tf_set = True
        deadline = time.perf_counter() + self.settle
        while True:
            if self._symbol_ready(tv_symbol, self._current_symbol()):
                return True
            if time.perf_counter() >= deadline:
                return False
            time.sleep(self.poll_interval)

    @staticmethod
    def _bars_batch_results(data) -> Optional[list]:
        """The `results` list of a parsed `tv bars` payload, or None when the
        payload is not bars-shaped (failed call, or some other JSON)."""
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data["results"]
        return None

    def _bars_live(self, tv_symbol: str, count: int) -> Optional[list[dict]]:
        """Raw daily bars (OLDEST FIRST, TradingView shape) for one already
        index-remapped symbol.

        Fast path: a single `tv bars` invocation — the CLI switches the chart
        and waits for readiness in-process, so one ~1s process/CDP setup
        replaces the legacy path's 3-4. A per-symbol failure reported by the
        CLI (invalid/delisted ticker it already waited out) returns None with
        NO legacy retry. The legacy path serves CLIs that predate `tv bars`
        (detected once via the `_bars_cmd_ok` tri-state) and transient CLI
        failures."""
        if self._bars_cmd_ok is not False:
            data = self._cli("bars", tv_symbol, "-n", str(count), timeout=_bars_call_timeout(1))
            results = self._bars_batch_results(data)
            if results is not None:
                self._bars_cmd_ok = True
                for r in results:
                    if r.get("symbol") == tv_symbol:
                        return (r.get("bars") or None) if r.get("success") else None
                return None
            if data is not None:
                # Parseable answer that is not a bars payload — treat the
                # command as unusable rather than re-probing every symbol.
                self._bars_cmd_ok = False
        return self._bars_live_legacy(tv_symbol, count)

    def _bars_live_legacy(self, tv_symbol: str, count: int) -> Optional[list[dict]]:
        """Pre-`tv bars` live path: one CLI spawn to switch the symbol, spawns
        to poll the switch, one to pull bars — plus blind settle retries for a
        cold chart. Kept as the fallback for older CLI checkouts."""
        self._switch_symbol(tv_symbol)
        data = self._cli("ohlcv", "-n", str(count))

        # Chart may still be loading right after a symbol switch — retry twice
        # with a longer settle (a cold chart needs a few seconds).
        for _ in range(2):
            if data and data.get("bars"):
                break
            time.sleep(self.settle * 1.5)
            data = self._cli("ohlcv", "-n", str(count))
        if not data or not data.get("bars"):
            return None
        return data["bars"]

    def _fetch_bars(self, symbol: str) -> list[dict]:
        """Pull daily bars for `symbol` from the live chart.

        Applies index_remap before the fetch (e.g. ^GSPC -> SP:SPX). Returns
        bars NEWEST FIRST in FMP-compatible dict form, or []."""
        tv_symbol = self.index_remap.get(symbol, symbol)
        raw = self._bars_live(tv_symbol, self.bars)
        if not raw:
            return []

        # Skip too-short histories cleanly instead of feeding them into the
        # calculators (which crash on a None SMA). An empty history reads as
        # "skip symbol" to every screener.
        if len(raw) < self.min_bars:
            print(
                f"  SKIP {symbol}: only {len(raw)} daily bars (<{self.min_bars})",
                file=sys.stderr,
            )
            return []

        return self._shape_bars(raw)

    @staticmethod
    def _shape_bars(raw: list[dict]) -> list[dict]:
        """TradingView bars (oldest first, `time` in UNIX seconds) -> FMP-shaped
        bars NEWEST FIRST."""
        bars = []
        for b in raw:
            try:
                ts = int(b["time"])
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except (KeyError, ValueError, OSError):
                iso = ""
            close = b.get("close", 0)
            bars.append(
                {
                    "date": iso,
                    "open": b.get("open", 0),
                    "high": b.get("high", 0),
                    "low": b.get("low", 0),
                    "close": close,
                    "adjClose": close,
                    "volume": b.get("volume", 0) or 0,
                }
            )
        bars.reverse()  # newest first, matching FMP
        return bars

    # ------------------------------------------------------- PRICE public API
    def get_historical_prices(self, symbol: str, days: int = 365) -> Optional[dict]:
        """Public price history in the default FMP shape: {symbol, historical}.

        Most skills consume this dict directly. The earnings-trade-analyzer
        subclass overrides this to return the bare list[dict] its scorer wants —
        which is why all INTERNAL callers (get_quote, get_company_profile,
        get_batch_historical) go through self._history() instead, so that
        override can't break them."""
        return self._history(symbol)

    def _history(self, symbol: str) -> Optional[dict]:
        # `days` is ignored — we always pull self.bars and let the calculators
        # slice the window they need. Matches FMP's return shape.
        cache_key = f"hist_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Fast path: fresh state/metrics/TICKER/ohlcv.json (no chart switch).
        # Require >=min_bars to mirror _fetch_bars; remapped index symbols are
        # not collected, so skip the cache for them and go live.
        if self._cache_ok and symbol not in self.index_remap:
            cb = metrics_cache.cached_ohlcv(symbol, min_bars=self.min_bars)
            if cb:
                result = {"symbol": symbol, "historical": cb}
                self.cache[cache_key] = result
                return result

        bars = self._fetch_bars(symbol)
        if not bars:
            self.cache[cache_key] = None
            return None
        result = {"symbol": symbol, "historical": bars}
        self.cache[cache_key] = result
        return result

    def get_quote(self, symbol: str):
        """Synthesize a quote from the daily history (TradingView has no quote
        endpoint mirroring FMP's fields; screeners only need price, 52-week
        high/low and average volume). Returns [dict] when quote_as_list, else a
        bare dict, to match the consuming FMPClient's shape."""
        cache_key = f"quote_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Fast path: fresh metrics snapshot (skip the chart switch + bar pull).
        if self._cache_ok and symbol not in self.index_remap:
            cq = metrics_cache.cached_quote(symbol)
            if cq:
                result = [cq] if self.quote_as_list else cq
                self.cache[cache_key] = result
                return result

        hist = self._history(symbol)
        if not hist or not hist["historical"]:
            self.cache[cache_key] = None
            return None

        bars = hist["historical"]  # newest first
        year = bars[:252] if len(bars) >= 252 else bars
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in year]
        lows = [b["low"] for b in year if b["low"] > 0]
        vols = [b["volume"] for b in bars[:50]]

        quote = {
            "symbol": symbol,
            "name": symbol,
            "price": closes[0] if closes else 0,
            "yearHigh": max(highs) if highs else 0,
            "yearLow": min(lows) if lows else 0,
            "avgVolume": (sum(vols) / len(vols)) if vols else 0,
            "volume": bars[0]["volume"] if bars else 0,
            "marketCap": 0,  # not available from chart bars
        }
        result = [quote] if self.quote_as_list else quote
        self.cache[cache_key] = result
        return result

    def _prefetch_bars(self, symbols: list[str]) -> None:
        """Bulk-load daily histories for `symbols` into the client cache.

        Symbols already cached (client cache or a fresh metrics snapshot) are
        skipped; the rest are fetched live in BATCH_CHUNK groups, ONE `tv
        bars` process per chunk instead of 3-4 spawns per symbol. Per-symbol
        failures are cached as misses (the CLI already waited them out
        in-process); a failed chunk SPAWN leaves its symbols uncached so the
        per-symbol path can retry them. No-op when the CLI lacks `tv bars`."""
        if self._bars_cmd_ok is False:
            return
        todo = []
        for sym in symbols:
            if f"hist_{sym}" in self.cache:
                continue
            if self._cache_ok and sym not in self.index_remap:
                cb = metrics_cache.cached_ohlcv(sym, min_bars=self.min_bars)
                if cb:
                    self.cache[f"hist_{sym}"] = {"symbol": sym, "historical": cb}
                    continue
            todo.append(sym)

        for start in range(0, len(todo), BATCH_CHUNK):
            chunk = todo[start : start + BATCH_CHUNK]
            tv_syms = [self.index_remap.get(s, s) for s in chunk]
            data = self._cli(
                "bars", *tv_syms, "-n", str(self.bars), timeout=_bars_call_timeout(len(chunk))
            )
            results = self._bars_batch_results(data)
            if results is None:
                if data is not None:
                    self._bars_cmd_ok = False
                if self._bars_cmd_ok is False:
                    return
                continue  # transient spawn failure: leave chunk unresolved
            self._bars_cmd_ok = True
            by_tv_symbol = {r.get("symbol"): r for r in results}
            for sym, tv_sym in zip(chunk, tv_syms):
                r = by_tv_symbol.get(tv_sym)
                if r is None:
                    continue
                raw = r.get("bars") or []
                if r.get("success") and len(raw) >= self.min_bars:
                    self.cache[f"hist_{sym}"] = {
                        "symbol": sym,
                        "historical": self._shape_bars(raw),
                    }
                else:
                    if r.get("success"):
                        print(
                            f"  SKIP {sym}: only {len(raw)} daily bars (<{self.min_bars})",
                            file=sys.stderr,
                        )
                    self.cache[f"hist_{sym}"] = None
            done = min(start + BATCH_CHUNK, len(todo))
            if len(todo) > BATCH_CHUNK:
                print(f"    Bars batch: {done}/{len(todo)}", flush=True)

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        self._prefetch_bars(symbols)
        results = {}
        total = len(symbols)
        for i, sym in enumerate(symbols):
            if (i + 1) % 10 == 0 or i == total - 1:
                print(f"    Progress: {i + 1}/{total}", flush=True)
            q = self.get_quote(sym)
            if q:
                # FMP's get_batch_quotes unwraps the list (one quote dict per
                # symbol), even though get_quote itself returns [dict]. Mirror
                # that so consumers do batch[sym]["price"], not batch[sym][0].
                results[sym] = q[0] if self.quote_as_list else q
        return results

    def get_batch_historical(self, symbols: list[str], days: int = 260) -> dict[str, list[dict]]:
        self._prefetch_bars(symbols)
        results = {}
        for sym in symbols:
            data = self._history(sym)
            if data and "historical" in data:
                results[sym] = data["historical"]
        return results

    def calculate_sma(self, prices: list[float], period: int) -> float:
        """Simple Moving Average (prices most-recent-first)."""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0
        return sum(prices[:period]) / period

    def calculate_ema(self, prices: list[float], period: int = 50) -> float:
        """Exponential Moving Average (prices most-recent-first), computed
        locally so no FMP key is needed. Matches FMPClient.calculate_ema."""
        if not prices:
            return 0.0
        if len(prices) < period:
            return sum(prices) / len(prices)
        prices_reversed = prices[::-1]  # oldest first
        ema = sum(prices_reversed[:period]) / period  # seed with SMA
        k = 2 / (period + 1)
        for price in prices_reversed[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def get_sp500_constituents(self) -> Optional[list[dict]]:
        """S&P 500 constituents from the committed scripts/lib/data/sp500.csv
        (falling back to <TV_MCP_REPO>/state/sp500.csv) — the same
        Wikipedia-derived snapshot scripts/collect_russell.js walks. Returns
        [{symbol, name, sector}]; dotted symbols (BRK.B) preserved."""
        cache_key = "sp500_constituents"
        if cache_key in self.cache:
            return self.cache[cache_key]

        csv_path = SP500_CSV if os.path.exists(SP500_CSV) else os.path.join(STATE_DIR, "sp500.csv")
        if not os.path.exists(csv_path):
            print(
                f"  WARN: sp500.csv not found at {SP500_CSV} or {STATE_DIR} (set TV_MCP_REPO)",
                file=sys.stderr,
            )
            return None

        import csv as _csv

        constituents = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                sym = (row.get("Symbol") or "").strip()
                if not sym:
                    continue
                constituents.append(
                    {
                        "symbol": sym,
                        "name": (row.get("Security") or sym).strip(),
                        "sector": (row.get("GICS Sector") or "Unknown").strip(),
                    }
                )
        if not constituents:
            return None
        self.cache[cache_key] = constituents
        return constituents

    # ------------------------------------------------- FUNDAMENTAL (scanner)
    def _fundamentals(self, symbol: str) -> Optional[dict]:
        """Fetch scanner fundamentals for `symbol` via `tv fundamentals
        --history`. Caches the parsed payload per symbol. Returns None on
        failure (NO FMP fallback — TradingView is the only source)."""
        cache_key = f"fund_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Fast path: fresh metrics snapshot carries the scanner fundamentals.
        if self._cache_ok and metrics_cache is not None:
            cf = metrics_cache.cached_fundamentals(symbol)
            if cf:
                self.cache[cache_key] = cf
                return cf

        return self._fundamentals_live(symbol)

    def _fundamentals_live(self, symbol: str) -> Optional[dict]:
        """Fetch scanner fundamentals from the live chart, bypassing the
        metrics-cache fast path, and pin the payload in the client cache.
        Used directly when a cached snapshot predates newly added scanner
        fields (e.g. the dividend history series).

        Switches the chart explicitly — get_quote's metrics-cache fast path
        never touches the chart, so relying on it would read whatever symbol
        the chart happens to show. The returned payload's symbol is verified
        before caching so a lagging chart can't poison the cache."""
        cache_key = f"fund_{symbol}"
        tv_symbol = self.index_remap.get(symbol, symbol)
        self._switch_symbol(tv_symbol)
        data = self._cli("fundamentals", "--history")
        if (
            not data
            or not data.get("success")
            or not self._symbol_ready(tv_symbol, str(data.get("symbol") or ""))
        ):
            self.cache[cache_key] = None
            return None
        self.cache[cache_key] = data
        return data

    def get_profile(self, symbol: str) -> Optional[list[dict]]:
        """FMP-shaped profile [{companyName, sector, industry, mktCap, price}]
        built from scanner fundamentals."""
        prof = self.get_company_profile(symbol)
        return [prof] if prof else None

    def get_company_profile(self, symbol: str) -> Optional[dict]:
        """Single FMP-shaped profile dict from scanner fundamentals. Fields:
        symbol, companyName, sector, industry, mktCap, exchangeShortName, price,
        days_listed_actual (approximated from available daily-bar count)."""
        cache_key = f"profile_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        data = self._fundamentals(symbol)
        if not data:
            self.cache[cache_key] = None
            return None
        profile = data.get("profile", {})
        valuation = data.get("valuation", {})
        full = data.get("symbol") or symbol  # e.g. "NASDAQ:NVDA"
        # Live `tv fundamentals` carries the exchange ("NASDAQ:NVDA"); the
        # metrics-cache fast path does not, so fall back to the exchange the
        # earnings scanner reported for this symbol.
        exch = full.split(":")[0] if ":" in full else self._exchange_map.get(symbol, "")
        quote = self.get_quote(symbol)
        price = (quote[0] if self.quote_as_list else quote) if quote else None
        price = price.get("price") if isinstance(price, dict) else None

        # days_listed_actual: FMP derives it from the IPO date; the scanner has
        # no IPO date, so approximate from the daily-bar history length
        # (trading days -> calendar days). Good enough for the recent-IPO filter
        # parabolic uses it for.
        days_listed = None
        hist = self.cache.get(f"hist_{symbol}")
        if hist and hist.get("historical"):
            n = len(hist["historical"])
            days_listed = int(n * 365 / 252)

        prof = {
            "symbol": symbol,
            "companyName": data.get("name") or profile.get("description"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "mktCap": valuation.get("market_cap_basic"),
            "exchangeShortName": exch,
            "price": price,
            "days_listed_actual": days_listed,
        }
        self.cache[cache_key] = prof
        return prof

    def get_company_profiles(self, symbols: list[str]) -> dict[str, dict]:
        """Batch profiles -> {symbol: profile dict}. Sequential (one chart
        switch per symbol); the metrics-cache fast path keeps it cheap."""
        out = {}
        total = len(symbols)
        for i, sym in enumerate(symbols):
            if (i + 1) % 10 == 0 or i == total - 1:
                print(f"    Profiles: {i + 1}/{total}", flush=True)
            p = self.get_company_profile(sym)
            if p:
                out[sym] = p
        return out

    def get_income_statement(
        self, symbol: str, period: str = "quarter", limit: int = 8
    ) -> Optional[list[dict]]:
        """FMP-shaped income statements (most recent first) from the scanner's
        historical series. `date` is left None — the CANSLIM calculators use it
        only for error text, never for logic."""
        data = self._fundamentals(symbol)
        if not data:
            return None
        hist = data.get("history", {})
        if period == "annual":
            rev = hist.get("total_revenue_fy_h", [])
            eps = hist.get("earnings_per_share_diluted_fy_h", [])
            ni = hist.get("net_income_fy_h", [])
        else:
            rev = hist.get("total_revenue_fq_h", [])
            eps = hist.get("earnings_per_share_diluted_fq_h", [])
            ni = hist.get("net_income_fq_h", [])
        n = min(limit, len(eps), len(rev))
        if n <= 0:
            return None
        return [
            {
                "date": None,
                "eps": eps[i],
                "epsdiluted": eps[i],
                "revenue": rev[i],
                "netIncome": ni[i] if i < len(ni) else None,
            }
            for i in range(n)
        ]

    def get_dividend_history(self, symbol: str) -> Optional[dict]:
        """FMP-shaped dividend history from the scanner's annual DPS series
        (`dps_common_stock_prim_issue_fy_h`, most-recent-first, ~20 fiscal
        years). Returns {"symbol", "historical": [{date, dividend,
        adjDividend}]} NEWEST FIRST, one synthetic year-end entry per
        completed fiscal year (labelled back from the last completed calendar
        year) — annual aggregation callers do on FMP's per-payment series
        works unchanged, with no partial-year distortion to exclude.

        Metrics-cache snapshots collected before the dividend fields were
        added lack the series; those fall through to a live scanner fetch."""
        data = self._fundamentals(symbol)
        dps = ((data or {}).get("history") or {}).get("dps_common_stock_prim_issue_fy_h")
        if not dps:
            data = self._fundamentals_live(symbol)
            dps = ((data or {}).get("history") or {}).get("dps_common_stock_prim_issue_fy_h")
        if not dps:
            return None
        last_completed = datetime.now(timezone.utc).year - 1
        historical = [
            {"date": f"{last_completed - i}-12-31", "dividend": d, "adjDividend": d}
            for i, d in enumerate(dps)
            if d is not None
        ]
        if not historical:
            return None
        return {"symbol": symbol, "historical": historical}

    def get_key_metrics(self, symbol: str, limit: int = 1) -> Optional[list[dict]]:
        """FMP-key-metrics-shaped snapshot built from scanner fundamentals.
        Returns a single-entry list (TradingView serves no key-metrics
        history; `limit` is accepted for signature parity).

        Semantics divergences from FMP, by design:
          - roe / netProfitMargin / dividendYield are PERCENT (TV native,
            e.g. 32.9), where FMP serves decimals — migrated screeners score
            against percent thresholds.
          - payoutRatio keeps FMP's decimal form (callers do `* 100`).
        Extra TV-only fields: operatingCashFlow, sharesOutstanding,
        annualDividendPerShare (last completed FY), continuousDividendGrowth
        (consecutive years of dividend growth)."""
        data = self._fundamentals(symbol)
        if not data:
            return None
        val = data.get("valuation") or {}
        ret = data.get("returns") or {}
        marg = data.get("margins") or {}
        bal = data.get("balance") or {}
        div = data.get("dividends") or {}
        cf = data.get("cashflow") or {}
        payout_pct = div.get("dividend_payout_ratio_ttm")
        return [
            {
                "peRatio": val.get("price_earnings_ttm"),
                "pbRatio": val.get("price_book_fq"),
                "roe": ret.get("return_on_equity_fq"),
                "netProfitMargin": marg.get("net_margin_ttm"),
                "payoutRatio": payout_pct / 100 if payout_pct is not None else None,
                "debtToEquity": bal.get("debt_to_equity_fq"),
                "currentRatio": bal.get("current_ratio_fq"),
                "dividendYield": div.get("dividends_yield_current"),
                "freeCashFlow": cf.get("free_cash_flow_ttm"),
                "operatingCashFlow": cf.get("cash_f_operating_activities_ttm"),
                "sharesOutstanding": val.get("total_shares_outstanding_fundamental"),
                "annualDividendPerShare": div.get("dps_common_stock_prim_issue_fy"),
                "continuousDividendGrowth": div.get("continuous_dividend_growth"),
            }
        ]

    def get_institutional_holders(self, symbol: str) -> Optional[list[dict]]:
        """Not exposed by the TradingView scanner. Returns None — CANSLIM's
        Finviz fallback (finviz_stock_client) supplies the I component instead,
        so no FMP key is needed."""
        return None

    # ------------------------------------------------------------ MACRO layer
    def get_vix_term_structure(self) -> Optional[dict]:
        """VIX term structure from TVC:VIX vs TVC:VIX3M daily closes. Returns
        {ratio, classification, vix, vix3m} or None if VIX3M is unavailable.

        Requires the subclass to map ^VIX/^VIX3M (or pass TVC symbols) via
        index_remap so they resolve to TradingView index symbols."""
        vix = self._spot("^VIX", "TVC:VIX")
        # TVC:VIX3M does not serve bars in TradingView Desktop; CBOE:VIX3M does.
        vix3m = self._spot("^VIX3M", "CBOE:VIX3M")
        if not vix or not vix3m or vix3m == 0:
            return None
        ratio = vix / vix3m
        if ratio < 0.85:
            classification = "steep_contango"
        elif ratio < 0.95:
            classification = "contango"
        elif ratio <= 1.05:
            classification = "flat"
        else:
            classification = "backwardation"
        return {
            "ratio": round(ratio, 4),
            "classification": classification,
            "vix": vix,
            "vix3m": vix3m,
        }

    def _spot(self, fmp_symbol: str, default_tv: str) -> Optional[float]:
        """Latest daily close for an index symbol, resolving via index_remap
        (falling back to `default_tv`). Bypasses the per-ticker metrics cache
        (indices aren't collected)."""
        tv_symbol = self.index_remap.get(fmp_symbol, default_tv)
        bars = self._bars_live(tv_symbol, 2)
        if not bars:
            return None
        return bars[-1].get("close")

    def get_treasury_rates(self, days: int = 600) -> Optional[list[dict]]:
        """Treasury 2y/10y yields from TVC:US02Y / TVC:US10Y daily closes,
        aligned by date. Returns [{date, year2, year10}] NEWEST FIRST (FMP
        shape), or None. Yields are in percent (the symbols' close == yield)."""
        y2 = self._yield_series("TVC:US02Y")
        y10 = self._yield_series("TVC:US10Y")
        if not y2 or not y10:
            return None
        m2 = {b["date"]: b["close"] for b in y2}
        m10 = {b["date"]: b["close"] for b in y10}
        dates = sorted(set(m2) & set(m10), reverse=True)  # newest first
        out = [
            {"date": d, "year2": m2[d], "year10": m10[d]}
            for d in dates[:days]
            if m2[d] is not None and m10[d] is not None
        ]
        return out or None

    def _yield_series(self, tv_symbol: str) -> Optional[list[dict]]:
        """Raw {date, close} daily series for a TVC yield symbol, OLDEST first.
        Bypasses min_bars/cache (yields aren't in the metrics cache)."""
        bars = self._bars_live(tv_symbol, self.bars)
        if not bars:
            return None
        out = []
        for b in bars:
            try:
                iso = datetime.fromtimestamp(int(b["time"]), tz=timezone.utc).strftime("%Y-%m-%d")
            except (KeyError, ValueError, OSError):
                continue
            out.append({"date": iso, "close": b.get("close")})
        return out or None

    def get_earnings_calendar(self, from_date: str, to_date: str) -> Optional[list[dict]]:
        """Market-wide earnings calendar for [from_date, to_date] (YYYY-MM-DD)
        from scanner.tradingview.com via <TV_MCP_REPO>/scripts/
        tv_earnings_calendar.mjs (the proven CDP+scanner pattern). Returns
        [{date, symbol, eps, epsEstimated, revenue, revenueEstimated, time}]
        (FMP shape), or None on failure."""
        cache_key = f"earnings_{from_date}_{to_date}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        if not os.path.exists(EARNINGS_MJS):
            print(
                f"  WARN: {EARNINGS_MJS} not found — earnings calendar unavailable "
                "(set TV_MCP_REPO)",
                file=sys.stderr,
            )
            return None
        try:
            out = subprocess.run(
                ["node", EARNINGS_MJS, "--from", from_date, "--to", to_date],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("  WARN: earnings calendar fetch timed out", file=sys.stderr)
            return None
        if out.returncode != 0:
            return None
        try:
            data = json.loads(out.stdout)
        except (json.JSONDecodeError, ValueError):
            return None
        events = data.get("earnings") if isinstance(data, dict) else data
        if events is None:
            return None
        # Remember each symbol's exchange so get_company_profile can fill
        # exchangeShortName even on the metrics-cache fast path.
        for e in events:
            sym, exch = e.get("symbol"), e.get("exchange")
            if sym and exch:
                self._exchange_map.setdefault(sym, exch)
        self.cache[cache_key] = events
        return events

    # ----------------------------------------------------------------- utility
    def clear_cache(self):
        self.cache.clear()

    def get_api_stats(self) -> dict:
        return {
            "cache_entries": len(self.cache),
            "api_calls_made": self.api_calls_made,
            "tv_cli_calls": self.api_calls_made,
            "max_api_calls": self.max_api_calls,
            "rate_limit_reached": self.rate_limit_reached,
        }
