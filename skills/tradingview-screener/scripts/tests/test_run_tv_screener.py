"""Tests for run_tv_screener.py — TradingView Stock Screener (All Stocks) CLI.

All tests run offline: network access is monkeypatched.
"""

from __future__ import annotations

import json

import pytest
import run_tv_screener as tvs

# ---------------------------------------------------------------------------
# resolve_field
# ---------------------------------------------------------------------------


class TestResolveField:
    def test_alias_pe(self):
        assert tvs.resolve_field("pe") == "price_earnings_ttm"

    def test_alias_market_cap(self):
        assert tvs.resolve_field("market_cap") == "market_cap_basic"
        assert tvs.resolve_field("mkt_cap") == "market_cap_basic"

    def test_alias_div_yield(self):
        assert tvs.resolve_field("div_yield") == "dividends_yield_current"

    def test_alias_rsi_case_insensitive(self):
        assert tvs.resolve_field("rsi") == "RSI"
        assert tvs.resolve_field("RSI") == "RSI"

    def test_raw_scanner_field_passthrough(self):
        assert tvs.resolve_field("market_cap_basic") == "market_cap_basic"
        assert tvs.resolve_field("Perf.1M") == "Perf.1M"
        assert tvs.resolve_field("ADX+DI") == "ADX+DI"
        assert tvs.resolve_field("RSI|1W") == "RSI|1W"

    def test_invalid_field_rejected(self):
        with pytest.raises(ValueError):
            tvs.resolve_field("foo bar")
        with pytest.raises(ValueError):
            tvs.resolve_field("close;drop")


# ---------------------------------------------------------------------------
# parse_value
# ---------------------------------------------------------------------------


class TestParseValue:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.5B", 1_500_000_000.0),
            ("300M", 300_000_000.0),
            ("500K", 500_000.0),
            ("2T", 2_000_000_000_000.0),
            ("3", 3.0),
            ("3.5", 3.5),
            ("-2", -2.0),
            ("25%", 25.0),
            ("true", True),
            ("False", False),
        ],
    )
    def test_numeric_and_bool(self, raw, expected):
        assert tvs.parse_value(raw) == expected

    def test_field_reference_stays_string(self):
        assert tvs.parse_value("EMA200") == "EMA200"

    def test_enum_value_stays_string(self):
        assert tvs.parse_value("Finance") == "Finance"


# ---------------------------------------------------------------------------
# parse_filter_token
# ---------------------------------------------------------------------------


class TestParseFilterToken:
    def test_less(self):
        assert tvs.parse_filter_token("pe<20") == {
            "left": "price_earnings_ttm",
            "operation": "less",
            "right": 20.0,
        }

    def test_egreater_with_suffix(self):
        assert tvs.parse_filter_token("mkt_cap>=1.5B") == {
            "left": "market_cap_basic",
            "operation": "egreater",
            "right": 1_500_000_000.0,
        }

    def test_greater(self):
        assert tvs.parse_filter_token("volume>1M")["operation"] == "greater"

    def test_eless(self):
        assert tvs.parse_filter_token("rsi<=30") == {
            "left": "RSI",
            "operation": "eless",
            "right": 30.0,
        }

    def test_range(self):
        assert tvs.parse_filter_token("div_yield=3..8") == {
            "left": "dividends_yield_current",
            "operation": "in_range",
            "right": [3.0, 8.0],
        }

    def test_multivalue_strings(self):
        assert tvs.parse_filter_token("sector=Finance|Utilities") == {
            "left": "sector",
            "operation": "in_range",
            "right": ["Finance", "Utilities"],
        }

    def test_field_to_field_comparison(self):
        assert tvs.parse_filter_token("close>EMA200") == {
            "left": "close",
            "operation": "greater",
            "right": "EMA200",
        }

    def test_not_equal(self):
        assert tvs.parse_filter_token("change!=0")["operation"] == "nequal"

    def test_equal_bool(self):
        assert tvs.parse_filter_token("is_primary=true") == {
            "left": "is_primary",
            "operation": "equal",
            "right": True,
        }

    @pytest.mark.parametrize("bad", ["pe~20", "pe<", "<20", "pe", ""])
    def test_invalid_tokens(self, bad):
        with pytest.raises(ValueError):
            tvs.parse_filter_token(bad)


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------


