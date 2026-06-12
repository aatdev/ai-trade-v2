"""Tests for tv_price_adapter.py — TradingView-backed daily closes for MAE/MFE."""

from datetime import date

from tv_price_adapter import TVPriceAdapter


class _FakeClient:
    """Stands in for the shared TradingView FMPClient drop-in."""

    def __init__(self, bars):
        self.bars = bars
        self.calls = []

    def get_historical_prices(self, symbol, days=365):
        self.calls.append((symbol, days))
        return {"symbol": symbol, "historical": self.bars}


def _bar(iso, close, adj=None):
    return {"date": iso, "close": close, "adjClose": adj if adj is not None else close}


class TestGetDailyCloses:
    def test_filters_range_and_returns_oldest_first(self):
        # TV layer returns FMP-shaped bars NEWEST first
        client = _FakeClient(
            [
                _bar("2026-06-08", 105.0),
                _bar("2026-06-05", 104.0),
                _bar("2026-06-04", 103.0),
                _bar("2026-06-03", 102.0),
                _bar("2026-06-02", 101.0),
            ]
        )
        adapter = TVPriceAdapter(client=client)
        rows = adapter.get_daily_closes("AAPL", "2026-06-03", "2026-06-05")
        assert rows == [
            {"date": "2026-06-03", "close": 102.0, "high": 102.0, "low": 102.0},
            {"date": "2026-06-04", "close": 103.0, "high": 103.0, "low": 103.0},
            {"date": "2026-06-05", "close": 104.0, "high": 104.0, "low": 104.0},
        ]

    def test_prefers_adj_close(self):
        client = _FakeClient([_bar("2026-06-03", 100.0, adj=99.5)])
        adapter = TVPriceAdapter(client=client)
        rows = adapter.get_daily_closes("AAPL", "2026-06-01", "2026-06-08")
        assert rows == [{"date": "2026-06-03", "close": 99.5, "high": 99.5, "low": 99.5}]

    def test_datetime_style_range_bounds_are_normalized(self):
        client = _FakeClient([_bar("2026-06-03", 100.0)])
        adapter = TVPriceAdapter(client=client)
        rows = adapter.get_daily_closes(
            "AAPL", "2026-06-03T00:00:00+00:00", "2026-06-03T23:59:59+00:00"
        )
        assert len(rows) == 1

    def test_lookback_days_cover_from_date(self):
        client = _FakeClient([])
        adapter = TVPriceAdapter(client=client)
        from_date = "2026-01-05"
        adapter.get_daily_closes("AAPL", from_date, "2026-06-05")
        (_, days), *_ = client.calls
        assert days >= (date.today() - date(2026, 1, 5)).days

    def test_empty_history_returns_empty(self):
        client = _FakeClient([])
        adapter = TVPriceAdapter(client=client)
        assert adapter.get_daily_closes("AAPL", "2026-06-01", "2026-06-08") == []

    def test_none_history_returns_empty(self):
        class _NoneClient:
            def get_historical_prices(self, symbol, days=365):
                return None

        adapter = TVPriceAdapter(client=_NoneClient())
        assert adapter.get_daily_closes("AAPL", "2026-06-01", "2026-06-08") == []

    def test_malformed_bars_skipped(self):
        client = _FakeClient(
            [
                {"date": "", "close": 1.0},
                {"close": 2.0},
                {"date": "2026-06-03"},
                _bar("2026-06-04", 103.0),
            ]
        )
        adapter = TVPriceAdapter(client=client)
        rows = adapter.get_daily_closes("AAPL", "2026-06-01", "2026-06-08")
        assert rows == [{"date": "2026-06-04", "close": 103.0, "high": 103.0, "low": 103.0}]


def test_mae_mfe_pipeline_with_tv_adapter():
    """compute_mae_mfe accepts the TV adapter (same contract as FMP adapter)."""
    import thesis_review

    client = _FakeClient(
        [
            _bar("2026-06-05", 95.0),  # newest first: dip below entry then exit
            _bar("2026-06-04", 112.0),
            _bar("2026-06-03", 90.0),
            _bar("2026-06-02", 100.0),
        ]
    )
    thesis = {
        "ticker": "AAPL",
        "entry": {"actual_price": 100.0, "actual_date": "2026-06-02T00:00:00+00:00"},
        "exit": {"actual_date": "2026-06-05T00:00:00+00:00"},
    }
    result = thesis_review.compute_mae_mfe(thesis, TVPriceAdapter(client=client))
    assert result["mae_pct"] == -10.0  # low 90 vs entry 100
    assert result["mfe_pct"] == 12.0  # high 112 vs entry 100
