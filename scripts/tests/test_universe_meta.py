"""Tests for scripts/lib/universe_meta.py and the universe builder's sidecar."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import universe_meta as um  # noqa: E402


def test_load_meta_and_lookup_sector(tmp_path):
    path = tmp_path / "meta.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-24 22:00:00",
                "tickers": {"NVDA": {"sector": "Electronic Technology", "name": "NVIDIA"}},
            }
        )
    )
    meta = um.load_universe_meta(path)
    assert um.sector_for("nvda", meta) == "Electronic Technology"
    assert um.sector_for("ZZZZ", meta) is None


def test_missing_or_bad_meta_is_empty(tmp_path):
    assert um.load_universe_meta(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert um.load_universe_meta(bad) == {}


def test_builder_returns_sector_metadata(monkeypatch):
    import build_vcp_universe as b

    cols = ["name", "description", "close", "market_cap_basic", "sector", "exchange"]

    def fake_build_payload(*a, **k):
        return {"columns": cols}

    def fake_run_scan(payload, market, timeout=30):
        return {
            "totalCount": 2,
            "data": [
                {
                    "s": "NASDAQ:NVDA",
                    "d": ["NVDA", "NVIDIA", 224.0, 5e12, "Electronic Technology", "NASDAQ"],
                },
                {"s": "NYSE:JPM", "d": ["JPM", "JPMorgan", 300.0, 8e11, "Finance", "NYSE"]},
            ],
        }

    monkeypatch.setattr(b.tv, "build_payload", fake_build_payload)
    monkeypatch.setattr(b.tv, "run_scan", fake_run_scan)
    tickers, total, meta = b.build_universe(
        limit=10,
        exchanges=["NASDAQ", "NYSE"],
        min_price=10,
        min_avg_volume="1M",
        min_market_cap="1B",
    )
    assert tickers == ["NVDA", "JPM"]
    assert meta["NVDA"]["sector"] == "Electronic Technology"
    assert meta["JPM"]["name"] == "JPMorgan"
    assert meta["JPM"]["market_cap"] == 8e11


def test_maps_for_universe_fill_sector_name_and_cap():
    meta = {"NVDA": {"sector": "Electronic Technology", "name": "NVIDIA", "market_cap": 5e12}}
    sectors, names, caps = um.maps_for_universe(["NVDA", "ZZZZ"], meta)
    assert sectors == {"NVDA": "Electronic Technology"}
    assert names == {"NVDA": "NVIDIA"}
    assert caps == {"NVDA": 5e12}
