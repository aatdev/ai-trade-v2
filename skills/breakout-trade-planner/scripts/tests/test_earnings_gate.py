"""Tests for the earnings_gate module (no network — fetcher injected).

Data source is the public TradingView scanner; the injected fetcher receives
``(url, payload)`` and returns a parsed scanner response.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from earnings_gate import (
    GATE_BLOCKED,
    GATE_PASS,
    GATE_UNKNOWN,
    SCANNER_URL,
    EarningsFetchError,
    build_gate_fields,
    build_scan_payload,
    fetch_earnings_map,
    trading_days_until,
)

# 2026-06-08 is a Monday.
MON = date(2026, 6, 8)
FRI = date(2026, 6, 12)


def _ts(iso: str, hour_utc: int = 13) -> int:
    """Unix ts for the given date at hour_utc (13:00 UTC = 09:00 ET, same day)."""
    y, m, d = map(int, iso.split("-"))
    return int(datetime(y, m, d, hour_utc, tzinfo=timezone.utc).timestamp())


def _row(name: str, ts: int | None, exchange: str = "NASDAQ") -> dict:
    return {"s": f"{exchange}:{name}", "d": [name, ts]}


def _response(rows: list[dict]) -> dict:
    return {"totalCount": len(rows), "data": rows}


class TestTradingDaysUntil:
    def test_same_day_is_zero(self):
        assert trading_days_until("2026-06-08", MON) == 0

    def test_past_date_clamps_to_zero(self):
        assert trading_days_until("2026-06-05", MON) == 0

    def test_next_weekday(self):
        assert trading_days_until("2026-06-09", MON) == 1

    def test_weekend_is_skipped(self):
        # Friday -> next Monday is one trading day away
        assert trading_days_until("2026-06-15", FRI) == 1

    def test_full_calendar_week_is_five(self):
        assert trading_days_until("2026-06-15", MON) == 5


class TestBuildScanPayload:
    def test_payload_shape(self):
        payload = build_scan_payload({"NVDA", "AAPL"})
        name_filter = [f for f in payload["filter"] if f["left"] == "name"]
        assert name_filter == [{"left": "name", "operation": "in_range", "right": ["AAPL", "NVDA"]}]
        assert {"left": "is_primary", "operation": "equal", "right": True} in payload["filter"]
        assert payload["columns"] == ["name", "earnings_release_next_date"]
        assert payload["markets"] == ["america"]
        assert payload["range"][0] == 0


class TestFetchEarningsMap:
    def _fetcher(self, response):
        calls: list[tuple[str, dict]] = []

        def fake(url: str, payload: dict):
            calls.append((url, payload))
            return response

        return fake, calls

    def test_maps_next_earnings_dates(self):
        fake, _ = self._fetcher(
            _response(
                [
                    _row("AAPL", _ts("2026-06-20")),
                    _row("MSFT", _ts("2026-06-25"), exchange="NYSE"),
                ]
            )
        )
        result = fetch_earnings_map(["AAPL", "MSFT"], today=MON, fetcher=fake)
        assert result == {"AAPL": "2026-06-20", "MSFT": "2026-06-25"}

    def test_amc_timestamp_maps_to_us_trading_date(self):
        # After-market-close release: 2026-06-12 21:00 ET == 2026-06-13 01:00 UTC.
        # The gate must see June 12 (the US trading date), not June 13.
        amc_ts = int(datetime(2026, 6, 13, 1, 0, tzinfo=timezone.utc).timestamp())
        fake, _ = self._fetcher(_response([_row("ADBE", amc_ts)]))
        result = fetch_earnings_map(["ADBE"], today=MON, fetcher=fake)
        assert result == {"ADBE": "2026-06-12"}

    def test_null_timestamp_and_bad_rows_ignored(self):
        fake, _ = self._fetcher(
            _response(
                [
                    _row("AAPL", None),
                    {"s": "NASDAQ:NVDA"},  # no "d"
                    "not-a-dict",
                    {"s": "NASDAQ:AMD", "d": ["AMD"]},  # too short
                ]
            )
        )
        result = fetch_earnings_map(["AAPL", "NVDA", "AMD"], today=MON, fetcher=fake)
        assert result == {}

    def test_unrequested_symbols_ignored(self):
        fake, _ = self._fetcher(_response([_row("NVDA", _ts("2026-06-20"))]))
        result = fetch_earnings_map(["AAPL"], today=MON, fetcher=fake)
        assert result == {}

    def test_request_symbols_case_insensitive(self):
        fake, _ = self._fetcher(_response([_row("AAPL", _ts("2026-06-20"))]))
        result = fetch_earnings_map(["AaPl"], today=MON, fetcher=fake)
        assert result == {"AAPL": "2026-06-20"}

    def test_past_dates_are_ignored(self):
        fake, _ = self._fetcher(_response([_row("AAPL", _ts("2026-06-05"))]))
        result = fetch_earnings_map(["AAPL"], today=MON, fetcher=fake)
        assert result == {}

    def test_fetcher_receives_scanner_url_and_payload(self):
        fake, calls = self._fetcher(_response([]))
        fetch_earnings_map(["AAPL"], today=MON, fetcher=fake)
        assert len(calls) == 1
        url, payload = calls[0]
        assert url == SCANNER_URL
        name_filter = [f for f in payload["filter"] if f["left"] == "name"][0]
        assert name_filter["right"] == ["AAPL"]

    def test_empty_symbols_short_circuits(self):
        fake, calls = self._fetcher(_response([]))
        assert fetch_earnings_map([], today=MON, fetcher=fake) == {}
        assert calls == []

    def test_non_dict_response_raises(self):
        fake, _ = self._fetcher(["not", "a", "dict"])
        with pytest.raises(EarningsFetchError):
            fetch_earnings_map(["AAPL"], today=MON, fetcher=fake)

    def test_missing_data_array_raises(self):
        fake, _ = self._fetcher({"error": "scanner unavailable"})
        with pytest.raises(EarningsFetchError):
            fetch_earnings_map(["AAPL"], today=MON, fetcher=fake)


class TestBuildGateFields:
    EARNINGS_MAP = {"AAPL": "2026-06-12"}  # 4 trading days from MON

    def test_blocked_within_gate(self):
        fields = build_gate_fields("AAPL", self.EARNINGS_MAP, gate_days=10, today=MON)
        assert fields == {
            "earnings_date": "2026-06-12",
            "days_to_earnings": 4,
            "earnings_gate": GATE_BLOCKED,
        }

    def test_boundary_is_blocked_inclusive(self):
        fields = build_gate_fields("AAPL", self.EARNINGS_MAP, gate_days=4, today=MON)
        assert fields["earnings_gate"] == GATE_BLOCKED

    def test_pass_outside_gate(self):
        fields = build_gate_fields("AAPL", self.EARNINGS_MAP, gate_days=3, today=MON)
        assert fields["earnings_gate"] == GATE_PASS
        assert fields["days_to_earnings"] == 4

    def test_no_upcoming_date_passes(self):
        fields = build_gate_fields("MSFT", self.EARNINGS_MAP, gate_days=10, today=MON)
        assert fields == {
            "earnings_date": None,
            "days_to_earnings": None,
            "earnings_gate": GATE_PASS,
        }

    def test_fetch_failed_is_unknown(self):
        fields = build_gate_fields(
            "AAPL", self.EARNINGS_MAP, gate_days=10, today=MON, fetch_failed=True
        )
        assert fields["earnings_gate"] == GATE_UNKNOWN
        assert fields["earnings_date"] is None
