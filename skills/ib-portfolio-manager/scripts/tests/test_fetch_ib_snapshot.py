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
# Order normalization
# --------------------------------------------------------------------------- #
def test_normalize_order_limit_buy():
    raw = {
        "orderId": 123456,
        "ticker": "NVDA",
        "conid": 4815747,
        "side": "BUY",
        "orderType": "LMT",
        "status": "Submitted",
        "totalSize": 50,
        "filledQuantity": 0,
        "remainingQuantity": 50,
        "price": 120.5,
        "timeInForce": "GTC",
        "cashCcy": "USD",
        "orderDesc": "BUY 50 NVDA LMT 120.50 GTC",
    }
    o = fis.normalize_order(raw)
    assert o["order_id"] == "123456"
    assert o["symbol"] == "NVDA"
    assert o["conid"] == 4815747
    assert o["side"] == "BUY"
    assert o["order_type"] == "LMT"
    assert o["status"] == "Submitted"
    assert o["total_quantity"] == 50.0
    assert o["remaining_quantity"] == 50.0
    assert o["limit_price"] == 120.5
    assert o["stop_price"] is None
    assert o["tif"] == "GTC"
    assert o["currency"] == "USD"


def test_normalize_order_stop_sell_and_symbol_token():
    raw = {
        "orderId": 99,
        "ticker": "MSFT NASDAQ.NMS STK",
        "side": "sell",
        "orderType": "STP",
        "status": "PreSubmitted",
        "totalSize": "30",
        "auxPrice": 380.0,
    }
    o = fis.normalize_order(raw)
    assert o["symbol"] == "MSFT"
    assert o["side"] == "SELL"  # normalized to upper-case
    assert o["order_type"] == "STP"
    assert o["stop_price"] == 380.0
    assert o["total_quantity"] == 30.0  # numeric string coerced
    assert o["limit_price"] is None


def test_normalize_order_missing_fields():
    o = fis.normalize_order({})
    assert o["symbol"] == "?"
    assert o["side"] is None
    assert o["order_id"] is None
    assert o["status"] is None
    assert o["conid"] is None


def test_fetch_orders_parses_orders_envelope(monkeypatch):
    payload = {
        "orders": [
            {"orderId": 1, "ticker": "AAPL", "side": "BUY", "orderType": "LMT", "totalSize": 10}
        ],
        "snapshot": True,
    }
    monkeypatch.setattr(fis, "http_get_json", lambda port, path, timeout: payload)
    orders = fis.fetch_orders(5002, 1.0)
    assert len(orders) == 1
    assert orders[0]["symbol"] == "AAPL"
    assert orders[0]["side"] == "BUY"


def test_fetch_orders_accepts_bare_list(monkeypatch):
    monkeypatch.setattr(
        fis, "http_get_json", lambda port, path, timeout: [{"orderId": 2, "ticker": "TSLA"}]
    )
    orders = fis.fetch_orders(5002, 1.0)
    assert [o["symbol"] for o in orders] == ["TSLA"]


def test_fetch_orders_tolerates_non_collection(monkeypatch):
    monkeypatch.setattr(fis, "http_get_json", lambda port, path, timeout: None)
    assert fis.fetch_orders(5002, 1.0) == []


# --------------------------------------------------------------------------- #
# Trade (execution history) normalization
# --------------------------------------------------------------------------- #
def test_normalize_trade_buy():
    raw = {
        "execution_id": "0000e0d5.1",
        "symbol": "AAPL",
        "conid": 265598,
        "side": "B",
        "size": 100,
        "price": "165.00",
        "net_amount": 16500.0,
        "commission": "1.05",
        "exchange": "NASDAQ",
        "sec_type": "STK",
        "order_description": "Bot 100 AAPL @ 165.00",
    }
    t = fis.normalize_trade(raw)
    assert t["execution_id"] == "0000e0d5.1"
    assert t["symbol"] == "AAPL"
    assert t["conid"] == 265598
    assert t["side"] == "BUY"  # "B" -> BUY
    assert t["quantity"] == 100.0
    assert t["price"] == 165.0
    assert t["amount"] == 16500.0
    assert t["commission"] == 1.05
    assert t["exchange"] == "NASDAQ"


def test_normalize_trade_sell_with_epoch_time_and_symbol_token():
    raw = {
        "symbol": "MSFT NASDAQ.NMS STK",
        "side": "SLD",
        "size": "30",
        "price": 380.0,
        "trade_time_r": 1750000000000,  # epoch ms -> ISO string
    }
    t = fis.normalize_trade(raw)
    assert t["symbol"] == "MSFT"
    assert t["side"] == "SELL"  # "SLD" -> SELL
    assert t["quantity"] == 30.0
    # epoch was converted to an ISO-8601 timestamp (tz-independent assertions)
    assert isinstance(t["trade_time"], str)
    assert "T" in t["trade_time"]


def test_normalize_trade_raw_time_fallback_and_missing():
    t = fis.normalize_trade({"trade_time": "20260615-13:30:00"})
    assert t["symbol"] == "?"
    assert t["side"] is None
    assert t["trade_time"] == "20260615-13:30:00"  # raw string kept when no epoch


def test_fetch_trades_sorts_newest_first(monkeypatch):
    rows = [
        {"symbol": "OLD", "side": "B", "trade_time_r": 1000},
        {"symbol": "NEW", "side": "S", "trade_time_r": 2000},
    ]
    monkeypatch.setattr(fis, "http_get_json", lambda port, path, timeout: rows)
    trades = fis.fetch_trades(5002, 1.0)
    assert [t["symbol"] for t in trades] == ["NEW", "OLD"]


def test_fetch_trades_tolerates_non_collection(monkeypatch):
    monkeypatch.setattr(fis, "http_get_json", lambda port, path, timeout: {"unexpected": 1})
    assert fis.fetch_trades(5002, 1.0) == []


# --------------------------------------------------------------------------- #
# Error snapshot shape
# --------------------------------------------------------------------------- #
def test_error_snapshot_shape():
    snap = fis.error_snapshot("boom")
    assert snap["ok"] is False
    assert snap["error"] == "boom"
    assert snap["positions"] == []
    assert snap["orders"] == []
    assert snap["trades"] == []
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
    assert snap["orders"] == []  # defaulted when absent from the fixture
    assert snap["trades"] == []  # defaulted when absent from the fixture


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
