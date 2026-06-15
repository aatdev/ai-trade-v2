"""Single-process `tv bars` fast-path contract for tv_client_base.

The legacy live path spawned 3-4 `tv` CLI processes per symbol (symbol set,
readiness polls, ohlcv pull) at ~1s of process+CDP setup each, plus blind
retry sleeps — ~6s per symbol. The fast path fetches bars through one
`tv bars` invocation per symbol (readiness handled in-process by the CLI),
and whole chunks per invocation for batch calls. These tests pin:

  1. _fetch_bars issues exactly one `bars` call — no symbol/timeframe/ohlcv
  2. legacy fallback when the CLI predates `bars` (probed once, memoized)
  3. failure/min_bars semantics match the legacy path (skip symbol, no retry)
  4. index_remap applies before the CLI call
  5. get_batch_historical / get_batch_quotes prefetch in chunks, one spawn
     per chunk, and serve per-symbol reads from the client cache
  6. metrics-cache hits are excluded from the prefetch live fetch

All `_cli` traffic is faked — no live chart, no real sleeping.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPTS_LIB))

import tv_client_base as tvb  # noqa: E402


def make_bars(n, base_close=100.0):
    """OLDEST-FIRST raw TV bars, close rising toward the newest bar."""
    return [
        {
            "time": 1700000000 + i * 86400,
            "open": base_close + i,
            "high": base_close + i + 1,
            "low": base_close + i - 1,
            "close": base_close + i,
            "volume": 1000 + i,
        }
        for i in range(n)
    ]


def bars_payload(*entries):
    """Shape of `tv bars A B ...` CLI output."""
    results = []
    for e in entries:
        if e.get("success", True):
            bars = e.get("bars", make_bars(250))
            results.append(
                {
                    "symbol": e["symbol"],
                    "success": True,
                    "resolved": e["symbol"],
                    "bar_count": len(bars),
                    "bars": bars,
                }
            )
        else:
            results.append(
                {"symbol": e["symbol"], "success": False, "error": e.get("error", "timeout")}
            )
    ok = sum(1 for r in results if r["success"])
    return {
        "success": ok > 0,
        "requested": len(results),
        "fetched": ok,
        "failed": len(results) - ok,
        "results": results,
    }


class FakeCli:
    """Records every `_cli` invocation; routes responses by command word."""

    def __init__(self, handlers=None):
        self.calls = []
        self.handlers = handlers or {}

    def __call__(self, *args, parse=True, **kwargs):
        self.calls.append({"args": args, "parse": parse, "kwargs": kwargs})
        handler = self.handlers.get(args[0])
        if callable(handler):
            return handler(args)
        return handler

    def commands(self):
        return [c["args"][0] for c in self.calls]


@pytest.fixture
def client(monkeypatch):
    """TVClient with stubbed CLI resolution, no metrics cache, no real sleep."""
    monkeypatch.setattr(tvb, "_resolve_cli", lambda: ["tv-fake"])
    monkeypatch.setattr(tvb.time, "sleep", lambda *_: None)
    monkeypatch.setenv("TV_NO_CACHE", "1")
    return tvb.TVClient(settle=0.01, poll_interval=0.001)


# ── single-symbol fast path ──────────────────────────────────────────────────


def test_fetch_bars_single_cli_call(client, monkeypatch):
    fake = FakeCli({"bars": lambda args: bars_payload({"symbol": "AAPL"})})
    monkeypatch.setattr(client, "_cli", fake)

    hist = client.get_historical_prices("AAPL")

    assert hist and len(hist["historical"]) == 250
    # NEWEST-FIRST, FMP shape.
    assert hist["historical"][0]["date"] > hist["historical"][-1]["date"]
    assert hist["historical"][0]["adjClose"] == hist["historical"][0]["close"]
    # Exactly one CLI spawn, and it was the bars command with the bar count.
    assert fake.commands() == ["bars"]
    assert "AAPL" in fake.calls[0]["args"]
    assert str(tvb.BARS) in fake.calls[0]["args"]


def test_fallback_to_legacy_when_bars_unavailable(client, monkeypatch):
    """A CLI without the `bars` command (old checkout) must fall back to the
    legacy switch+poll+ohlcv path — and only probe `bars` once.

    The real `_cli` detects "Unknown command" on stderr and marks the command
    unavailable; the fake mimics that documented side effect."""

    def bars_unknown(args):
        client._bars_cmd_ok = False  # what _cli does on "Unknown command"
        return None

    def legacy(args):
        if len(args) == 1:  # `tv symbol` (get) poll
            return {"symbol": "NASDAQ:ANY"}
        return ""

    fake = FakeCli(
        {
            "bars": bars_unknown,
            "symbol": legacy,
            "timeframe": "",
            "ohlcv": {"bars": make_bars(250)},
        }
    )
    # Legacy poll matches any symbol so both fetches succeed.
    monkeypatch.setattr(client, "_symbol_ready", staticmethod(lambda tv, cur: True))
    monkeypatch.setattr(client, "_cli", fake)

    assert client.get_historical_prices("AAPL") is not None
    assert client.get_historical_prices("MSFT") is not None
    # `bars` probed exactly once across both symbols; legacy did the work.
    assert fake.commands().count("bars") == 1
    assert "ohlcv" in fake.commands()


def test_failed_symbol_skips_without_retry(client, monkeypatch):
    fake = FakeCli(
        {"bars": bars_payload({"symbol": "DEADQ", "success": False, "error": "empty series"})}
    )
    monkeypatch.setattr(client, "_cli", fake)

    assert client.get_historical_prices("DEADQ") is None
    # No legacy retry burned on a symbol the CLI already waited out in-process.
    assert fake.commands() == ["bars"]
    # Cached as a miss: a second read costs zero CLI calls.
    assert client.get_historical_prices("DEADQ") is None
    assert len(fake.calls) == 1


def test_min_bars_enforced_on_fast_path(client, monkeypatch):
    fake = FakeCli({"bars": bars_payload({"symbol": "IPO", "bars": make_bars(50)})})
    monkeypatch.setattr(client, "_cli", fake)
    assert client.get_historical_prices("IPO") is None


def test_index_remap_applied_to_bars_call(client, monkeypatch):
    client.index_remap = dict(tvb.DEFAULT_INDEX_REMAP)
    fake = FakeCli({"bars": bars_payload({"symbol": "SP:SPX"})})
    monkeypatch.setattr(client, "_cli", fake)

    hist = client.get_historical_prices("^GSPC")

    assert hist and len(hist["historical"]) == 250
    assert "SP:SPX" in fake.calls[0]["args"]
    assert "^GSPC" not in fake.calls[0]["args"]


def test_spot_uses_single_bars_call(client, monkeypatch):
    bars = make_bars(2, base_close=20.0)
    fake = FakeCli({"bars": bars_payload({"symbol": "TVC:VIX", "bars": bars})})
    monkeypatch.setattr(client, "_cli", fake)

    spot = client._spot("^VIX", "TVC:VIX")

    assert spot == bars[-1]["close"]  # newest bar's close
    assert fake.commands() == ["bars"]


# ── batch prefetch ───────────────────────────────────────────────────────────


def test_batch_historical_one_spawn_per_chunk(client, monkeypatch):
    symbols = [f"S{i:02d}" for i in range(tvb.BATCH_CHUNK + 5)]

    def bars_handler(args):
        requested = [a for a in args if a in symbols]
        return bars_payload(*({"symbol": s} for s in requested))

    fake = FakeCli({"bars": bars_handler})
    monkeypatch.setattr(client, "_cli", fake)

    out = client.get_batch_historical(symbols)

    assert len(out) == len(symbols)
    assert all(len(v) == 250 for v in out.values())
    # One full chunk + one remainder chunk — nothing per-symbol.
    assert fake.commands() == ["bars", "bars"]
    assert len([a for a in fake.calls[0]["args"] if a in symbols]) == tvb.BATCH_CHUNK
    assert len([a for a in fake.calls[1]["args"] if a in symbols]) == 5
    # Chunk calls get a proportional subprocess timeout, not the default.
    assert fake.calls[0]["kwargs"].get("timeout", 0) > 40


def test_batch_quotes_served_from_prefetched_bars(client, monkeypatch):
    symbols = ["AAA", "BBB", "CCC"]

    def bars_handler(args):
        requested = [a for a in args if a in symbols]
        return bars_payload(*({"symbol": s, "bars": make_bars(250)} for s in requested))

    fake = FakeCli({"bars": bars_handler})
    monkeypatch.setattr(client, "_cli", fake)

    quotes = client.get_batch_quotes(symbols)

    assert set(quotes) == set(symbols)
    newest_close = make_bars(250)[-1]["close"]
    assert all(q["price"] == newest_close for q in quotes.values())
    assert fake.commands() == ["bars"]  # one spawn for the whole batch


def test_batch_mixed_failures_cached_as_misses(client, monkeypatch):
    def bars_handler(args):
        entries = []
        for a in args:
            if a == "GOOD":
                entries.append({"symbol": "GOOD"})
            elif a == "DEADQ":
                entries.append({"symbol": "DEADQ", "success": False})
        return bars_payload(*entries) if entries else None

    fake = FakeCli({"bars": bars_handler})
    monkeypatch.setattr(client, "_cli", fake)

    out = client.get_batch_historical(["GOOD", "DEADQ"])

    assert set(out) == {"GOOD"}
    n_calls = len(fake.calls)
    # The failed symbol is a cached miss — later reads cost no CLI traffic.
    assert client.get_quote("DEADQ") is None
    assert len(fake.calls) == n_calls


def test_batch_skips_metrics_cache_hits(client, monkeypatch):
    """Symbols served by a fresh metrics snapshot must not be re-fetched live."""
    client._cache_ok = True
    shaped = [
        {
            "date": f"2026-06-{d:02d}",
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "adjClose": 1.5,
            "volume": 10,
        }
        for d in range(11, 1, -1)
    ]
    monkeypatch.setattr(
        tvb.metrics_cache,
        "cached_ohlcv",
        lambda sym, min_bars=1, **kw: shaped if sym == "CACHED" else None,
    )

    def bars_handler(args):
        assert "CACHED" not in args
        return bars_payload({"symbol": "LIVE"})

    fake = FakeCli({"bars": bars_handler})
    monkeypatch.setattr(client, "_cli", fake)
    client.min_bars = 5  # the fake snapshot is 10 bars long

    out = client.get_batch_historical(["CACHED", "LIVE"])

    assert set(out) == {"CACHED", "LIVE"}
    assert fake.commands() == ["bars"]
    assert "CACHED" not in fake.calls[0]["args"]


def test_batch_chunk_cli_failure_leaves_symbols_unresolved(client, monkeypatch):
    """If a whole chunk spawn dies (timeout/conn), its symbols must NOT be
    cached as misses — the per-symbol path retries them."""
    state = {"n": 0}

    def bars_handler(args):
        state["n"] += 1
        if state["n"] == 1:
            return None  # chunk spawn failed
        requested = [a for a in args if a.startswith("S")]
        return bars_payload(*({"symbol": s} for s in requested))

    fake = FakeCli({"bars": bars_handler})
    monkeypatch.setattr(client, "_cli", fake)

    out = client.get_batch_historical(["S01", "S02"])

    # Per-symbol fallback still produced data.
    assert set(out) == {"S01", "S02"}


def test_batch_mapping_is_by_name_not_position(client, monkeypatch):
    """Each chunk result is matched to its symbol by NAME, not response order.

    Feeds a batch reply that is (a) shuffled vs the request order, (b) carries an
    extra unrequested symbol, (c) gives each symbol a UNIQUE marker close — so a
    position-based mis-map would attach the wrong bars and be detectable.
    """
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    marker = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0, "DDD": 40.0}

    def bars_handler(args):
        requested = [a for a in args if a in symbols]
        entries = [
            {"symbol": s, "bars": make_bars(250, base_close=marker[s])} for s in requested
        ]
        entries = list(reversed(entries))  # response order != request order
        entries.insert(2, {"symbol": "ZZZ_INTRUDER", "bars": make_bars(250, base_close=999.0)})
        return bars_payload(*entries)

    fake = FakeCli({"bars": bars_handler})
    monkeypatch.setattr(client, "_cli", fake)

    out = client.get_batch_historical(symbols)

    # Intruder ignored, no symbol dropped.
    assert set(out) == set(symbols)
    # Every symbol carries ITS OWN marker close (newest first), none collapsed.
    for s in symbols:
        expected = make_bars(250, base_close=marker[s])[-1]["close"]
        assert out[s][0]["close"] == expected, f"{s} got the wrong symbol's bars"
    assert len({out[s][0]["close"] for s in symbols}) == len(symbols)
