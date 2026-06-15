#!/usr/bin/env python3
"""Fetch a live, read-only Interactive Brokers account + positions snapshot as JSON.

This is the data source behind the trading dashboard's "IB" tab. Unlike the MCP
layer (which only Claude can drive), this script talks to the bundled IB Gateway
Client Portal API directly over its local HTTPS port and prints a normalized JSON
snapshot to stdout, so the Express server can simply shell out to it.

It is strictly read-only: it issues only ``GET`` requests against
``/portfolio/*`` (account + positions), ``/iserver/account/orders`` (live
orders) and ``/iserver/account/trades`` (recent execution history) endpoints.
No orders are placed regardless of ``IB_READ_ONLY_MODE``.

Connection discovery mirrors ``check_ib_connection.py``: locate
``ib-gateway/.runtime/gateway-session.json`` (written by the MCP server when the
Gateway starts) and read the port the Gateway is listening on.

Output (stdout) is always a single JSON object with this shape::

    {
      "ok": true | false,
      "generated_at": "<iso8601>",
      "mode": "paper" | "live",
      "account_id": "U1234567" | null,
      "account_ids": ["U1234567"],
      "summary": { ...IbAccountSummary } | null,
      "positions": [ { ...IbPosition }, ... ],
      "orders": [ { ...IbOrder }, ... ],
      "trades": [ { ...IbTrade }, ... ],
      "error": "<reason>" | null,
      "source": "live" | "fixture"
    }

Exit code is 0 on a successful snapshot and 2 on a structured error (Gateway not
running / not authenticated / network failure). The JSON is printed in both
cases so the caller can render the reason gracefully.

Usage:
    python3 fetch_ib_snapshot.py [--runtime-dir PATH] [--timeout SECONDS]
    python3 fetch_ib_snapshot.py --fixture path/to/snapshot.json

Environment Variables:
    IB_PAPER_TRADING        'true' for paper (default), 'false' for live
    IB_GATEWAY_RUNTIME_DIR  Override the ib-gateway/.runtime directory location
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse the Gateway-session discovery + config helpers from the preflight check.
# Python puts this script's own directory on sys.path[0], so the sibling module
# resolves whether invoked from the repo root or from scripts/.
import check_ib_connection as cic

DEFAULT_TIMEOUT = 20.0
MAX_POSITION_PAGES = 10  # Client Portal returns positions in pages of ~30.


# --------------------------------------------------------------------------- #
# Low-level HTTP (self-signed localhost Gateway; verification intentionally off)
# --------------------------------------------------------------------------- #
def http_get_json(port: int, api_path: str, timeout: float) -> Any:
    """GET ``https://localhost:<port>/v1/api<api_path>`` and parse JSON.

    Raises on any network/HTTP/parse error so the caller can convert it into a
    structured snapshot error. Uses ``requests`` when available, falling back to
    the standard library so the script works in a bare environment.
    """
    url = f"https://localhost:{port}/v1/api{api_path}"
    try:
        import requests  # type: ignore

        try:
            import urllib3  # type: ignore

            urllib3.disable_warnings()
        except Exception:  # pragma: no cover - cosmetic only
            pass

        resp = requests.get(url, verify=False, timeout=timeout)  # noqa: S501
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        return _http_get_json_urllib(url, timeout)


def _http_get_json_urllib(url: str, timeout: float) -> Any:
    """Fallback GET using only the standard library (no requests installed)."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Normalization: IB Client Portal shapes -> stable canonical snapshot fields
