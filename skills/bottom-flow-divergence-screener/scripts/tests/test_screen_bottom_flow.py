#!/usr/bin/env python3
"""Pipeline / payload / CLI tests for the screener orchestration (offline)."""

import json

from scorer import SCAN_COLUMNS, ScoreConfig
from screen_bottom_flow import (
    ScreenConfig,
    build_payload,
    filter_and_rank,
    main,
    rows_from_response,
    run_from_fixture,
    run_from_rows,
)


def _cfg(**kw) -> ScreenConfig:
    return ScreenConfig(score_cfg=ScoreConfig(), **kw)


def test_build_payload_shape():
    payload = build_payload(_cfg(limit=250, max_perf_1y=-15.0))
    assert payload["columns"] == list(SCAN_COLUMNS)
    assert payload["range"] == [0, 250]
    assert payload["sort"] == {"sortBy": "Perf.Y", "sortOrder": "asc"}
    perf = [f for f in payload["filter"] if f["left"] == "Perf.Y"][0]
    assert perf == {"left": "Perf.Y", "operation": "less", "right": -15.0}
    assert payload["filter2"]["operator"] == "and"


def test_rows_from_response_maps_raw():
    raw = {"data": [{"s": "NASDAQ:FOO", "d": [10.0, 8.5]}]}
    rows = rows_from_response(raw, ["close", "price_52_week_low"])
    assert rows == [{"symbol": "NASDAQ:FOO", "close": 10.0, "price_52_week_low": 8.5}]


def test_run_from_rows_counts(fixture_rows):
    records, stats = run_from_rows(fixture_rows, _cfg())
    assert stats["scanned"] == 8
    assert stats["rejected_bottom"] == 3  # HIGH, SHALLOW, NULLP
    assert stats["no_divergence"] == 1  # DEAD
    grades = sorted(r["grade"] for r in records)
    assert grades == ["A", "A", "B-accum", "B-fund"]
    foo = next(r for r in records if r["symbol"] == "FOO")
    assert foo["sector"] == "Producer Manufacturing"
    bar = next(r for r in records if r["symbol"] == "BAR")
    assert bar["sector"] is None  # row without a sector key maps to None


def test_filter_and_rank_grade_selection(fixture_rows):
    records, _ = run_from_rows(fixture_rows, _cfg())
    only_a = filter_and_rank(records, _cfg(grades=("A",)))
    assert {r["grade"] for r in only_a} == {"A"}
    assert len(only_a) == 2


def test_filter_and_rank_require_turn(fixture_rows):
    records, _ = run_from_rows(fixture_rows, _cfg())
    turning = filter_and_rank(records, _cfg(require_turn=True))
    assert {r["symbol"] for r in turning} == {"FOO", "MNA"}  # BAR/BAZ are falling


def test_filter_and_rank_require_survivable(fixture_rows):
    records, _ = run_from_rows(fixture_rows, _cfg())
    survivable = filter_and_rank(records, _cfg(require_survivable=True))
    assert "BAR" not in {r["symbol"] for r in survivable}  # unprofitable + weak balance sheet
    assert len(survivable) == 3


def test_filter_and_rank_sorts_a_first_and_caps_top(fixture_rows):
    records, _ = run_from_rows(fixture_rows, _cfg())
    ranked = filter_and_rank(records, _cfg(top=1))
    assert len(ranked) == 1
    assert ranked[0]["grade"] == "A"


def test_run_from_fixture_end_to_end(fixture_path):
    records, meta = run_from_fixture(fixture_path, _cfg(), "2026-06-17")
    assert meta["scanned"] == 8
    assert meta["as_of"] == "2026-06-17"
    assert len(records) == 4


def test_main_writes_reports(tmp_path, fixture_path):
    rc = main(["--fixture", fixture_path, "--as-of", "2026-06-17", "--output-dir", str(tmp_path)])
    assert rc == 0
    mds = list(tmp_path.glob("*.md"))
    jsons = list(tmp_path.glob("*.json"))
    assert len(mds) == 1 and len(jsons) == 1
    data = json.loads(jsons[0].read_text())
    assert len(data["candidates"]) == 4
    assert data["candidates"][0]["grade"] == "A"


def test_main_dry_run(capsys):
    rc = main(["--dry-run", "--limit", "123"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["columns"] == list(SCAN_COLUMNS)
    assert payload["range"] == [0, 123]
