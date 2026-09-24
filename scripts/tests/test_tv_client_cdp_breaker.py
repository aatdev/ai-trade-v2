"""Fail-fast when TradingView Desktop's CDP endpoint is unreachable.

Each `tv` call against a dead endpoint burns ~16s of CDP retries; a 1,140-name
screen used to grind for hours. After CDP_FAILURE_LIMIT consecutive CDP
failures the client raises TVUnavailableError, and every later call raises
immediately without spawning the CLI.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

SCRIPTS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPTS_LIB))

import tv_client_base as tvb  # noqa: E402

CDP_DOWN = '{"success": false, "error": "CDP connection failed after 5 attempts: fetch failed"}'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(tvb, "_resolve_cli", lambda: ["tv"])
    return tvb.TVClient(cache_disable_env="TV_NO_CACHE")


def test_consecutive_cdp_failures_trip_the_breaker(client, monkeypatch):
    spawned = []

    def fake_run(argv, **k):
        spawned.append(argv)
        return types.SimpleNamespace(returncode=1, stdout=CDP_DOWN, stderr="")

    monkeypatch.setattr(tvb.subprocess, "run", fake_run)
    assert client._cli("symbol") is None  # first failure: still tolerated
    with pytest.raises(tvb.TVUnavailableError):
        client._cli("symbol")
    n = len(spawned)
    with pytest.raises(tvb.TVUnavailableError):
        client._cli("bars", "AAPL")
    assert len(spawned) == n  # tripped: no more 16s CLI spawns


def test_success_resets_the_failure_count(client, monkeypatch):
    outputs = iter([CDP_DOWN, '{"symbol": "NASDAQ:AAPL"}', CDP_DOWN])

    def fake_run(argv, **k):
        out = next(outputs)
        rc = 0 if "symbol" in out and "success" not in out else 1
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")

    monkeypatch.setattr(tvb.subprocess, "run", fake_run)
    assert client._cli("symbol") is None
    assert client._cli("symbol") == {"symbol": "NASDAQ:AAPL"}
    assert client._cli("symbol") is None  # counter was reset -> not tripped


def test_breaker_error_is_a_connection_error():
    assert issubclass(tvb.TVUnavailableError, ConnectionError)
