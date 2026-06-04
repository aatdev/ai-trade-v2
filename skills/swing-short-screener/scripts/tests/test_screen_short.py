#!/usr/bin/env python3
"""End-to-end and filter tests for the screener orchestration (offline)."""

import json

from screen_short import (
    analyze_symbol,
    filter_and_rank,
    main,
    parse_arguments,
    passes_short_filter,
    run_from_fixture,
)
from weakness_metrics import compute_metrics

from conftest import downtrend_closes, make_series, uptrend_closes


def test_filter_rejects_above_ma200(uptrend_bars):
    m = compute_metrics(uptrend_bars)
    passed, reason = passes_short_filter(m)
    assert passed is False
    assert reason == "above_ma200_not_stage4"


def test_filter_rejects_illiquid(downtrend_bars):
    m = compute_metrics(downtrend_bars)
    passed, reason = passes_short_filter(m, min_dollar_vol=10**12)
    assert passed is False
    assert reason == "illiquid_squeeze_risk"


def test_filter_rejects_insufficient_history():
    passed, reason = passes_short_filter(None)
    assert passed is False
    assert reason == "insufficient_history"


def test_analyze_downtrend_produces_record(downtrend_bars):
    record, reason = analyze_symbol(downtrend_bars, spy_return=0.0, name="WEAK", sector="Tech")
    assert record is not None
    assert reason == ""
    assert record["grade"] in ("A", "B", "C")
    assert record["metrics"]["below_ma200"] is True


def test_filter_and_rank_orders_and_drops_d():
    results = [
        {"grade": "A", "composite_score": 88},
        {"grade": "C", "composite_score": 55},
        {"grade": "D", "composite_score": 40},
        {"grade": "B", "composite_score": 70},
    ]
    ranked = filter_and_rank(results, min_grade="C", top=10)
    assert [r["composite_score"] for r in ranked] == [88, 70, 55]  # D dropped, sorted desc


def _build_fixture(tmp_path):
    fixture = {
        "index": make_series(uptrend_closes(260, 195.0, 200.0)),
        "symbols": {
            "WEAK": {
                "name": "Weak Co",
                "sector": "Technology",
                "bars": make_series(
                    downtrend_closes(260, 220.0, 140.0), base_volume=3_000_000, last_vol_mult=2.5
                ),
            },
            "STRONG": {
                "name": "Strong Co",
                "sector": "Technology",
                "bars": make_series(uptrend_closes(260, 100.0, 220.0), base_volume=3_000_000),
            },
        },
    }
    path = tmp_path / "fix.json"
    path.write_text(json.dumps(fixture))
    return str(path)


def test_run_from_fixture_filters_strong_out(tmp_path):
    fixture_path = _build_fixture(tmp_path)
    args = parse_arguments(
        [
            "--fixture",
            fixture_path,
            "--rs-lookback",
            "63",
            "--min-price",
            "5",
            "--min-dollar-vol",
            "1000000",
        ]
    )
    results, meta = run_from_fixture(fixture_path, 63, args)
    symbols = {r["symbol"] for r in results}
    assert "WEAK" in symbols  # Stage 4 passes
    assert "STRONG" not in symbols  # above MA200 invalidated
    assert meta["source"] == "fixture"


def test_main_writes_reports(tmp_path):
    fixture_path = _build_fixture(tmp_path)
    out_dir = tmp_path / "reports"
    rc = main(
        [
            "--fixture",
            fixture_path,
            "--output-dir",
            str(out_dir),
            "--as-of",
            "2026-04-30",
            "--min-dollar-vol",
            "1000000",
        ]
    )
    assert rc == 0
    json_file = out_dir / "swing_short_screener_2026-04-30.json"
    md_file = out_dir / "swing_short_screener_2026-04-30.md"
    assert json_file.exists()
    assert md_file.exists()
    payload = json.loads(json_file.read_text())
    assert "candidates" in payload and "meta" in payload
    assert any(c["symbol"] == "WEAK" for c in payload["candidates"])