# --------------------------------------------------------------------------- #
def _num(value: Any) -> float | None:
    """Coerce a number-ish value (number, numeric string, or {amount: n}) to float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        # Client Portal summary values look like {"amount": 1234.5, "currency": "USD"}.
        if value.get("isNull"):
            return None
        return _num(value.get("amount"))
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _get_ci(data: dict, *keys: str) -> Any:
    """Case-insensitive lookup returning the first present key's value.

    Also tolerates the segment suffixes IB appends to summary keys (e.g.
    ``netliquidation-s``) by matching on the base key prefix.
    """
    lower = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        k = key.lower()
        if k in lower:
            return lower[k]
    # Suffix-tolerant fallback: "netliquidation" matches "netliquidation-s".
    for key in keys:
        k = key.lower()
        for lk, lv in lower.items():
            if lk == k or lk.startswith(k + "-"):
                return lv
    return None


def normalize_summary(account_id: str | None, raw: dict | None) -> dict:
    """Map a Client Portal ``/portfolio/{id}/summary`` payload to IbAccountSummary."""
    raw = raw or {}
    currency = _get_ci(raw, "currency")
    if isinstance(currency, dict):
        currency = currency.get("currency")
    return {
        "account_id": account_id,
        "net_liquidation": _num(_get_ci(raw, "netliquidation", "netliquidationvalue")),
        "total_cash": _num(_get_ci(raw, "totalcashvalue", "totalcash")),
        "available_funds": _num(_get_ci(raw, "availablefunds")),
        "buying_power": _num(_get_ci(raw, "buyingpower")),
        "gross_position_value": _num(_get_ci(raw, "grosspositionvalue")),
        "unrealized_pnl": _num(_get_ci(raw, "unrealizedpnl")),
        "realized_pnl": _num(_get_ci(raw, "realizedpnl")),
        "excess_liquidity": _num(_get_ci(raw, "excessliquidity")),
        "equity_with_loan": _num(_get_ci(raw, "equitywithloanvalue", "equitywithloan")),
        "currency": currency if isinstance(currency, str) else None,
    }


def _position_symbol(raw: dict) -> str:
    """Best-effort ticker for a position row across Client Portal field variants."""
    for key in ("ticker", "contractDesc", "name", "symbol"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            # contractDesc can be "AAPL" or "AAPL NASDAQ.NMS STK" -> take first token.
            return val.strip().split()[0]
    return "?"


def normalize_position(raw: dict) -> dict:
    """Map one Client Portal position row to an IbPosition."""
    position = _num(raw.get("position"))
    avg_cost = _num(raw.get("avgCost"))
    if avg_cost is None:
        avg_cost = _num(raw.get("avgPrice"))
    market_price = _num(raw.get("mktPrice"))
    market_value = _num(raw.get("mktValue"))
    unrealized = _num(raw.get("unrealizedPnl"))

    # Cost basis = avgCost * |qty| (avgCost is per-share). Derive a % when possible.
    unrealized_pct: float | None = None
    if unrealized is not None and avg_cost not in (None, 0) and position not in (None, 0):
        basis = abs(avg_cost * position)
        if basis:
            unrealized_pct = unrealized / basis * 100.0

    side = None
    if position is not None:
        side = "short" if position < 0 else "long"

    conid = raw.get("conid")
    return {
        "symbol": _position_symbol(raw),
        "conid": int(conid) if isinstance(conid, (int, float)) else None,
        "position": position,
        "side": side,
        "avg_cost": avg_cost,
        "market_price": market_price,
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "unrealized_pnl_pct": unrealized_pct,
        "realized_pnl": _num(raw.get("realizedPnl")),
        "currency": raw.get("currency") if isinstance(raw.get("currency"), str) else None,
        "asset_class": raw.get("assetClass") if isinstance(raw.get("assetClass"), str) else None,
        "sector": raw.get("sector") if isinstance(raw.get("sector"), str) else None,
    }


def _order_symbol(raw: dict) -> str:
    """Best-effort ticker for an order row across Client Portal field variants."""
    for key in ("ticker", "symbol", "contractDesc"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            # "MSFT NASDAQ.NMS STK" -> "MSFT".
            return val.strip().split()[0]
    return "?"


def _str_field(raw: dict, *keys: str) -> str | None:
    """First non-empty string among ``keys`` (in order), else None."""
    for key in keys:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def normalize_order(raw: dict) -> dict:
    """Map one Client Portal ``/iserver/account/orders`` row to an IbOrder."""
    side = _str_field(raw, "side")
    order_id = raw.get("orderId")
    if order_id is None:
        order_id = raw.get("order_id")
    conid = raw.get("conid")
    # Limit price lives under "price" (or "limitPrice"); stop under "auxPrice".
    limit_price = _num(raw.get("price"))
    if limit_price is None:
        limit_price = _num(raw.get("limitPrice"))
    stop_price = _num(raw.get("auxPrice"))
    if stop_price is None:
        stop_price = _num(raw.get("stopPrice"))
    return {
        "order_id": str(order_id) if order_id is not None else None,
        "symbol": _order_symbol(raw),
        "conid": int(conid) if isinstance(conid, (int, float)) else None,
        "side": side.upper() if side else None,
        "order_type": _str_field(raw, "orderType", "origOrderType"),
        "status": _str_field(raw, "status", "order_ccp_status"),
        "total_quantity": _num(raw.get("totalSize")),
        "filled_quantity": _num(raw.get("filledQuantity")),
        "remaining_quantity": _num(raw.get("remainingQuantity")),
        "limit_price": limit_price,
        "stop_price": stop_price,
        "tif": _str_field(raw, "timeInForce", "tif"),
        "currency": _str_field(raw, "cashCcy", "currency"),
        "last_execution_time": _str_field(raw, "lastExecutionTime"),
        "order_desc": _str_field(raw, "orderDesc"),
    }


def _trade_symbol(raw: dict) -> str:
    """Best-effort ticker for a trade row across Client Portal field variants."""
    for key in ("symbol", "contract_description_1", "ticker"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().split()[0]
    return "?"


def _trade_time(raw: dict) -> str | None:
    """Prefer the epoch-ms ``trade_time_r`` as an ISO-8601 string; else the raw string."""
    epoch_ms = raw.get("trade_time_r")
    if isinstance(epoch_ms, (int, float)) and not isinstance(epoch_ms, bool) and epoch_ms > 0:
        try:
            return (
                datetime.fromtimestamp(epoch_ms / 1000.0, timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds")
            )
        except (OverflowError, OSError, ValueError):
            pass
    return _str_field(raw, "trade_time")


def normalize_trade(raw: dict) -> dict:
    """Map one Client Portal ``/iserver/account/trades`` row to an IbTrade.

    ``side`` is IB's ``B``/``S`` (or ``BOT``/``SLD``); normalize to ``BUY``/``SELL``.
    """
    side = _str_field(raw, "side")
    norm_side: str | None = None
    if side:
        upper = side.upper()
        if upper in ("B", "BOT", "BUY"):
            norm_side = "BUY"
        elif upper in ("S", "SLD", "SELL"):
            norm_side = "SELL"
        else:
            norm_side = upper
    conid = raw.get("conid")
    amount = _num(raw.get("net_amount"))
    if amount is None:
        amount = _num(raw.get("amount"))
    return {
        "execution_id": _str_field(raw, "execution_id", "executionId"),
        "symbol": _trade_symbol(raw),
        "conid": int(conid) if isinstance(conid, (int, float)) else None,
        "side": norm_side,
        "quantity": _num(raw.get("size")),
        "price": _num(raw.get("price")),
        "amount": amount,
        "commission": _num(raw.get("commission")),
        "exchange": _str_field(raw, "exchange"),
        "sec_type": _str_field(raw, "sec_type", "secType"),
        "trade_time": _trade_time(raw),
        "order_desc": _str_field(raw, "order_description", "orderDesc"),
    }


# --------------------------------------------------------------------------- #
# Snapshot assembly
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _mode() -> str:
    return "paper" if cic.bool_env("IB_PAPER_TRADING", default=True) else "live"


def error_snapshot(message: str, source: str = "live") -> dict:
    return {
        "ok": False,
        "generated_at": _now_iso(),
        "mode": _mode(),
        "account_id": None,
        "account_ids": [],
        "summary": None,
        "positions": [],
        "orders": [],
        "trades": [],
        "error": message,
        "source": source,
    }


def fetch_positions(port: int, account_id: str, timeout: float) -> list[dict]:
    """Fetch all position pages for an account and normalize them."""
    positions: list[dict] = []
    for page in range(MAX_POSITION_PAGES):
        rows = http_get_json(port, f"/portfolio/{account_id}/positions/{page}", timeout)
        if not isinstance(rows, list) or not rows:
            # IB sometimes returns [] on the very first call while it warms its
            # cache; retry page 0 once before giving up on it.
            if page == 0 and rows == []:
                rows = http_get_json(port, f"/portfolio/{account_id}/positions/0", timeout)
            if not isinstance(rows, list) or not rows:
                break
        positions.extend(normalize_position(r) for r in rows if isinstance(r, dict))
        if len(rows) < 30:
            break
    return positions


def fetch_orders(port: int, timeout: float) -> list[dict]:
    """Fetch live/working orders and normalize them.

    The Client Portal ``/iserver/account/orders`` endpoint returns an envelope
    ``{"orders": [...], "snapshot": true}``; some Gateway builds return a bare
    list. Tolerate both and ignore anything else.
    """
    payload = http_get_json(port, "/iserver/account/orders", timeout)
    rows: list = []
    if isinstance(payload, dict):
        maybe = payload.get("orders")
        if isinstance(maybe, list):
            rows = maybe
    elif isinstance(payload, list):
        rows = payload
    return [normalize_order(r) for r in rows if isinstance(r, dict)]


def _trade_sort_key(raw: dict) -> float:
    """Sortable epoch-ms for a raw trade row (0 when absent), for newest-first order."""
    epoch = raw.get("trade_time_r")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        return float(epoch)
    return 0.0


def fetch_trades(port: int, timeout: float) -> list[dict]:
    """Fetch recent executions (current day + ~6 prior), newest first.

    ``/iserver/account/trades`` normally returns a bare list; tolerate a
    ``{"trades": [...]}`` envelope and ignore anything else.
    """
    payload = http_get_json(port, "/iserver/account/trades", timeout)
    rows: list = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("trades"), list):
        rows = payload["trades"]
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=_trade_sort_key, reverse=True)
    return [normalize_trade(r) for r in rows]


def build_snapshot(port: int, timeout: float) -> dict:
    """Hit the read-only Client Portal endpoints and assemble the snapshot."""
    accounts = http_get_json(port, "/portfolio/accounts", timeout)
    account_ids: list[str] = []
    if isinstance(accounts, list):
        for acc in accounts:
            if isinstance(acc, dict):
                acc_id = acc.get("id") or acc.get("accountId")
                if acc_id:
                    account_ids.append(str(acc_id))
    if not account_ids:
        return error_snapshot("No portfolio accounts returned by the Gateway.")

    primary = account_ids[0]
    raw_summary = http_get_json(port, f"/portfolio/{primary}/summary", timeout)
    summary = normalize_summary(primary, raw_summary if isinstance(raw_summary, dict) else None)
    positions = fetch_positions(port, primary, timeout)

    # Orders and trades are best-effort add-ons: never fail the whole snapshot
    # (account + positions are the primary payload) if either endpoint hiccups.
    try:
        orders = fetch_orders(port, timeout)
    except Exception:  # noqa: BLE001 - degrade gracefully, keep account/positions
        orders = []
    try:
        trades = fetch_trades(port, timeout)
    except Exception:  # noqa: BLE001 - degrade gracefully, keep account/positions
        trades = []

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "mode": _mode(),
        "account_id": primary,
        "account_ids": account_ids,
        "summary": summary,
        "positions": positions,
        "orders": orders,
        "trades": trades,
        "error": None,
        "source": "live",
    }


def fetch_live_snapshot(runtime_dir: str | None, timeout: float) -> dict:
    """Discover the Gateway session, verify auth, and build the snapshot."""
    dirs = cic.candidate_runtime_dirs(runtime_dir)
    session_path = cic.find_session_file(dirs)
    if session_path is None:
        searched = ", ".join(str(d) for d in dirs)
        return error_snapshot(
            "IB Gateway session file not found. Start a Claude session with the "
            f"interactive-brokers MCP configured (searched: {searched})."
        )
    try:
        session = cic.load_session(session_path)
    except (OSError, json.JSONDecodeError) as exc:
        return error_snapshot(f"Could not read Gateway session file: {exc}")

    port = session.get("port")
    if not isinstance(port, int):
        return error_snapshot("Gateway session file has no usable 'port'.")

    authenticated, detail = cic.probe_auth(port, timeout=timeout)
    if not authenticated:
        return error_snapshot(
            "IB Gateway is running but the session is not authenticated. Complete "
            f"the browser login / 2FA, then retry. ({detail})"
        )

    try:
        return build_snapshot(port, timeout)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on any API error
        return error_snapshot(f"Failed to fetch IB snapshot: {exc}")


def load_fixture(path: str) -> dict:
    """Load a pre-recorded snapshot JSON (offline / UI development)."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        snap = error_snapshot(f"Could not read fixture: {exc}", source="fixture")
        return snap
    if not isinstance(data, dict):
        return error_snapshot("Fixture is not a JSON object.", source="fixture")
    data.setdefault("ok", True)
    data.setdefault("source", "fixture")
    data.setdefault("generated_at", _now_iso())
    data.setdefault("error", None)
    data.setdefault("positions", [])
    data.setdefault("orders", [])
    data.setdefault("trades", [])
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a read-only Interactive Brokers account/positions snapshot as JSON.",
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        help="Override the ib-gateway/.runtime directory to search for the session file.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="Read a pre-recorded snapshot JSON file instead of contacting the Gateway.",
    )
    args = parser.parse_args(argv)

    if args.fixture:
        snapshot = load_fixture(args.fixture)
    else:
        snapshot = fetch_live_snapshot(args.runtime_dir, args.timeout)

    json.dump(snapshot, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if snapshot.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
