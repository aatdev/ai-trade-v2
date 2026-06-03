"""Performance contract for metrics_cache's OpenSearch backend.

The hot path of every cache-backed screener is `metrics_cache._os_request`.
Profiling showed the TCP handshake to the OpenSearch host costs ~1s, paid on
EVERY request because each call opened (and closed) a fresh connection. These
tests pin the two fixes:

  1. A single persistent keep-alive connection is reused across requests
     (the handshake is paid once, not per request).
  2. `read_metrics` memoizes the per-ticker doc so callers that need both the
     quote and the fundamentals projection (get_company_profile) don't fetch
     the same `_doc` twice.

They use fakes — no live network — so they stay fast and deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPTS_LIB))

import metrics_cache as mc  # noqa: E402


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConn:
    """Records requests; counts how many distinct connections were opened."""

    open_count = 0

    def __init__(self, *, fail_first_request: bool = False):
        type(self).open_count += 1
        self.requests: list[tuple] = []
        self._fail_first_request = fail_first_request
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        if self._fail_first_request and not self.requests:
            self.requests.append((method, path))
            raise OSError("stale keep-alive connection")
        self.requests.append((method, path))

    def getresponse(self):
        return _FakeResponse(200, b'{"found": true, "_source": {"ok": 1}}')

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module globals (connection, breaker, memo) around every test."""
    mc._reset_backend_state()
    _FakeConn.open_count = 0
    yield
    mc._reset_backend_state()


def test_connection_is_reused_across_requests(monkeypatch):
    monkeypatch.setattr(mc, "_make_conn", lambda: _FakeConn())

    for _ in range(5):
        out = mc._os_request("GET", "/idx/_doc/X")
        assert out == {"found": True, "_source": {"ok": 1}}

    # Five requests, but only ONE connection opened — keep-alive reuse.
    assert _FakeConn.open_count == 1


def test_stale_connection_triggers_one_reconnect(monkeypatch):
    conns: list[_FakeConn] = []

    def _factory():
        c = _FakeConn(fail_first_request=not conns)  # first conn fails once
        conns.append(c)
        return c

    monkeypatch.setattr(mc, "_make_conn", _factory)

    out = mc._os_request("GET", "/idx/_doc/X")
    assert out == {"found": True, "_source": {"ok": 1}}
    # First connection raised on its request -> reconnect -> success on the 2nd.
    assert _FakeConn.open_count == 2
    assert conns[0].closed is True
    assert mc._os_down is False  # a single stale socket must NOT trip the breaker


def test_read_metrics_memoizes_per_ticker(monkeypatch):
    calls: list[str] = []

    def _fake_os_read(ticker):
        calls.append(ticker)
        return {"ticker": ticker, "collected_at": "2026-06-03T00:00:00Z"}

    monkeypatch.setattr(mc, "os_read_metrics", _fake_os_read)

    a = mc.read_metrics("AAPL")
    b = mc.read_metrics("AAPL")
    assert a is b  # same object returned from memo
    assert calls == ["AAPL"]  # OpenSearch hit exactly once

    mc.read_metrics("MSFT")
    assert calls == ["AAPL", "MSFT"]


def test_memo_does_not_cache_misses(monkeypatch):
    """A None result (genuine miss / transient failure) must stay retryable."""
    monkeypatch.setattr(mc, "os_read_metrics", lambda t: None)
    monkeypatch.setattr(mc, "_read_json", lambda p: None)

    assert mc.read_metrics("ZZZZ") is None
    assert mc.read_metrics("ZZZZ") is None  # no crash, not pinned to a bad value


def test_freshness_window_is_three_hours():
    """The cache is trusted only within a 3-hour window (down from 2 days)."""
    assert mc.STALE_HOURS == 3.0
    assert mc.STALE_DAYS == pytest.approx(3.0 / 24.0)


def _snap(hours_old: float) -> dict:
    """A metrics snapshot collected `hours_old` hours ago (UTC ISO, Z suffix)."""
    from datetime import datetime, timedelta, timezone

    ts = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    return {"collected_at": ts.isoformat().replace("+00:00", "Z")}


def test_is_fresh_at_three_hour_boundary():
    assert mc.is_fresh(_snap(1.0)) is True       # 1h old → fresh
    assert mc.is_fresh(_snap(2.9)) is True        # just under 3h → fresh
    assert mc.is_fresh(_snap(4.0)) is False       # 4h old → stale, go live
    assert mc.is_fresh(_snap(48.0)) is False      # what the OLD 2-day window let through