class TestBuildPayload:
    def test_default_universe_all_stocks(self):
        payload = tvs.build_payload(filters=["pe<20"])
        assert payload["markets"] == ["america"]
        # filter2 = All Stocks universe: 4 OR arms + pre-ipo exclusion
        f2 = payload["filter2"]
        assert f2["operator"] == "and"
        or_block = f2["operands"][0]["operation"]
        assert or_block["operator"] == "or"
        assert len(or_block["operands"]) == 4
        # flat filter contains defaults + user expression
        lefts = [f["left"] for f in payload["filter"]]
        assert "is_blacklisted" in lefts
        assert "is_primary" in lefts
        assert "price_earnings_ttm" in lefts

    def test_universe_common_only(self):
        payload = tvs.build_payload(filters=[], universe="common")
        or_block = payload["filter2"]["operands"][0]["operation"]
        assert len(or_block["operands"]) == 1

    def test_include_secondary_drops_is_primary(self):
        payload = tvs.build_payload(filters=[], include_secondary=True)
        lefts = [f["left"] for f in payload["filter"]]
        assert "is_primary" not in lefts

    def test_index_sp500(self):
        payload = tvs.build_payload(filters=[], index="sp500")
        assert payload["symbols"]["symbolset"] == ["SYML:SP;SPX"]

    def test_index_raw_syml_passthrough(self):
        payload = tvs.build_payload(filters=[], index="SYML:TVC;RUT")
        assert payload["symbols"]["symbolset"] == ["SYML:TVC;RUT"]

    def test_index_unknown_rejected(self):
        with pytest.raises(ValueError):
            tvs.build_payload(filters=[], index="sp9000")

    def test_sectors(self):
        payload = tvs.build_payload(filters=[], sectors=["Finance", "Utilities"])
        expr = [f for f in payload["filter"] if f["left"] == "sector"][0]
        assert expr == {
            "left": "sector",
            "operation": "in_range",
            "right": ["Finance", "Utilities"],
        }

    def test_sort_descending_default(self):
        payload = tvs.build_payload(filters=[])
        assert payload["sort"] == {"sortBy": "market_cap_basic", "sortOrder": "desc"}

    def test_sort_ascending(self):
        payload = tvs.build_payload(filters=[], sort="pe")
        assert payload["sort"] == {"sortBy": "price_earnings_ttm", "sortOrder": "asc"}

    def test_sort_descending_dash(self):
        payload = tvs.build_payload(filters=[], sort="-div_yield")
        assert payload["sort"] == {
            "sortBy": "dividends_yield_current",
            "sortOrder": "desc",
        }

    def test_sort_colon_suffix(self):
        payload = tvs.build_payload(filters=[], sort="div_yield:desc")
        assert payload["sort"] == {
            "sortBy": "dividends_yield_current",
            "sortOrder": "desc",
        }
        payload = tvs.build_payload(filters=[], sort="pe:asc")
        assert payload["sort"] == {"sortBy": "price_earnings_ttm", "sortOrder": "asc"}

    def test_limit_sets_range(self):
        payload = tvs.build_payload(filters=[], limit=25)
        assert payload["range"] == [0, 25]

    def test_columns_preset_overview(self):
        payload = tvs.build_payload(filters=[], columns="overview")
        assert "name" in payload["columns"]
        assert "market_cap_basic" in payload["columns"]

    def test_columns_custom_list(self):
        payload = tvs.build_payload(filters=[], columns="name,close,rsi")
        assert payload["columns"] == ["name", "close", "RSI"]

    def test_add_columns_dedup(self):
        payload = tvs.build_payload(filters=[], columns="overview", add_columns=["close", "ADX"])
        assert payload["columns"].count("close") == 1
        assert "ADX" in payload["columns"]

    def test_analyst_rating_envelope(self):
        payload = tvs.build_payload(filters=[], analyst_rating=["strong_buy", "buy"])
        expr = [f for f in payload["filter"] if f["left"] == "recommendation_mark"][0]
        assert expr["operation"] == "in_range"
        lo, hi = expr["right"]
        assert lo == pytest.approx(1.0)
        assert hi == pytest.approx(2.5)

    def test_technical_rating_envelope(self):
        payload = tvs.build_payload(filters=[], technical_rating=["strong_buy"])
        expr = [f for f in payload["filter"] if f["left"] == "Recommend.All"][0]
        assert expr["right"] == [0.5, 1.0]

    def test_unknown_rating_rejected(self):
        with pytest.raises(ValueError):
            tvs.build_payload(filters=[], analyst_rating=["awesome"])


# ---------------------------------------------------------------------------
# humanize
# ---------------------------------------------------------------------------


class TestHumanize:
    def test_trillions(self):
        assert tvs.humanize(5_324_726_000_000, "market_cap_basic") == "5.32T"

    def test_billions(self):
        assert tvs.humanize(1_500_000_000, "market_cap_basic") == "1.50B"

    def test_volume_millions(self):
        assert tvs.humanize(136_450_000, "volume") == "136.45M"

    def test_signed_percent_for_change_and_perf(self):
        assert tvs.humanize(3.4567, "change") == "+3.46%"
        assert tvs.humanize(-1.2, "Perf.1M") == "-1.20%"

    def test_unsigned_percent_for_yield_and_margin(self):
        assert tvs.humanize(7.58, "dividends_yield_current") == "7.58%"
        assert tvs.humanize(42.1, "gross_margin_ttm") == "42.10%"

    def test_relative_volume_is_ratio_not_big_number(self):
        assert tvs.humanize(1.234, "relative_volume_10d_calc") == "1.23"

    def test_continuous_dividend_years_are_counts(self):
        assert tvs.humanize(7.0, "continuous_dividend_growth") == "7"
        assert tvs.humanize(29.0, "continuous_dividend_payout") == "29"

    def test_unix_timestamp_renders_as_date(self):
        # 2026-06-21 (UTC)
        assert tvs.humanize(1782000000, "dividend_ex_date_upcoming") == "2026-06-21"
        assert tvs.humanize(1782000000, "earnings_release_next_date") == "2026-06-21"

    def test_price_plain(self):
        assert tvs.humanize(219.81, "close") == "219.81"

    def test_none(self):
        assert tvs.humanize(None, "close") == "—"

    def test_string_passthrough(self):
        assert tvs.humanize("Finance", "sector") == "Finance"


