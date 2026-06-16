"""Tests for sector_strength (sector ETF vs SPY) — offline with a fake client."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(SCRIPTS_LIB))

import sector_strength as ss  # noqa: E402


class _FakeClient:
    """get_historical_prices returns a 100-bar series with a known 63-day return.

    `returns` maps symbol -> percent return over 63 sessions; a symbol absent
    from the map (or mapped to None) yields no data.
    """

    def __init__(self, returns: dict):
        self.returns = returns
        self.calls: list[str] = []

    def get_historical_prices(self, symbol, days=260):
        self.calls.append(symbol)
        r = self.returns.get(symbol)
        if r is None:
            return {"historical": []}
        past = 100.0
        latest = past * (1 + r / 100)
        bars = [{"close": past} for _ in range(100)]
        bars[0] = {"close": latest}  # index 63 stays `past`
        return {"historical": bars}


def test_classify_leadership_thresholds():
    assert ss.classify_leadership(5.0) == "leading"
    assert ss.classify_leadership(8.0) == "leading"
    assert ss.classify_leadership(-5.0) == "lagging"
    assert ss.classify_leadership(-9.0) == "lagging"
    assert ss.classify_leadership(0.0) == "inline"
    assert ss.classify_leadership(4.9) == "inline"
    assert ss.classify_leadership(-4.9) == "inline"
    assert ss.classify_leadership(None) is None


def test_leading_lagging_inline():
    client = _FakeClient({"SPY": 2.0, "XLK": 10.0, "XLF": -8.0, "XLV": 4.0})
    out = ss.compute_sector_rs(
        client,
        ["Information Technology", "Financials", "Health Care"],
        lookback=63,
    )
    assert out["Information Technology"]["etf"] == "XLK"
    assert out["Information Technology"]["sector_rs"] == 8.0  # 10 - 2
    assert out["Information Technology"]["leadership"] == "leading"
    assert out["Financials"]["sector_rs"] == -10.0  # -8 - 2
    assert out["Financials"]["leadership"] == "lagging"
    assert out["Health Care"]["sector_rs"] == 2.0  # 4 - 2 → inline
    assert out["Health Care"]["leadership"] == "inline"


def test_unknown_sector_maps_to_none():
    client = _FakeClient({"SPY": 1.0})
    out = ss.compute_sector_rs(client, ["Frobnicators"], lookback=63)
    assert out["Frobnicators"] == {"etf": None, "sector_rs": None, "leadership": None}


def test_missing_etf_data_is_none_failopen():
    # XLE present as a sector but the client returns no bars for it.
    client = _FakeClient({"SPY": 1.0})  # XLE absent → empty history
    out = ss.compute_sector_rs(client, ["Energy"], lookback=63)
    assert out["Energy"]["etf"] == "XLE"
    assert out["Energy"]["leadership"] is None


def test_reuses_passed_spy_history_no_refetch():
    client = _FakeClient({"XLK": 9.0})  # no SPY in map; must use passed history
    spy_hist = [{"close": 101.0}] + [{"close": 100.0} for _ in range(99)]  # +1% over 63d
    out = ss.compute_sector_rs(
        client, ["Information Technology"], lookback=63, spy_history=spy_hist
    )
    assert "SPY" not in client.calls  # did not refetch SPY
    assert out["Information Technology"]["leadership"] == "leading"  # 9 - 1 = 8


def test_etf_fetched_once_per_sector_set():
    client = _FakeClient({"SPY": 0.0, "XLK": 7.0})
    ss.compute_sector_rs(
        client, ["Information Technology", "Technology"], lookback=63
    )  # both → XLK
    assert client.calls.count("XLK") == 1  # cached across duplicate ETF
