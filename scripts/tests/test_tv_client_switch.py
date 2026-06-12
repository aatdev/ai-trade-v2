"""Live-path readiness contract for tv_client_base.

The cache-disabled (live chart) path used to pay a fixed `time.sleep(settle)`
(2.5s) on every symbol switch. Profiling showed the chart actually becomes
ready in ~0.2s on a warm chart, so the client now polls `tv symbol` until the
switch registers and proceeds immediately. These tests pin that behaviour with
a fully mocked `_cli` (no live chart, no real sleeping).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPTS_LIB))

import tv_client_base as tvb  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    """A TVClient whose CLI resolution is stubbed so construction needs no `tv`."""
    monkeypatch.setattr(tvb, "_resolve_cli", lambda: ["echo"])
    # Never really sleep in tests.
    monkeypatch.setattr(tvb.time, "sleep", lambda *_: None)
    c = tvb.TVClient(cache_disable_env="TV_NO_CACHE", settle=2.5, poll_interval=0.01)
    return c


def test_symbol_ready_matching():
    f = tvb.TVClient._symbol_ready
    assert f("AAPL", "NASDAQ:AAPL") is True
    assert f("SP:SPX", "SP:SPX") is True
    assert f("BRK.B", "NYSE:BRK.B") is True
    assert f("AAPL", "NASDAQ:MSFT") is False
    assert f("AAPL", "") is False


def test_switch_proceeds_as_soon_as_symbol_matches(client, monkeypatch):
    """The poll must stop the instant the chart reports the target symbol —
    not wait out the full `settle` ceiling."""
    calls: list[tuple] = []

    def fake_cli(*args, parse=True):
        calls.append(args)
        if args and args[0] == "symbol" and len(args) == 1:
            # `tv symbol` (get) — report the target immediately.
            return {"symbol": "NASDAQ:AAPL"}
        return ""

    monkeypatch.setattr(client, "_cli", fake_cli)

    assert client._switch_symbol("AAPL") is True
    # Polled the current symbol exactly once (matched on first check), so no
    # repeated polling and no reliance on a fixed sleep.
    get_polls = [c for c in calls if c == ("symbol",)]
    assert len(get_polls) == 1


def test_switch_polls_until_ready(client, monkeypatch):
    """When the switch lags, the client keeps polling until it registers."""
    state = {"polls": 0}

    def fake_cli(*args, parse=True):
        if args == ("symbol",):
            state["polls"] += 1
            # Not ready for the first two polls, then the chart catches up.
            return {"symbol": "NASDAQ:MSFT"} if state["polls"] >= 3 else {"symbol": "NASDAQ:OLD"}
        return ""

    monkeypatch.setattr(client, "_cli", fake_cli)
    assert client._switch_symbol("MSFT") is True
    assert state["polls"] == 3


def test_switch_times_out_without_blocking(client, monkeypatch):
    """A symbol that never registers must time out (return False) rather than
    hang — the caller then falls through to its cold-chart bar retry."""
    # settle is tiny here so the perf_counter deadline trips fast; sleep is a
    # no-op so this returns immediately.
    client.settle = 0.0

    def fake_cli(*args, parse=True):
        if args == ("symbol",):
            return {"symbol": "NASDAQ:NEVER"}
        return ""

    monkeypatch.setattr(client, "_cli", fake_cli)
    assert client._switch_symbol("AAPL") is False


def test_fetch_bars_uses_switch_then_reads(client, monkeypatch):
    """End-to-end on the LEGACY path (the mocked `_cli` answers the `bars`
    probe with a non-bars payload, so the client falls back): _fetch_bars
    switches via the poll, then returns shaped bars without ever paying a
    blind settle sleep."""
    monkeypatch.setattr(client, "_switch_symbol", lambda sym: True)

    bars_payload = {"bars": [{"time": 1700000000 + i * 86400, "open": 1, "high": 2,
                              "low": 0.5, "close": 1.5, "volume": 100} for i in range(250)]}
    monkeypatch.setattr(client, "_cli", lambda *a, parse=True, **kw: bars_payload)

    out = client._fetch_bars("AAPL")
    assert len(out) == 250
    # NEWEST-FIRST contract preserved.
    assert out[0]["date"] >= out[-1]["date"]