# ---------------------------------------------------------------------------
# render_markdown / reports
# ---------------------------------------------------------------------------

CANNED_RESPONSE = {
    "totalCount": 2,
    "data": [
        {"s": "NASDAQ:NVDA", "d": ["NVDA", 220.03, 5_324_726_000_000]},
        {"s": "NASDAQ:AAPL", "d": ["AAPL", 311.25, 4_571_439_837_492]},
    ],
}


class TestRenderMarkdown:
    def test_contains_symbols_and_header(self):
        md = tvs.render_markdown(
            CANNED_RESPONSE,
            columns=["name", "close", "market_cap_basic"],
            meta={"market": "america", "universe": "all", "filters": ["mkt_cap>1B"]},
        )
        assert "NASDAQ:NVDA" in md
        assert "5.32T" in md
        assert "Total matches: 2" in md
        assert "mkt_cap>1B" in md


class TestWriteReports:
    def test_writes_md_and_json(self, tmp_path):
        md_path, json_path = tvs.write_reports(
            CANNED_RESPONSE,
            columns=["name", "close", "market_cap_basic"],
            meta={"market": "america", "universe": "all", "filters": []},
            output_dir=str(tmp_path),
            screen_name="test scan",
        )
        assert md_path.exists()
        assert json_path.exists()
        assert "tradingview_screener_test-scan_" in md_path.name
        data = json.loads(json_path.read_text())
        assert data["totalCount"] == 2
        assert data["rows"][0]["symbol"] == "NASDAQ:NVDA"
        assert data["rows"][0]["close"] == 220.03


# ---------------------------------------------------------------------------
# run_scan retry logic
# ---------------------------------------------------------------------------


class TestRunScan:
    def test_retries_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def fake_post(url, payload, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise tvs.TransientScanError("HTTP 429")
            return CANNED_RESPONSE

        monkeypatch.setattr(tvs, "_http_post_json", fake_post)
        result = tvs.run_scan({}, "america", retry_base_delay=0)
        assert result["totalCount"] == 2
        assert calls["n"] == 3

    def test_gives_up_after_max_retries(self, monkeypatch):
        def fake_post(url, payload, timeout):
            raise tvs.TransientScanError("HTTP 503")

        monkeypatch.setattr(tvs, "_http_post_json", fake_post)
        with pytest.raises(tvs.ScanError):
            tvs.run_scan({}, "america", retry_base_delay=0)

    def test_market_in_url(self, monkeypatch):
        seen = {}

        def fake_post(url, payload, timeout):
            seen["url"] = url
            return CANNED_RESPONSE

        monkeypatch.setattr(tvs, "_http_post_json", fake_post)
        tvs.run_scan({}, "america", retry_base_delay=0)
        assert "scanner.tradingview.com/america/scan" in seen["url"]


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestMain:
    def test_dry_run_prints_payload(self, capsys):
        rc = tvs.main(["--filters", "pe<20,div_yield>3", "--dry-run"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        lefts = [f["left"] for f in payload["filter"]]
        assert "price_earnings_ttm" in lefts
        assert "dividends_yield_current" in lefts

    def test_invalid_filter_exits_1(self, capsys):
        rc = tvs.main(["--filters", "pe~20", "--dry-run"])
        assert rc == 1
        assert "Error" in capsys.readouterr().err

    def test_invalid_market_exits_1(self, capsys):
        rc = tvs.main(["--filters", "pe<20", "--market", "amer ica!", "--dry-run"])
        assert rc == 1

    def test_end_to_end_with_mock(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(tvs, "run_scan", lambda *a, **k: CANNED_RESPONSE)
        rc = tvs.main(
            [
                "--filters",
                "mkt_cap>1B",
                "--columns",
                "name,close,market_cap_basic",
                "--output-dir",
                str(tmp_path),
                "--screen-name",
                "e2e",
            ]
        )
        assert rc == 0
        files = list(tmp_path.iterdir())
        assert any(f.suffix == ".md" for f in files)
        assert any(f.suffix == ".json" for f in files)
        out = capsys.readouterr().out
        assert "NASDAQ:NVDA" in out

    def test_requires_some_criteria(self, capsys):
        rc = tvs.main(["--dry-run"])
        assert rc == 1
        assert "at least one" in capsys.readouterr().err.lower()

    def test_index_only_is_enough(self, capsys):
        rc = tvs.main(["--index", "sp500", "--dry-run"])
        assert rc == 0
