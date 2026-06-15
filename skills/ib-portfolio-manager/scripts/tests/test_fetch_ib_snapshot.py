"""Unit tests for fetch_ib_snapshot.py (no network; normalization + fixtures)."""

import json
from pathlib import Path

import fetch_ib_snapshot as fis


# --------------------------------------------------------------------------- #
# Numeric coercion
# --------------------------------------------------------------------------- #
def test_num_handles_number_string_and_amount_dict():
    assert fis._num(12.5) == 12.5
    assert fis._num(7) == 7.0
    assert fis._num("1,234.5") == 1234.5
    assert fis._num({"amount": 99.0, "currency": "USD"}) == 99.0


def test_num_handles_null_and_bad_values():
    assert fis._num(None) is None
    assert fis._num(True) is None  # bool is not a number here
    assert fis._num({"isNull": True, "amount": 5}) is None
    assert fis._num("not-a-number") is None


# --------------------------------------------------------------------------- #
# Case-insensitive / suffix-tolerant lookup
# --------------------------------------------------------------------------- #
def test_get_ci_exact_and_suffix():
    data = {"NetLiquidation-S": {"amount": 100}, "buyingpower": {"amount": 50}}
    assert fis._get_ci(data, "netliquidation") == {"amount": 100}
    assert fis._get_ci(data, "buyingpower") == {"amount": 50}
    assert fis._get_ci(data, "missing") is None


# --------------------------------------------------------------------------- #
# Summary normalization
# --------------------------------------------------------------------------- #
def test_normalize_summary_maps_client_portal_shape():
    raw = {
        "netliquidation": {"amount": 152340.55, "currency": "USD"},
        "totalcashvalue": {"amount": 42000.0},
        "availablefunds": {"amount": 38000.0},
        "buyingpower": {"amount": 76000.0},
        "grosspositionvalue": {"amount": 110340.55},
        "unrealizedpnl": {"amount": 1200.0},
        "excessliquidity": {"amount": 37000.0},
    }
    summary = fis.normalize_summary("U123", raw)
    assert summary["account_id"] == "U123"
    assert summary["net_liquidation"] == 152340.55
    assert summary["total_cash"] == 42000.0
    assert summary["buying_power"] == 76000.0
    assert summary["gross_position_value"] == 110340.55
    assert summary["unrealized_pnl"] == 1200.0


def test_normalize_summary_tolerates_empty():
    summary = fis.normalize_summary(None, None)
    assert summary["account_id"] is None
    assert summary["net_liquidation"] is None


# --------------------------------------------------------------------------- #
# Position normalization
# --------------------------------------------------------------------------- #
def test_normalize_position_long_with_pct():
    raw = {
        "conid": 265598,
        "contractDesc": "AAPL",
        "position": 100,
        "avgCost": 150.0,
        "mktPrice": 165.0,
        "mktValue": 16500.0,
        "unrealizedPnl": 1500.0,
        "currency": "USD",
        "assetClass": "STK",
        "sector": "Technology",
    }
    p = fis.normalize_position(raw)
    assert p["symbol"] == "AAPL"
    assert p["conid"] == 265598
    assert p["side"] == "long"
    assert p["position"] == 100.0
    assert p["market_value"] == 16500.0
    # 1500 / (150 * 100) * 100 = 10%
    assert round(p["unrealized_pnl_pct"], 4) == 10.0


def test_normalize_position_short_side_and_symbol_token():
    raw = {
        "contractDesc": "TSLA NASDAQ.NMS STK",
        "position": -50,
        "avgCost": 200.0,
        "unrealizedPnl": 500.0,
    }
    p = fis.normalize_position(raw)
    assert p["symbol"] == "TSLA"
    assert p["side"] == "short"
    # basis uses abs(avgCost * position): 500 / (200*50) * 100 = 5%
    assert round(p["unrealized_pnl_pct"], 4) == 5.0


def test_normalize_position_missing_fields():
    p = fis.normalize_position({"position": None})
    assert p["symbol"] == "?"
    assert p["side"] is None
    assert p["unrealized_pnl_pct"] is None


# --------------------------------------------------------------------------- #
# Error snapshot shape
# --------------------------------------------------------------------------- #
def test_error_snapshot_shape():
    snap = fis.error_snapshot("boom")
    assert snap["ok"] is False
    assert snap["error"] == "boom"
    assert snap["positions"] == []
    assert snap["summary"] is None
    assert snap["source"] == "live"
    assert snap["mode"] in ("paper", "live")


# --------------------------------------------------------------------------- #
# Fixture loading
# --------------------------------------------------------------------------- #
def test_load_fixture_happy(tmp_path: Path):
    fixture = tmp_path / "snap.json"
    fixture.write_text(
        json.dumps({"account_id": "U999", "positions": [{"symbol": "MSFT"}]}),
        encoding="utf-8",
    )
    snap = fis.load_fixture(str(fixture))
    assert snap["ok"] is True
    assert snap["source"] == "fixture"
    assert snap["account_id"] == "U999"
    assert snap["positions"][0]["symbol"] == "MSFT"


def test_load_fixture_missing_file_is_structured_error(tmp_path: Path):
    snap = fis.load_fixture(str(tmp_path / "nope.json"))
    assert snap["ok"] is False
    assert snap["source"] == "fixture"
    assert "Could not read fixture" in snap["error"]


# --------------------------------------------------------------------------- #
# Live path error handling (no network)
# --------------------------------------------------------------------------- #
def test_fetch_live_snapshot_no_session(monkeypatch):
    monkeypatch.setattr(fis.cic, "find_session_file", lambda dirs: None)
    snap = fis.fetch_live_snapshot(runtime_dir="/nonexistent", timeout=1.0)
    assert snap["ok"] is False
    assert "session file not found" in snap["error"]


def test_fetch_live_snapshot_session_without_port(monkeypatch, tmp_path):
    monkeypatch.setattr(fis.cic, "find_session_file", lambda dirs: tmp_path / "s.json")
    monkeypatch.setattr(fis.cic, "load_session", lambda path: {"pid": 1})
    snap = fis.fetch_live_snapshot(runtime_dir=None, timeout=1.0)
    assert snap["ok"] is False
    assert "no usable 'port'" in snap["error"]


def test_fetch_live_snapshot_not_authenticated(monkeypatch, tmp_path):
    monkeypatch.setattr(fis.cic, "find_session_file", lambda dirs: tmp_path / "s.json")
    monkeypatch.setattr(fis.cic, "load_session", lambda path: {"port": 5002})
    monkeypatch.setattr(fis.cic, "probe_auth", lambda port, timeout=5.0: (False, "down"))
    snap = fis.fetch_live_snapshot(runtime_dir=None, timeout=1.0)
    assert snap["ok"] is False
    assert "not authenticated" in snap["error"]


# --------------------------------------------------------------------------- #
# main() CLI
# --------------------------------------------------------------------------- #
def test_main_fixture_prints_json_and_returns_zero(tmp_path, capsys):
    fixture = tmp_path / "snap.json"
    fixture.write_text(json.dumps({"account_id": "U1", "positions": []}), encoding="utf-8")
    rc = fis.main(["--fixture", str(fixture)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["account_id"] == "U1"
    assert out["ok"] is True


def test_main_missing_fixture_returns_two(tmp_path, capsys):
    rc = fis.main(["--fixture", str(tmp_path / "nope.json")])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
